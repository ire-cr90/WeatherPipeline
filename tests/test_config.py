import logging
from pathlib import Path

import pytest

from src.pipeline import LOG_FORMAT, Settings, configure_logging, load_settings, logger

ENV_KEYS = ["WEATHER_UNIT", "MAX_RETRIES", "LOG_LEVEL", "ALERT_THRESHOLD_C"]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.usefixtures("clean_env")
def test_load_settings_uses_defaults_without_env_file(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "missing.env") == Settings()


@pytest.mark.usefixtures("clean_env")
def test_load_settings_reads_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WEATHER_UNIT=Fahrenheit\nMAX_RETRIES=5\nLOG_LEVEL=debug\nALERT_THRESHOLD_C=25\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings == Settings(
        weather_unit="fahrenheit", max_retries=5, log_level="DEBUG", alert_threshold_c=25.0
    )


@pytest.mark.usefixtures("clean_env")
def test_environment_variables_take_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ALERT_THRESHOLD_C=25\n", encoding="utf-8")
    monkeypatch.setenv("ALERT_THRESHOLD_C", "30")

    assert load_settings(env_file).alert_threshold_c == 30.0


@pytest.mark.usefixtures("restore_root_logging")
def test_configure_logging_writes_to_file_with_format(tmp_path: Path) -> None:
    log_file = tmp_path / "pipeline.log"

    configure_logging("WARNING", log_file)
    logger.info("hidden message")
    logger.error("visible message")

    content = log_file.read_text(encoding="utf-8")
    assert logging.getLogger().level == logging.WARNING
    assert "| ERROR | visible message" in content
    assert "hidden message" not in content
    assert any(
        handler.formatter and handler.formatter._fmt == LOG_FORMAT
        for handler in logging.getLogger().handlers
    )


@pytest.mark.usefixtures("restore_root_logging")
def test_configure_logging_falls_back_to_info(tmp_path: Path) -> None:
    configure_logging("NOT_A_LEVEL", tmp_path / "pipeline.log")

    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("httpx").level == logging.WARNING
