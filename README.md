# Global Weather Analytics & Alerting Pipeline

[![CI](https://github.com/ire-cr90/WeatherPipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/ire-cr90/WeatherPipeline/actions/workflows/ci.yml)

Laboratory project for **SOFT-753 Automation with Python** (CENFOTEC, section SEV1, period M5-2026).

The pipeline reads a deliberately dirty list of world cities, normalizes the names, downloads the
hourly forecast of every city from the free [Open-Meteo](https://open-meteo.com/) API, aggregates it
per city and day with pandas, and produces a formatted Excel report plus a JSON file with the
cities that exceed a temperature threshold.

```
data/cities_raw.csv ──► load & normalize ──► extract (Open-Meteo) ──► transform (pandas) ──► reports/
                         (re, validation)     (httpx, retries)          (clean, group, merge)   .xlsx + .json
```

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.13 or newer
- Python 3.13 (uv installs it automatically from `.python-version`)
- Internet access to run the pipeline (the test suite runs fully offline)

## Setup

```bash
git clone https://github.com/ire-cr90/WeatherPipeline.git
cd WeatherPipeline
uv sync
cp .env.example .env
```

On Windows PowerShell use `Copy-Item .env.example .env` instead of `cp`.

### Configuration (`.env`)

| Variable            | Default   | Meaning                                                        |
| ------------------- | --------- | -------------------------------------------------------------- |
| `WEATHER_UNIT`      | `celsius` | `celsius` or `fahrenheit` (adds `temperature_unit=fahrenheit`) |
| `MAX_RETRIES`       | `3`       | Retries after the first attempt for transient API failures     |
| `LOG_LEVEL`         | `INFO`    | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`              |
| `ALERT_THRESHOLD_C` | `30`      | Alert threshold in °C (always read from the environment)       |

Every variable has a default, so the pipeline and the tests also work without a `.env` file
(for example in CI). Real environment variables take precedence over the values in `.env`.

## Usage

```bash
uv run python src/pipeline.py
```

Optional extraction strategies:

```bash
uv run python src/pipeline.py --mode sequential
uv run python src/pipeline.py --mode async
uv run python src/pipeline.py --mode compare
```

| Mode         | Behaviour                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------ |
| `sequential` | Default. A standard `for` loop, one request at a time (Phase 3 requirement).               |
| `async`      | `httpx.AsyncClient` + `asyncio.gather`, at most 10 concurrent requests.                    |
| `compare`    | Runs both, logs both timings and the speedup, and builds the reports from the sequential run. |

A typical run with 19 cities takes about 4.5 s sequentially and 1.5–2.5 s asynchronously.

## Outputs

| File                                   | Content                                                                                   |
| -------------------------------------- | ----------------------------------------------------------------------------------------- |
| `pipeline.log`                         | Every log record, format `%(asctime)s \| %(levelname)s \| %(message)s` (also on console). |
| `reports/weather_report_YYYYMMDD.xlsx` | `raw_city`, `city`, `latitude`, `longitude`, `date`, `max_temperature`, `total_precipitation`. Bold coloured header, sized columns, frozen top row, autofilter, 1–2 decimals. |
| `reports/weather_alerts_YYYYMMDD.json` | List of city-days whose `max_temperature` is above the threshold: `city`, `date`, `max_temperature`, `threshold`, `generated_at`. |

`pipeline.log` and `reports/` are generated artifacts and are ignored by git.

## Tests and code quality

```bash
uv run ruff check .
uv run ruff format .
uv run pytest -v
```

The suite (71 tests) covers name normalization, settings and logging, CSV loading and validation,
the API layer (mocked with `unittest.mock.patch` on `httpx.Client.get` / `httpx.AsyncClient.get`,
including the retry paths), every transformation step, the Excel and JSON exports and `main()`.
No test needs internet, and every file written by a test goes to `tmp_path`.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`:
`actions/checkout@v4`, `astral-sh/setup-uv@v10.3.0` (uv `0.13.1`), `uv sync`,
`uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest -v`.

## Project structure

```
.
├── .github/workflows/ci.yml   GitHub Actions pipeline
├── data/cities_raw.csv        20 dirty input rows (19 valid cities + 1 invalid on purpose)
├── src/
│   ├── __init__.py
│   └── pipeline.py            Settings, logging, extraction, transformation, reports, main()
├── tests/
│   ├── conftest.py            Shared fixtures (fake Open-Meteo payload, cities, responses)
│   ├── test_normalization.py
│   ├── test_config.py
│   ├── test_loading.py
│   ├── test_api.py
│   ├── test_transform.py
│   ├── test_reports.py
│   └── test_main.py
├── .env.example               Template of the environment variables
├── .gitignore
├── .python-version
├── pyproject.toml             Dependencies, pytest and ruff configuration
└── uv.lock
```

## Design decisions

1. **Non-packaged uv project.** uv 0.13 `uv init` creates a packaged `src/<name>/` layout by
   default, so the project was initialized with `uv init --app --no-package` to get the exact
   `src/pipeline.py` layout the instructions ask for.
2. **Underscores are replaced before invalid characters are removed.** Doing it in the order listed
   in the instructions would turn `buenos___AIRES` into `buenosAIRES`; replacing first gives
   `Buenos Aires`.
3. **Custom title case.** `str.title()` produces `It'S` or `D'ivoire`-style mistakes, so each word
   is capitalized individually: hyphenated parts are capitalized separately (`Saint-Étienne`) and a
   single-letter prefix before an apostrophe is treated as a particle (`O'Higgins`, `D'Ivoire`),
   while longer prefixes keep the rest lowercase (`Xi'an`). Leading and trailing dots, hyphens and
   apostrophes are stripped; dots inside the name are kept (`St. Petersburg`). Digits are removed
   (`MUMBAI@2024` → `Mumbai`).
4. **Invalid input rows.** Rows with an empty clean name, non-numeric coordinates or coordinates out
   of range are logged as `ERROR` and skipped. The dataset includes `atlantis???` with impossible
   coordinates on purpose to demonstrate this.
5. **Retry policy.** `MAX_RETRIES` is the number of *retries*, so `MAX_RETRIES=3` means up to 4
   attempts. Timeouts, connection errors (`httpx.TransportError`), 5xx and 429 (rate limit)
   responses are retried with exponential backoff (0.5 s, 1 s, 2 s). Other 4xx responses and
   invalid JSON are not retried because repeating the request would give the same result. A city
   that fails returns `None`, is logged and the pipeline continues.
6. **httpx logs.** httpx logs every request at `INFO`; its logger is limited to `WARNING` so the
   pipeline's own messages stay readable.
7. **Paths are relative to the project root**, not to the current directory, so `pipeline.log`,
   `data/` and `reports/` resolve correctly wherever the command is run from.
8. **Missing values.** Hourly rows without temperature are dropped because temperature is the
   metric the report and the alerts are built on, and an invented value would distort the daily
   maximum. Missing precipitation is filled with `0.0`: in Open-Meteo a missing hourly
   precipitation value almost always means no recorded precipitation, filling keeps the hour's
   valid temperature, and it makes the daily sum and the Excel cells explicitly numeric. Both
   counts are logged before cleaning.
9. **Local dates.** The request uses `timezone=auto`, so `date` is the calendar day in each city's
   own timezone; the first and last day can therefore differ slightly between cities.
10. **Merge.** Aggregates are joined to the cities table with an inner merge validated as
    `one_to_many`, so a city without data does not produce empty rows and a duplicated city name
    raises an error instead of silently multiplying rows.
11. **Alert threshold.** The instructions contained both 25 °C and 30 °C; **30 °C** was chosen and is
    stored in `.env` / `.env.example`. The code never hardcodes it: `ALERT_THRESHOLD_C` is read from
    the environment. An alert requires a temperature strictly *above* the threshold. When
    `WEATHER_UNIT=fahrenheit`, the Celsius threshold is converted to °F before comparing. Alerts are
    one entry per city-day, sorted from hottest to coolest.
12. **Report file names** use the local run date; running twice on the same day overwrites the
    files.
13. **Optional async version.** Implemented in addition to the required sequential loop, behind the
    `--mode` flag, with a semaphore of 10 concurrent requests to be polite with the free API.
14. **Tests are split by area** instead of a single `test_pipeline.py`, and share fixtures through
    `tests/conftest.py`. A fixture restores the root logger after the logging tests.
15. **CI versions.** `actions/checkout@v4` as requested; `astral-sh/setup-uv` is pinned to `v10.3.0`
    and uv to `0.13.1`, the version used locally, so CI and local runs match.

## Known issues in the original instructions

- **`asyncio` is part of the standard library.** It must not be installed with `uv add asyncio`:
  the `asyncio` package on PyPI is an obsolete backport that breaks modern Python. It is simply
  imported.
- **Alert threshold mismatch.** `.env` asked for `ALERT_THRESHOLD_C=25` while Phase 5 asked for
  cities above 30 °C. Resolved by using 30 °C, read from the environment (see decision 11).
- **Sequential vs. asynchronous.** The title says "asynchronous pipeline" but Phase 3 requires a
  sequential loop. The sequential loop is the default; the async version is optional (`--mode`).
- **Typo.** The PDF says "calle"; the main file is `src/pipeline.py`.

## Repository

https://github.com/ire-cr90/WeatherPipeline
