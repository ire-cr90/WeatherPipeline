import logging
from pathlib import Path

import pytest

from src.pipeline import DATA_FILE, load_cities


def write_csv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "cities.csv"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_cities_keeps_raw_and_clean_names(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        'city,latitude,longitude\n"  nEw YoRK  ",40.7128,-74.0060\nsan_josé,9.9281,-84.0907\n',
    )

    cities = load_cities(path)

    assert list(cities.columns) == ["raw_city", "city", "latitude", "longitude"]
    assert cities["raw_city"].tolist() == ["  nEw YoRK  ", "san_josé"]
    assert cities["city"].tolist() == ["New York", "San José"]
    assert cities["latitude"].tolist() == [40.7128, 9.9281]


def test_load_cities_skips_and_logs_invalid_rows(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_csv(
        tmp_path,
        "city,latitude,longitude\n"
        "lima___,-12.0464,-77.0428\n"
        "atlantis,123.45,10\n"
        "nowhere,10,-200\n"
        "bad,abc,10\n"
        "***,10,10\n"
        "edge,-90,180\n",
    )

    with caplog.at_level(logging.ERROR, logger="weather_pipeline"):
        cities = load_cities(path)

    assert cities["city"].tolist() == ["Lima", "Edge"]
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 4
    assert "atlantis" in errors[0].getMessage()


def test_load_cities_requires_columns(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "name,lat\nLima,-12\n")

    with pytest.raises(ValueError, match="Missing required columns"):
        load_cities(path)


def test_project_dataset_has_at_least_fifteen_valid_cities() -> None:
    cities = load_cities(DATA_FILE)

    assert len(cities) >= 15
    assert cities["city"].is_unique
    assert (cities["raw_city"] != cities["city"]).all()
