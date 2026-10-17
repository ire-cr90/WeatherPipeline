import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import src.pipeline as pipeline
from src.pipeline import Settings, extract, main, parse_args


@pytest.fixture
def patched_pipeline(tmp_path: Path, fake_payload: dict) -> Iterator[dict[str, MagicMock]]:
    real_export_excel = pipeline.export_excel
    real_export_alerts = pipeline.export_alerts_json
    with (
        patch.object(pipeline, "configure_logging") as configure_logging,
        patch.object(pipeline, "load_settings", return_value=Settings(alert_threshold_c=25)),
        patch.object(
            pipeline,
            "extract",
            side_effect=lambda cities, settings, mode: {
                city: fake_payload for city in cities["city"]
            },
        ) as extract_mock,
        patch.object(
            pipeline,
            "export_excel",
            side_effect=lambda df: real_export_excel(df, tmp_path),
        ) as export_excel,
        patch.object(
            pipeline,
            "export_alerts_json",
            side_effect=lambda df, threshold: real_export_alerts(df, threshold, tmp_path),
        ) as export_alerts,
    ):
        yield {
            "configure_logging": configure_logging,
            "extract": extract_mock,
            "export_excel": export_excel,
            "export_alerts_json": export_alerts,
        }


def test_parse_args_defaults_to_sequential() -> None:
    assert parse_args([]).mode == "sequential"
    assert parse_args(["--mode", "async"]).mode == "async"


def test_parse_args_rejects_unknown_mode() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--mode", "parallel"])


def test_main_runs_full_pipeline(tmp_path: Path, patched_pipeline: dict[str, MagicMock]) -> None:
    assert main([]) == 0

    patched_pipeline["configure_logging"].assert_called_once_with("INFO")
    assert patched_pipeline["extract"].call_args.args[2] == "sequential"
    report = pd.read_excel(next(tmp_path.glob("weather_report_*.xlsx")))
    assert len(report) == 2 * patched_pipeline["extract"].call_args.args[0]["city"].nunique()
    alerts = json.loads(next(tmp_path.glob("weather_alerts_*.json")).read_text(encoding="utf-8"))
    assert alerts
    assert all(alert["threshold"] == 25 for alert in alerts)


def test_main_converts_threshold_for_fahrenheit(patched_pipeline: dict[str, MagicMock]) -> None:
    settings = Settings(weather_unit="fahrenheit", alert_threshold_c=30)
    with patch.object(pipeline, "load_settings", return_value=settings):
        assert main(["--mode", "async"]) == 0

    assert patched_pipeline["extract"].call_args.args[2] == "async"
    assert patched_pipeline["export_alerts_json"].call_args.args[1] == pytest.approx(86.0)


def test_main_fails_when_nothing_is_extracted(patched_pipeline: dict[str, MagicMock]) -> None:
    patched_pipeline["extract"].side_effect = None
    patched_pipeline["extract"].return_value = {}

    assert main([]) == 1
    patched_pipeline["export_excel"].assert_not_called()


def test_main_returns_error_code_on_unexpected_failure(
    patched_pipeline: dict[str, MagicMock],
) -> None:
    patched_pipeline["extract"].side_effect = RuntimeError("boom")

    assert main([]) == 1


@pytest.mark.parametrize(
    ("mode", "target"),
    [("sequential", "extract_all"), ("async", "extract_all_async"), ("compare", None)],
)
def test_extract_dispatches_by_mode(
    mode: str, target: str | None, cities_df: pd.DataFrame, fake_payload: dict
) -> None:
    expected = {"San José": fake_payload}
    with (
        patch.object(pipeline, "extract_all", return_value=expected) as sequential,
        patch.object(pipeline, "extract_all_async", return_value=expected) as concurrent,
        patch.object(
            pipeline, "compare_extraction_modes", return_value=(expected, {})
        ) as comparison,
    ):
        assert extract(cities_df, Settings(), mode) == expected

    called = {
        "extract_all": sequential.called,
        "extract_all_async": concurrent.called,
        None: comparison.called,
    }
    assert called[target]
    assert sum(called.values()) == 1
