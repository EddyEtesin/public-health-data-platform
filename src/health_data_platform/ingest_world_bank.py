
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from urllib3.util.retry import Retry


# ---------------------------------------------------------
# Project configuration
# ---------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = ROOT_DIR / "data" / "raw" / "world_bank"

load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = __import__("os").getenv("DATABASE_URL")

BASE_URL = "https://api.worldbank.org/v2"

COUNTRIES = [
    "NGA",  # Nigeria
    "GHA",  # Ghana
    "KEN",  # Kenya
    "ZAF",  # South Africa
    "RWA",  # Rwanda
    "SEN",  # Senegal
    "UGA",  # Uganda
    "ETH",  # Ethiopia
]

INDICATORS = {
    "SH.STA.MMRT": {
        "name": "Maternal mortality ratio",
        "unit": "deaths per 100,000 live births",
    },
    "SH.DYN.MORT": {
        "name": "Under-five mortality rate",
        "unit": "deaths per 1,000 live births",
    },
    "SH.XPD.CHEX.GD.ZS": {
        "name": "Current health expenditure",
        "unit": "percent of GDP",
    },
    "SH.IMM.IDPT": {
        "name": "DPT immunisation coverage",
        "unit": "percent of children aged 12-23 months",
    },
}

SOURCE_NAME = "World Bank Indicators API"


# ---------------------------------------------------------
# PostgreSQL table creation
# ---------------------------------------------------------

def create_tables(engine: Engine) -> None:
    """Create raw-response and analytical observation tables."""

    statements = [
        """
        CREATE TABLE IF NOT EXISTS raw_api_responses (
            raw_response_id BIGSERIAL PRIMARY KEY,
            source_name TEXT NOT NULL,
            indicator_code TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            payload JSONB NOT NULL,
            raw_file_path TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS health_observations (
            country_code VARCHAR(10) NOT NULL,
            country_name TEXT NOT NULL,
            indicator_code VARCHAR(50) NOT NULL,
            indicator_name TEXT NOT NULL,
            year INTEGER NOT NULL,
            value NUMERIC(20, 6),
            unit TEXT,
            observation_status TEXT,
            source_name TEXT NOT NULL,
            last_ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (country_code, indicator_code, year)
        )
        """,
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

    print("PostgreSQL tables are ready.")


# ---------------------------------------------------------
# HTTP client with retry handling
# ---------------------------------------------------------

def create_http_session() -> requests.Session:
    """Create an HTTP session that retries temporary API failures."""

    retry_config = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(max_retries=retry_config)
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "public-health-data-platform/1.0"}
    )
    session.mount("https://", adapter)

    return session


# ---------------------------------------------------------
# Preserve raw API responses
# ---------------------------------------------------------

