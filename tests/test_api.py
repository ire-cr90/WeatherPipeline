import logging
from unittest.mock import AsyncMock, call, patch

import httpx
import pandas as pd
import pytest

from src.pipeline import (
    OPEN_METEO_URL,
    Settings,
    build_query_params,
    compare_extraction_modes,
    extract_all,
    extract_all_async,
    fetch_weather,
    fetch_weather_async,
)

CITY_ROW = {"city": "San José", "latitude": 9.9281, "longitude": -84.0907}


def test_build_query_params_defaults_to_celsius() -> None:
    assert build_query_params(CITY_ROW) == {
        "latitude": 9.9281,
        "longitude": -84.0907,
        "hourly": "temperature_2m,precipitation",
        "timezone": "auto",
    }


def test_build_query_params_adds_fahrenheit_unit() -> None:
    assert build_query_params(CITY_ROW, "fahrenheit")["temperature_unit"] == "fahrenheit"


def test_fetch_weather_returns_payload(make_response, fake_payload: dict) -> None:
    with (
        patch.object(httpx.Client, "get", return_value=make_response(200, fake_payload)) as get,
        httpx.Client() as client,
    ):
        payload = fetch_weather(client, CITY_ROW)

    assert payload == fake_payload
    get.assert_called_once_with(OPEN_METEO_URL, params=build_query_params(CITY_ROW))


def test_fetch_weather_retries_transient_failures(
    make_response, fake_payload: dict, caplog: pytest.LogCaptureFixture
) -> None:
    responses = [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        make_response(503),
        make_response(200, fake_payload),
    ]
    with (
        patch.object(httpx.Client, "get", side_effect=responses) as get,
        patch("src.pipeline.time.sleep") as sleep,
        httpx.Client() as client,
        caplog.at_level(logging.ERROR, logger="weather_pipeline"),
    ):
        payload = fetch_weather(client, CITY_ROW, max_retries=3, backoff_seconds=0.5)

    assert payload == fake_payload
    assert get.call_count == 4
    assert sleep.call_args_list == [call(0.5), call(1.0), call(2.0)]
    assert len(caplog.records) == 3


def test_fetch_weather_gives_up_after_max_retries(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch.object(httpx.Client, "get", side_effect=httpx.ConnectTimeout("timed out")) as get,
        patch("src.pipeline.time.sleep"),
        httpx.Client() as client,
        caplog.at_level(logging.ERROR, logger="weather_pipeline"),
    ):
        payload = fetch_weather(client, CITY_ROW, max_retries=2)

    assert payload is None
    assert get.call_count == 3
    assert "Giving up on San José after 3 attempts" in caplog.text


def test_fetch_weather_retries_rate_limited_responses(make_response, fake_payload: dict) -> None:
    responses = [make_response(429), make_response(200, fake_payload)]
    with (
        patch.object(httpx.Client, "get", side_effect=responses) as get,
        patch("src.pipeline.time.sleep"),
        httpx.Client() as client,
    ):
        payload = fetch_weather(client, CITY_ROW)

    assert payload == fake_payload
    assert get.call_count == 2


def test_fetch_weather_does_not_retry_client_errors(make_response) -> None:
    with (
        patch.object(httpx.Client, "get", return_value=make_response(400)) as get,
        httpx.Client() as client,
    ):
        payload = fetch_weather(client, CITY_ROW)

    assert payload is None
    get.assert_called_once()


def test_fetch_weather_handles_invalid_json() -> None:
    response = httpx.Response(200, text="not json", request=httpx.Request("GET", OPEN_METEO_URL))
    with patch.object(httpx.Client, "get", return_value=response), httpx.Client() as client:
        assert fetch_weather(client, CITY_ROW) is None


def test_extract_all_is_sequential_and_skips_failures(
    make_response, fake_payload: dict, cities_df: pd.DataFrame, caplog: pytest.LogCaptureFixture
) -> None:
    responses = [make_response(200, fake_payload), make_response(404)]
    with (
        patch.object(httpx.Client, "get", side_effect=responses) as get,
        caplog.at_level(logging.INFO, logger="weather_pipeline"),
    ):
        results = extract_all(cities_df, Settings(max_retries=0))

    assert results == {"San José": fake_payload}
    assert [c.kwargs["params"]["latitude"] for c in get.call_args_list] == [9.9281, 25.2048]
    assert "Sequential extraction finished: 1/2 cities" in caplog.text


async def test_fetch_weather_async_retries_then_succeeds(make_response, fake_payload: dict) -> None:
    responses = [httpx.ConnectError("boom"), make_response(200, fake_payload)]
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as get:
        get.side_effect = responses
        async with httpx.AsyncClient() as client:
            payload = await fetch_weather_async(client, CITY_ROW, backoff_seconds=0)

    assert payload == fake_payload
    assert get.await_count == 2


async def test_extract_all_async_gathers_all_cities(
    make_response, fake_payload: dict, cities_df: pd.DataFrame
) -> None:
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as get:
        get.return_value = make_response(200, fake_payload)
        results = await extract_all_async(cities_df, Settings())

    assert set(results) == {"San José", "Dubai"}
    assert get.await_count == 2


def test_compare_extraction_modes_reports_timings(
    make_response, fake_payload: dict, cities_df: pd.DataFrame
) -> None:
    with (
        patch.object(httpx.Client, "get", return_value=make_response(200, fake_payload)),
        patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            return_value=make_response(200, fake_payload),
        ),
    ):
        results, timings = compare_extraction_modes(cities_df, Settings())

    assert set(results) == {"San José", "Dubai"}
    assert set(timings) == {"sequential_seconds", "async_seconds", "speedup"}
    assert timings["sequential_seconds"] > 0
    assert timings["async_seconds"] > 0
