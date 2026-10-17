import pytest

from src.pipeline import normalize_city_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  nEw YoRK  ", "New York"),
        ("*Tokyo!!", "Tokyo"),
        ("#paris#", "Paris"),
        ("lima___", "Lima"),
        ("buenos___AIRES", "Buenos Aires"),
        ("cape    town", "Cape Town"),
        ("MUMBAI@2024", "Mumbai"),
        ("~singapore~", "Singapore"),
    ],
)
def test_removes_noise_and_title_cases(raw: str, expected: str) -> None:
    assert normalize_city_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  São   Paulo ", "São Paulo"),
        ("méxico CITY", "México City"),
        ("REYKJAVÍK", "Reykjavík"),
        ("san_josé", "San José"),
    ],
)
def test_keeps_accented_letters(raw: str, expected: str) -> None:
    assert normalize_city_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("villa o'higgins", "Villa O'Higgins"),
        ("O'HIGGINS", "O'Higgins"),
        ("côte d'ivoire", "Côte D'Ivoire"),
        ("xi'an", "Xi'an"),
        ("sAINT-étienne", "Saint-Étienne"),
        ("  st. petersburg ", "St. Petersburg"),
    ],
)
def test_handles_apostrophes_hyphens_and_dots(raw: str, expected: str) -> None:
    assert normalize_city_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "***", "1234", "___"])
def test_returns_empty_string_when_nothing_valid_remains(raw: str) -> None:
    assert normalize_city_name(raw) == ""