def save_raw_response(
    engine: Engine,
    indicator_code: str,
    page_number: int,
    endpoint: str,
    payload: dict | list,
    raw_file_path: Path,
) -> None:
    """Write the original response to disk and PostgreSQL JSONB."""

    raw_file_path.parent.mkdir(parents=True, exist_ok=True)

    raw_file_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    relative_path = raw_file_path.relative_to(ROOT_DIR).as_posix()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO raw_api_responses (
                    source_name,
                    indicator_code,
                    page_number,
                    endpoint,
                    payload,
                    raw_file_path
                )
                VALUES (
                    :source_name,
                    :indicator_code,
                    :page_number,
                    :endpoint,
                    CAST(:payload AS JSONB),
                    :raw_file_path
                )
                """
            ),
            {
                "source_name": SOURCE_NAME,
                "indicator_code": indicator_code,
                "page_number": page_number,
                "endpoint": endpoint,
                "payload": json.dumps(
                    payload, ensure_ascii=False
                ),
                "raw_file_path": relative_path,
            },
        )


# ---------------------------------------------------------
# Transform API records into database observations
# ---------------------------------------------------------

def normalise_observation(
    item: dict,
    requested_indicator: str,
) -> dict | None:
    """Convert one World Bank API record into a database row."""

    country = item.get("country") or {}
    indicator = item.get("indicator") or {}

    country_code = (
        item.get("countryiso3code")
        or country.get("id")
    )
    year_value = item.get("date")

    if not country_code or not year_value:
        return None

    try:
        year = int(year_value)
    except (TypeError, ValueError):
        return None

    indicator_code = indicator.get("id") or requested_indicator
    indicator_config = INDICATORS.get(
        indicator_code,
        INDICATORS[requested_indicator],
    )

    raw_value = item.get("value")

    try:
        value = float(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        value = None

    api_source = item.get("source")
    source_name = (
        api_source.get("value")
        if isinstance(api_source, dict)
        else None
    )

    return {
        "country_code": country_code,
        "country_name": country.get("value") or country_code,
        "indicator_code": indicator_code,
        "indicator_name": (
            indicator.get("value") or indicator_config["name"]
        ),
        "year": year,
        "value": value,
        "unit": indicator_config["unit"],
        "observation_status": item.get("obs_status") or None,
        "source_name": source_name or "World Bank",
    }


# ---------------------------------------------------------
# Extract API data with pagination
# ---------------------------------------------------------

def fetch_indicator(
    session: requests.Session,
    engine: Engine,
    indicator_code: str,
    run_id: str,
) -> tuple[list[dict], int]:
    """Fetch all pages for an indicator and preserve raw responses."""

    country_path = ";".join(COUNTRIES)
    endpoint = (
        f"{BASE_URL}/country/{country_path}"
        f"/indicator/{indicator_code}"
    )

    observations: list[dict] = []
    page_number = 1
    pages_saved = 0

    while True:
        params = {
            "format": "json",
            "per_page": 1000,
            "page": page_number,
        }

        response = session.get(
            endpoint,
            params=params,
            timeout=45,
        )
        response.raise_for_status()

        payload = response.json()

        if (
            not isinstance(payload, list)
            or not payload
            or not isinstance(payload[0], dict)
        ):
            raise RuntimeError(
                f"Unexpected World Bank response for {indicator_code}"
            )

        metadata = payload[0]

        if "message" in metadata and "page" not in metadata:
            raise RuntimeError(
                f"World Bank API error for {indicator_code}: "
                f"{metadata['message']}"
            )

        records = (
            payload[1]
            if len(payload) > 1 and isinstance(payload[1], list)
            else []
        )

        filename = (
            f"{run_id}_{indicator_code.replace('.', '_')}"
            f"_page_{page_number}.json"
        )

        raw_file_path = RAW_DATA_DIR / filename

        save_raw_response(
            engine=engine,
            indicator_code=indicator_code,
            page_number=page_number,
            endpoint=response.url,
            payload=payload,
            raw_file_path=raw_file_path,
        )

        pages_saved += 1

        for item in records:
            observation = normalise_observation(
                item,
                requested_indicator=indicator_code,
            )

            if observation is not None:
                observations.append(observation)

        total_pages = int(metadata.get("pages") or 1)

        print(
            f"  Page {page_number}/{total_pages}: "
            f"{len(records)} source records"
        )

        if page_number >= total_pages:
            break

        page_number += 1

    return observations, pages_saved


# ---------------------------------------------------------
# Load observations using an idempotent upsert
# ---------------------------------------------------------

def upsert_observations(
    engine: Engine,
    observations: list[dict],
) -> None:
    """Insert new observations and update existing observations."""

    if not observations:
        print("No observations to load.")
        return

    upsert_sql = text(
        """
        INSERT INTO health_observations (
            country_code,
            country_name,
            indicator_code,
            indicator_name,
            year,
            value,
            unit,
            observation_status,
            source_name
        )
        VALUES (
            :country_code,
            :country_name,
            :indicator_code,
            :indicator_name,
            :year,
            :value,
            :unit,
            :observation_status,
            :source_name
        )
        ON CONFLICT (country_code, indicator_code, year)
        DO UPDATE SET
            country_name = EXCLUDED.country_name,
            indicator_name = EXCLUDED.indicator_name,
            value = EXCLUDED.value,
            unit = EXCLUDED.unit,
            observation_status = EXCLUDED.observation_status,
            source_name = EXCLUDED.source_name,
            last_ingested_at = CURRENT_TIMESTAMP
        """
    )

    with engine.begin() as connection:
        connection.execute(upsert_sql, observations)

    print(
        f"Successfully inserted or updated "
        f"{len(observations):,} observations."
    )


# ---------------------------------------------------------
# Run the ingestion pipeline
# ---------------------------------------------------------

def main() -> None:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing. Check the project .env file."
        )

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
    )

    try:
        # Fail early if PostgreSQL is unavailable.
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        create_tables(engine)

        all_observations: list[dict] = []
        total_pages = 0

        with create_http_session() as session:
            for indicator_code, config in INDICATORS.items():
                print(
                    f"\nFetching {config['name']} "
                    f"({indicator_code})..."
                )

                observations, pages_saved = fetch_indicator(
                    session=session,
                    engine=engine,
                    indicator_code=indicator_code,
                    run_id=run_id,
                )

                all_observations.extend(observations)
                total_pages += pages_saved

                print(
                    f"Collected {len(observations):,} observations "
                    f"for {indicator_code}."
                )

        upsert_observations(engine, all_observations)

        print("\nIngestion summary")
        print("-----------------")
        print(f"Countries configured: {len(COUNTRIES)}")
        print(f"Indicators requested: {len(INDICATORS)}")
        print(f"Raw API pages saved: {total_pages}")
        print(f"Observation records processed: {len(all_observations):,}")
        print(f"Raw files directory: {RAW_DATA_DIR}")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
