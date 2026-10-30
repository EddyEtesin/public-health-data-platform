# Public Health Data Platform

**Health Observatory** is a public health data platform for exploring selected health indicators across eight African countries. It brings together World Bank indicator data, a PostgreSQL data store, a Python ingestion workflow, and a Streamlit dashboard for examining trends, comparing countries, and checking data completeness.

The dashboard is designed to make the underlying observations easy to inspect. Missing values remain missing, reporting periods are shown explicitly, and indicator-specific notes explain how each measure should be interpreted.

## Dashboard preview

The screenshots below show the interactive dashboard, indicator-specific guidance, database-wide coverage reporting, and historical trend exploration.

<p align="center">
  <img src="docs/screenshots/01-dashboard-overview.png" alt="Health Observatory dashboard overview with country and indicator filters and KPI cards" width="100%">
</p>

<p align="center">
  <img src="docs/screenshots/02-indicator-guidance.png" alt="Indicator guidance explaining current health expenditure as a percentage of GDP" width="49%">
  <img src="docs/screenshots/03-data-coverage.png" alt="Data completeness report showing available and missing observations by indicator" width="49%">
</p>

<p align="center">
  <img src="docs/screenshots/04-historical-trends.png" alt="Historical trend chart showing health expenditure as a percentage of GDP across countries" width="100%">
</p>

## What the platform does

