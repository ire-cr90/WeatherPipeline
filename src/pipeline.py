"""Global Weather Analytics & Alerting Pipeline."""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / "data" / "cities_raw.csv"
LOG_FILE = PROJECT_ROOT / "pipeline.log"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"

INVALID_NAME_CHARS = re.compile(r"[^\w\s'.-]|[\d_]")
REPEATED_SPACES = re.compile(r"\s+")

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

    Unknown level names fall back to `INFO`.
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
