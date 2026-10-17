"""Global Weather Analytics & Alerting Pipeline."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "pipeline.log"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"

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
