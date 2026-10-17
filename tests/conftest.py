import logging
from collections.abc import Iterator

import httpx
import pandas as pd
import pytest

from src.pipeline import OPEN_METEO_URL


@pytest.fixture
def fake_payload() -> dict:
    return {
        "latitude": 9.93,
        "longitude": -84.09,
        "timezone": "America/Costa_Rica",
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C", "precipitation": "mm"},
        "hourly": {
            "time": [
                "2026-10-16T00:00",
                "2026-10-16T12:00",
                "2026-10-17T00:00",
                "2026-10-17T12:00",
            ],
            "temperature_2m": [18.5, 27.3, 17.9, 31.4],
            "precipitation": [0.0, 2.5, None, 1.0],
        },
    }


@pytest.fixture
def cities_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "raw_city": ["san_josé", "  dubai$$"],
            "city": ["San José", "Dubai"],
            "latitude": [9.9281, 25.2048],
            "longitude": [-84.0907, 55.2708],
        }
    )


@pytest.fixture
def make_response():
    def _make(status_code: int, json_body: dict | None = None) -> httpx.Response:
        return httpx.Response(
            status_code,
            json=json_body if json_body is not None else {},
            request=httpx.Request("GET", OPEN_METEO_URL),
        )

    return _make


@pytest.fixture
def restore_root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    httpx_level = logging.getLogger("httpx").level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(httpx_level)