- Retrieves public health indicator data from the [World Bank Indicators API](https://api.worldbank.org/).
- Stores source API responses and structured country-indicator-year observations in PostgreSQL.
- Provides an interactive Streamlit dashboard with country, indicator, and reporting-year filters.
- Displays indicator summaries, country comparisons, historical trends, and descriptive findings for the selected data.
- Reports database-wide data completeness, missing observations, and the latest year with a non-null value for each indicator.
- Allows filtered observations to be exported as CSV.

## Countries and indicators

The current dataset covers eight countries:

Nigeria, Ghana, Kenya, South Africa, Rwanda, Senegal, Uganda, and Ethiopia.

Four World Bank indicators are included:

| Indicator | World Bank code | Unit / definition |
|---|---|---|
| Under-five mortality rate | `SH.DYN.MORT` | Deaths per 1,000 live births |
| DPT immunisation coverage | `SH.IMM.IDPT` | Percentage of children aged 12–23 months who received three doses of DPT vaccine |
| Maternal mortality ratio | `SH.STA.MMRT` | Modelled maternal deaths per 100,000 live births |
| Current health expenditure | `SH.XPD.CHEX.GD.ZS` | Current health expenditure as a percentage of GDP |

The latest available year is determined separately for each indicator from non-missing observations. In the data snapshot dated **30 October 2026**, under-five mortality and DPT immunisation have observations through 2024. Maternal mortality and current health expenditure have observations through 2023. A stored row for a later year with a `NULL` value is not treated as a published observation.

## Architecture

```mermaid
flowchart TD
    A[World Bank Indicators API] --> B[Python ingestion workflow]
    B --> C[(PostgreSQL)]
    B --> D[(raw_api_responses)]
    C --> E[health_observations]
    E --> F[Streamlit dashboard]
    F --> G[Charts, findings, data coverage and CSV export]
```

The main database tables are:

- `raw_api_responses`: retained API response payloads for source traceability.
- `health_observations`: structured observations with country, indicator, year, value, unit, and ingestion timestamp fields.

The dashboard reads from `health_observations`. SQL queries are executed through SQLAlchemy, while pandas is used to prepare data for tables, calculations, and Plotly charts.

## Dashboard

The **Health Observatory** dashboard includes:

- **Filter controls:** select one or more countries, an indicator, and a reporting-year range.
- **Indicator guidance:** plain-language definitions, units, and interpretation notes for each measure.
- **Findings report:** descriptive summaries of the selected observations, including changes across the selected period.
- **Trend chart:** view how an indicator has changed over time for the selected countries.
- **Country comparison:** compare the latest available observations across countries.
- **Data explorer:** inspect the filtered observations and export them to CSV.
- **Data coverage and reliability:** review stored records, available and missing values, completeness percentages, and latest available years across the full database. This report is independent of the dashboard filters.

The interface styling is defined in `dashboard/theme.css` and applied by `dashboard/app.py`.

## Data quality snapshot

The database-wide coverage assessment on **30 October 2026** contained 2,112 country-indicator-year records. Of these, 1,340 had a non-null value and 772 were missing, giving an overall completeness of **63.45%**.

| Indicator | Stored records | Available values | Missing values | Completeness | Latest non-missing year |
|---|---:|---:|---:|---:|---:|
| Under-five mortality | 528 | 496 | 32 | 93.94% | 2024 |
| DPT immunisation | 528 | 340 | 188 | 64.39% | 2024 |
| Maternal mortality | 528 | 312 | 216 | 59.09% | 2023 |
| Current health expenditure | 528 | 192 | 336 | 36.36% | 2023 |
| **Total** | **2,112** | **1,340** | **772** | **63.45%** | — |

These are snapshot figures. The dashboard calculates coverage from the database when the report loads, so the values may change after a new ingestion run. Completeness describes the proportion of stored records with values. It does not measure accuracy, precision, or how well national estimates represent differences between districts or communities.

## Technology

- **Language:** Python
- **Data source:** World Bank Indicators API
- **Database:** PostgreSQL
- **Data access:** SQLAlchemy
- **Data preparation:** pandas
- **Visualisation:** Plotly Express
- **Dashboard:** Streamlit
- **Configuration:** python-dotenv

## Local setup

### 1. Requirements

Install Python 3.12 or 3.13 and PostgreSQL. The project was developed locally with PostgreSQL 18.3 and Python 3.13.14. Python 3.12 or 3.13 is recommended for a straightforward Windows setup.

### 2. Clone the repository

```bash
git clone https://github.com/EddyEtesin/public-health-data-platform.git
cd public-health-data-platform
```

Replace `<your-github-username>` with the GitHub account or organisation that owns the repository.

### 3. Create and activate a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install Python dependencies

If the repository includes a `requirements.txt` file:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The dashboard uses Streamlit, pandas, Plotly, SQLAlchemy, and python-dotenv. The ingestion workflow also requires an HTTP client and a PostgreSQL driver as declared by the project dependencies.

### 5. Configure PostgreSQL

Create a PostgreSQL database and a database user for the project, or use an existing development database. The local development configuration used the database name `health_analytics` and the database user `health_user`.

Create a `.env` file in the project root. Add the connection string for your own PostgreSQL credentials:

```dotenv
DATABASE_URL=postgresql://health_user:YOUR_PASSWORD@localhost:5432/health_analytics
```

Replace `YOUR_PASSWORD` with the password for your local PostgreSQL user. Do not commit `.env` or any file containing a real database password, access token, or other secret.

### 6. Load the data

Run the World Bank ingestion script from the project root:

```bash
python src/health_data_platform/ingest_world_bank.py
```

The workflow retrieves the configured countries and indicators and writes the source and structured data to PostgreSQL. Run ingestion again when you need to refresh the local dataset.

### 7. Start the dashboard

From the project root:

```bash
python -m streamlit run dashboard/app.py
```

Streamlit will print a local address in the terminal and open the dashboard in your browser. The dashboard requires the PostgreSQL service to be running and a valid `DATABASE_URL` in the root `.env` file.

## Data handling and interpretation

- Missing values are retained as `NULL`. The platform does not interpolate or fill missing observations.
- Latest available years are calculated using non-null values only.
- A row existing in the database does not necessarily mean the source published a value for that year.
- Maternal mortality is a modelled estimate, not a direct count of deaths recorded by this platform.
- Current health expenditure is expressed as a share of GDP. A larger percentage means current health expenditure represents a larger share of the economy. It does not, by itself, establish that services are better, that access has improved, or that government spending alone has increased.
- Indicator definitions and units should be checked against the World Bank source metadata before reusing or citing values.

## Limitations

This is an exploratory data platform covering eight selected countries and four indicators. Data availability varies by indicator and country. Some measures are modelled estimates, and national-level indicators do not describe every local or subnational difference. The dashboard is intended for data exploration and descriptive comparison. It should not be used on its own to make causal claims or clinical decisions.

## Security

Keep credentials and local configuration out of version control. At minimum, ensure that `.env`, virtual environments, Python cache files, local exports, and database dumps are excluded from Git. Commit an `.env.example` containing placeholders only, never working credentials.

## Acknowledgement

Indicator data are sourced from the World Bank. Refer to the World Bank indicator metadata and data-use terms when using the underlying statistics in reports, publications, or other applications.
