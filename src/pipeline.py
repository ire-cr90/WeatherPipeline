"""Global Weather Analytics & Alerting Pipeline."""

import asyncio
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / "data" / "cities_raw.csv"
LOG_FILE = PROJECT_ROOT / "pipeline.log"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"

INVALID_NAME_CHARS = re.compile(r"[^\w\s'.-]|[\d_]")
REPEATED_SPACES = re.compile(r"\s+")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 10.0
BACKOFF_SECONDS = 0.5
MAX_CONCURRENT_REQUESTS = 10

logger = logging.getLogger("weather_pipeline")


@dataclass(frozen=True)
class Settings:
    """Runtime configuration read from environment variables."""

    weather_unit: str = "celsius"
    max_retries: int = 3
    log_level: str = "INFO"
    alert_threshold_c: float = 30.0


def load_settings(env_file: Path | None = None) -> Settings:
    """Load the `.env` file (if present) and build the pipeline settings.

    Missing variables fall back to the defaults declared in `Settings`, so the
    pipeline and the test suite also work without a `.env` file.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env")
    defaults = Settings()
    return Settings(
        weather_unit=os.getenv("WEATHER_UNIT", defaults.weather_unit).strip().lower(),
        max_retries=int(os.getenv("MAX_RETRIES", str(defaults.max_retries))),
        log_level=os.getenv("LOG_LEVEL", defaults.log_level).strip().upper(),
        alert_threshold_c=float(os.getenv("ALERT_THRESHOLD_C", str(defaults.alert_threshold_c))),
    )


def configure_logging(level: str = "INFO", log_file: Path = LOG_FILE) -> None:
    """Send log records to `log_file` and to the console using the shared format.

    Unknown level names fall back to `INFO`. The per-request logs of `httpx`
    are limited to warnings so the pipeline's own messages stay readable.
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format=LOG_FORMAT,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    logging.getLogger("httpx").setLevel(max(numeric_level, logging.WARNING))


def _capitalize_word(word: str) -> str:
    """Capitalize one word, handling hyphenated and apostrophe-prefixed names."""
    if "-" in word:
        return "-".join(_capitalize_word(part) for part in word.split("-"))
    prefix, apostrophe, rest = word.partition("'")
    if apostrophe and len(prefix) == 1 and rest:
        return f"{prefix.upper()}'{rest[:1].upper()}{rest[1:].lower()}"
    return word[:1].upper() + word[1:].lower()


def normalize_city_name(raw: str) -> str:
    """Clean a dirty city name while keeping valid (accented) letters.

    Steps: strip, replace underscores with spaces, drop every character that is
    not a letter, space, hyphen, apostrophe or dot, collapse repeated spaces and
    title-case each word without breaking names such as `O'Higgins`.
    """
    text = raw.strip().replace("_", " ")
    text = INVALID_NAME_CHARS.sub("", text)
    text = REPEATED_SPACES.sub(" ", text).strip(" -'.")
    return " ".join(_capitalize_word(word) for word in text.split(" ") if word)


def load_cities(path: Path | str = DATA_FILE) -> pd.DataFrame:
    """Read the raw cities CSV, normalize names and drop invalid rows.

    The returned DataFrame has the columns `raw_city`, `city`, `latitude` and
    `longitude`. Rows with an empty name, non-numeric coordinates, a latitude
    outside [-90, 90] or a longitude outside [-180, 180] are logged and skipped.
    """
    logger.info("Loading cities from %s", path)
    raw = pd.read_csv(path, dtype={"city": str}, keep_default_na=False, encoding="utf-8")
    missing_columns = {"city", "latitude", "longitude"} - set(raw.columns)
    if missing_columns:
        raise ValueError(f"Missing required columns: {sorted(missing_columns)}")

    cities = pd.DataFrame(
        {
            "raw_city": raw["city"],
            "city": raw["city"].map(normalize_city_name),
            "latitude": pd.to_numeric(raw["latitude"], errors="coerce"),
            "longitude": pd.to_numeric(raw["longitude"], errors="coerce"),
        }
    )

    valid = (
        (cities["city"] != "")
        & cities["latitude"].between(-90, 90)
        & cities["longitude"].between(-180, 180)
    )
    for _, row in cities[~valid].iterrows():
        logger.error(
            "Skipping invalid row: raw_city=%r latitude=%s longitude=%s",
            row["raw_city"],
            row["latitude"],
            row["longitude"],
        )

    cities = cities[valid].reset_index(drop=True)
    logger.info("Loaded %d valid cities (%d skipped)", len(cities), int((~valid).sum()))
    return cities


class RetryableError(Exception):
    """Raised when an API response is a transient failure worth retrying."""


def build_query_params(city_row: Mapping[str, Any], weather_unit: str = "celsius") -> dict:
    """Build the Open-Meteo query parameters for one city."""
    params: dict[str, Any] = {
        "latitude": city_row["latitude"],
        "longitude": city_row["longitude"],
        "hourly": "temperature_2m,precipitation",
        "timezone": "auto",
    }
    if weather_unit == "fahrenheit":
        params["temperature_unit"] = "fahrenheit"
    return params


