import logging
from datetime import date

import pandas as pd
import pytest

from src.pipeline import (
    HOURLY_COLUMNS,
    REPORT_COLUMNS,
    add_date_column,
    aggregate_daily,
    handle_missing_values,
    merge_with_cities,
    parse_hourly,
    parse_responses,
    transform,
)


@pytest.fixture
def hourly_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city": ["San José"] * 4 + ["Dubai"] * 2,
            "time": [
                "2026-10-16T00:00",
                "2026-10-16T12:00",
                "2026-10-17T00:00",
                "2026-10-17T12:00",
                "2026-10-16T06:00",
                "2026-10-16T15:00",
            ],
            "temperature_2m": [18.5, 27.3, None, 31.4, 29.0, 36.2],
            "precipitation": [0.0, 2.5, 4.0, None, None, 0.0],
        }
    )


def test_parse_hourly_builds_long_frame(fake_payload: dict) -> None:
    frame = parse_hourly("San José", fake_payload)

    assert list(frame.columns) == HOURLY_COLUMNS
    assert len(frame) == 4
    assert (frame["city"] == "San José").all()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"hourly": {"time": ["2026-10-16T00:00"]}},
        {"hourly": {"time": ["a", "b"], "temperature_2m": [1.0], "precipitation": [0.0]}},
    ],
)
def test_parse_hourly_returns_empty_frame_for_invalid_payload(
    payload: dict, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="weather_pipeline"):
        frame = parse_hourly("Nowhere", payload)

    assert frame.empty
    assert list(frame.columns) == HOURLY_COLUMNS
    assert "Invalid hourly data for Nowhere" in caplog.text


def test_parse_responses_concatenates_cities(fake_payload: dict) -> None:
    hourly = parse_responses({"San José": fake_payload, "Dubai": fake_payload, "Bad": {}})

    assert len(hourly) == 8
    assert set(hourly["city"]) == {"San José", "Dubai"}
    assert hourly["precipitation"].isna().sum() == 2


def test_parse_responses_with_no_data_returns_empty_frame() -> None:
    assert parse_responses({}).empty


def test_handle_missing_values_drops_and_fills(
    hourly_df: pd.DataFrame, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="weather_pipeline"):
        cleaned = handle_missing_values(hourly_df)

    assert len(cleaned) == 5
    assert cleaned["temperature_2m"].notna().all()
    assert cleaned["precipitation"].notna().all()
    assert cleaned["precipitation"].tolist() == [0.0, 2.5, 0.0, 0.0, 0.0]
    assert "Missing values: temperature_2m=1 precipitation=2" in caplog.text


def test_add_date_column_converts_time(hourly_df: pd.DataFrame) -> None:
    with_dates = add_date_column(hourly_df)

    assert pd.api.types.is_datetime64_any_dtype(with_dates["time"])
    assert with_dates["date"].tolist()[:3] == [
        date(2026, 10, 16),
        date(2026, 10, 16),
        date(2026, 10, 17),
    ]
    assert "date" not in hourly_df.columns


def test_aggregate_daily_computes_max_and_sum(hourly_df: pd.DataFrame) -> None:
    daily = aggregate_daily(add_date_column(handle_missing_values(hourly_df)))

    assert list(daily.columns) == ["city", "date", "max_temperature", "total_precipitation"]
    rows = {(row.city, row.date): row for row in daily.itertuples()}
    assert len(rows) == 3
    assert rows[("San José", date(2026, 10, 16))].max_temperature == 27.3
    assert rows[("San José", date(2026, 10, 16))].total_precipitation == 2.5
    assert rows[("San José", date(2026, 10, 17))].max_temperature == 31.4
    assert rows[("San José", date(2026, 10, 17))].total_precipitation == 0.0
    assert rows[("Dubai", date(2026, 10, 16))].max_temperature == 36.2


def test_merge_with_cities_adds_names_and_coordinates(
    hourly_df: pd.DataFrame, cities_df: pd.DataFrame
) -> None:
    daily = aggregate_daily(add_date_column(handle_missing_values(hourly_df)))

    merged = merge_with_cities(daily, cities_df)

    assert list(merged.columns) == REPORT_COLUMNS
    assert len(merged) == 3
    dubai = merged[merged["city"] == "Dubai"].iloc[0]
    assert dubai["raw_city"] == "  dubai$$"
    assert dubai["latitude"] == 25.2048
    assert dubai["longitude"] == 55.2708


def test_merge_with_cities_drops_cities_without_data(cities_df: pd.DataFrame) -> None:
    daily = pd.DataFrame(
        {
            "city": ["Dubai"],
            "date": [date(2026, 10, 16)],
            "max_temperature": [36.2],
            "total_precipitation": [0.0],
        }
    )

    assert merge_with_cities(daily, cities_df)["city"].tolist() == ["Dubai"]


def test_transform_runs_all_steps(fake_payload: dict, cities_df: pd.DataFrame) -> None:
    report = transform({"San José": fake_payload}, cities_df)

    assert list(report.columns) == REPORT_COLUMNS
    assert report["date"].tolist() == [date(2026, 10, 16), date(2026, 10, 17)]
    assert report["max_temperature"].tolist() == [27.3, 31.4]
    assert report["total_precipitation"].tolist() == [2.5, 1.0]


def test_transform_without_data_returns_empty_report(cities_df: pd.DataFrame) -> None:
    report = transform({}, cities_df)

    assert report.empty
    assert list(report.columns) == REPORT_COLUMNS