def _parse_response(response: httpx.Response, city: str) -> dict | None:
    """Return the JSON payload, raise `RetryableError` on 5xx, or `None` on other errors."""
    if response.status_code >= 500:
        raise RetryableError(f"server error {response.status_code}")
    if response.status_code >= 400:
        logger.error("Request for %s failed with status %d", city, response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        logger.error("Response for %s is not valid JSON", city)
        return None


def _backoff_delay(attempt: int, backoff_seconds: float) -> float:
    """Exponential backoff delay before the next attempt."""
    return backoff_seconds * 2 ** (attempt - 1)


def fetch_weather(
    client: httpx.Client,
    city_row: Mapping[str, Any],
    max_retries: int = 3,
    weather_unit: str = "celsius",
    backoff_seconds: float = BACKOFF_SECONDS,
) -> dict | None:
    """Fetch the hourly forecast for one city, retrying transient failures.

    Timeouts, connection errors and 5xx responses are retried up to
    `max_retries` times after the first attempt. Every failed attempt is logged
    as `ERROR`; `None` is returned when the city cannot be fetched.
    """
    city = city_row["city"]
    params = build_query_params(city_row, weather_unit)
    total_attempts = max(0, max_retries) + 1
    for attempt in range(1, total_attempts + 1):
        try:
            response = client.get(OPEN_METEO_URL, params=params)
            return _parse_response(response, city)
        except (httpx.TransportError, RetryableError) as exc:
            logger.error(
                "Attempt %d/%d for %s failed: %s: %s",
                attempt,
                total_attempts,
                city,
                type(exc).__name__,
                exc,
            )
        if attempt < total_attempts:
            time.sleep(_backoff_delay(attempt, backoff_seconds))
    logger.error("Giving up on %s after %d attempts", city, total_attempts)
    return None


def extract_all(cities_df: pd.DataFrame, settings: Settings | None = None) -> dict[str, dict]:
    """Fetch every city sequentially, one request at a time.

    Returns a mapping from clean city name to its Open-Meteo JSON payload.
    Cities that fail are logged and left out. The total time is logged as `INFO`.
    """
    settings = settings or Settings()
    results: dict[str, dict] = {}
    start = time.perf_counter()
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for city_row in cities_df.to_dict("records"):
            logger.info("Fetching weather for %s", city_row["city"])
            payload = fetch_weather(client, city_row, settings.max_retries, settings.weather_unit)
            if payload is not None:
                results[city_row["city"]] = payload
    elapsed = time.perf_counter() - start
    logger.info(
        "Sequential extraction finished: %d/%d cities in %.2f seconds",
        len(results),
        len(cities_df),
        elapsed,
    )
    return results


async def fetch_weather_async(
    client: httpx.AsyncClient,
    city_row: Mapping[str, Any],
    max_retries: int = 3,
    weather_unit: str = "celsius",
    backoff_seconds: float = BACKOFF_SECONDS,
) -> dict | None:
    """Asynchronous counterpart of `fetch_weather` with the same retry rules."""
    city = city_row["city"]
    params = build_query_params(city_row, weather_unit)
    total_attempts = max(0, max_retries) + 1
    for attempt in range(1, total_attempts + 1):
        try:
            response = await client.get(OPEN_METEO_URL, params=params)
            return _parse_response(response, city)
        except (httpx.TransportError, RetryableError) as exc:
            logger.error(
                "Attempt %d/%d for %s failed: %s: %s",
                attempt,
                total_attempts,
                city,
                type(exc).__name__,
                exc,
            )
        if attempt < total_attempts:
            await asyncio.sleep(_backoff_delay(attempt, backoff_seconds))
    logger.error("Giving up on %s after %d attempts", city, total_attempts)
    return None


async def extract_all_async(
    cities_df: pd.DataFrame,
    settings: Settings | None = None,
    max_concurrency: int = MAX_CONCURRENT_REQUESTS,
) -> dict[str, dict]:
    """Fetch every city concurrently with `httpx.AsyncClient` and `asyncio.gather`.

    A semaphore caps the number of simultaneous requests to respect the API.
    Returns the same mapping as `extract_all`.
    """
    settings = settings or Settings()
    semaphore = asyncio.Semaphore(max_concurrency)
    city_rows = cities_df.to_dict("records")
    start = time.perf_counter()

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:

        async def fetch_with_limit(city_row: Mapping[str, Any]) -> dict | None:
            async with semaphore:
                logger.info("Fetching weather for %s (async)", city_row["city"])
                return await fetch_weather_async(
                    client, city_row, settings.max_retries, settings.weather_unit
                )

        payloads = await asyncio.gather(*(fetch_with_limit(row) for row in city_rows))

    results = {
        row["city"]: payload
        for row, payload in zip(city_rows, payloads, strict=True)
        if payload is not None
    }
    elapsed = time.perf_counter() - start
    logger.info(
        "Async extraction finished: %d/%d cities in %.2f seconds",
        len(results),
        len(cities_df),
        elapsed,
    )
    return results
