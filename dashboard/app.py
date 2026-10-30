
from pathlib import Path
import base64
import html
import json
import os
from textwrap import dedent

import pandas as pd
import plotly.express as px
import streamlit as st

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


# =========================================================
# CONFIGURATION
# =========================================================

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

BG = "#FBF7F5"
SURFACE = "#FFFFFF"
ACCENT = "#FF4500"
INK = "#271111"
MUTED = "#6B5A53"
LINE = "#EEE3DE"

CHART_COLORS = [
    "#FF4500",
    "#271111",
    "#C65D35",
    "#80665B",
    "#D99B82",
    "#A63D00",
    "#8C7870",
    "#D9B7A8",
]


# Plain-language descriptions and interpretation rules for each indicator.
INDICATOR_GUIDANCE = {
    "SH.XPD.CHEX.GD.ZS": {
        "meaning": (
            "Current health expenditure is the value of healthcare goods "
            "and services consumed during a year, expressed as a share of "
            "the country's Gross Domestic Product (GDP). It covers current "
            "health spending across the health system, not only the "
            "government health budget."
        ),
        "example": (
            "For example, 4.19% means that current health expenditure was "
            "equivalent to about 4.19 units for every 100 units of GDP "
            "generated in that year."
        ),
        "interpretation": (
            "A higher percentage means health spending represents a larger "
            "share of the economy. It does not by itself mean that services "
            "are better, that access has improved, or that government "
            "spending alone has increased. The ratio can rise because health "
            "spending increases, GDP falls, or health spending grows faster "
            "than GDP."
        ),
        "direction": "neutral",
        "unit_label": "percentage points",
    },
    "SH.STA.MMRT": {
        "meaning": (
            "The estimated number of maternal deaths per 100,000 live births. "
            "It is a modelled population-level estimate, not a count of "
            "deaths recorded in the dashboard."
        ),
        "example": (
            "A value of 500 represents an estimated 500 maternal deaths "
            "for every 100,000 live births."
        ),
        "interpretation": (
            "A decline is generally favourable. Changes in this indicator "
            "should be interpreted alongside the uncertainty and methods "
            "used in the underlying estimates."
        ),
        "direction": "lower",
        "unit_label": "deaths per 100,000 live births",
    },
    "SH.DYN.MORT": {
        "meaning": (
            "The probability of a newborn dying before reaching age five, "
            "expressed per 1,000 live births under the age-specific "
            "mortality conditions of the reported year."
        ),
        "example": (
            "A value of 50 means an estimated 50 children would die before "
            "age five for every 1,000 live births, under the measure's "
            "definition."
        ),
        "interpretation": (
            "A decline is generally favourable because it indicates lower "
            "under-five mortality."
        ),
        "direction": "lower",
        "unit_label": "deaths per 1,000 live births",
    },
    "SH.IMM.IDPT": {
        "meaning": (
            "The percentage of children aged 12–23 months who received "
            "the recommended three doses of diphtheria, pertussis and "
            "tetanus (DPT) vaccine."
        ),
        "example": (
            "A value of 67% means that an estimated 67 out of every "
            "100 children in the indicator's target age group received "
            "the three DPT doses."
        ),
        "interpretation": (
            "Higher coverage is generally favourable. This indicator does "
            "not, on its own, show whether doses were given on time or "
            "whether coverage is equitable across districts and communities."
        ),
        "direction": "higher",
        "unit_label": "percentage points",
    },
}

st.set_page_config(
    page_title="Health Observatory",
    page_icon="H",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =========================================================
# LOAD PORTFOLIO THEME
# =========================================================

css_path = ROOT_DIR / "dashboard" / "theme.css"

if css_path.exists():
    css = css_path.read_text(encoding="utf-8")
    st.markdown(
        f"<style>\n{css}\n</style>",
        unsafe_allow_html=True,
    )


# =========================================================
# DATABASE ACCESS
# =========================================================

@st.cache_resource
def get_engine(database_url: str):
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
    )


@st.cache_data(ttl=300)
def load_filters(database_url: str):
    with get_engine(database_url).connect() as connection:
        countries = pd.read_sql(
            text(
                """
                SELECT
                    country_code,
                    MAX(country_name) AS country_name
                FROM health_observations
                WHERE value IS NOT NULL
                GROUP BY country_code
                ORDER BY country_name
                """
            ),
            connection,
        )

        indicators = pd.read_sql(
            text(
                """
                SELECT
                    indicator_code,
                    MAX(indicator_name) AS indicator_name
                FROM health_observations
                WHERE value IS NOT NULL
                GROUP BY indicator_code
                ORDER BY indicator_name
                """
            ),
            connection,
        )

    return countries, indicators


@st.cache_data(ttl=300)
def load_observations(database_url: str, indicator_code: str):
    with get_engine(database_url).connect() as connection:
        return pd.read_sql(
            text(
                """
                SELECT
                    country_code,
                    country_name,
                    indicator_code,
                    indicator_name,
                    year,
                    value,
                    unit,
                    last_ingested_at
                FROM health_observations
                WHERE indicator_code = :indicator_code
                  AND value IS NOT NULL
                ORDER BY year, country_name
                """
            ),
            connection,
            params={"indicator_code": indicator_code},
        )


@st.cache_data(ttl=300)
def load_data_coverage(database_url: str) -> pd.DataFrame:
    """Return database-wide completeness and latest non-missing year by indicator."""
    query = text(
        """
        SELECT
            indicator_code,
            MAX(indicator_name) AS indicator_name,
            COUNT(*) AS total_records,
            COUNT(value) AS available_observations,
            COUNT(*) - COUNT(value) AS missing_observations,
            ROUND(
                100.0 * COUNT(value) / NULLIF(COUNT(*), 0),
                2
            ) AS completeness_percentage,
            MAX(year) FILTER (WHERE value IS NOT NULL)
                AS latest_available_year,
            COUNT(DISTINCT country_code) AS countries_covered
        FROM health_observations
        GROUP BY indicator_code
        ORDER BY indicator_code
        """
    )

    with get_engine(database_url).connect() as connection:
        return pd.read_sql(query, connection)


# =========================================================
# PRESENTATION HELPERS
# =========================================================

def metric_card(label: str, value: str, note: str) -> str:
    """Return one compact HTML card without Markdown-sensitive indentation."""
    return (
        '<div class="metric-card">'
        f'<div class="metric-label">{html.escape(label)}</div>'
        f'<div class="metric-value">{html.escape(value)}</div>'
        f'<div class="metric-note">{html.escape(note)}</div>'
        '</div>'
    )


def render_sidebar_reopen_button() -> None:
    """Render a compact reopen button when Streamlit's sidebar is collapsed.

    Streamlit 1.65+ exposes the collapsed state as data-collapsed and uses
    stExpandSidebarButton in the page header. Older releases use different
    test IDs, so the click handler supports both generations.
    """
    st.markdown(
        """
        <style>
        .st-key-sidebar_reopen_control {
            position: fixed !important;
            top: 12px !important;
            left: 8px !important;
            z-index: 2147483000 !important;
            width: 34px !important;
            height: 36px !important;
            margin: 0 !important;
            padding: 0 !important;
            background: transparent !important;
            border: 0 !important;
            pointer-events: none !important;
        }
        .st-key-sidebar_reopen_control [data-testid="stIFrame"],
        .st-key-sidebar_reopen_control iframe {
            display: block !important;
            width: 34px !important;
            min-width: 34px !important;
            height: 36px !important;
            min-height: 36px !important;
            border: 0 !important;
            pointer-events: auto !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    toggle_document = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    * { box-sizing: border-box; }
    html, body {
        width: 34px;
        height: 36px;
        margin: 0;
        padding: 0;
        overflow: hidden;
        background: transparent;
        font-family: Arial, sans-serif;
    }
    #open-sidebar {
        display: none;
        align-items: center;
        justify-content: center;
        width: 34px;
        height: 36px;
        padding: 0;
        border: 0;
        border-radius: 8px;
        background: transparent;
        color: #78665F;
        box-shadow: none;
        cursor: pointer;
    }
    #open-sidebar:hover {
        background: #F5EDE9;
        color: #FF4500;
    }
    #open-sidebar:focus-visible {
        outline: 2px solid #FF4500;
        outline-offset: 2px;
    }
    svg { width: 21px; height: 21px; }
</style>
</head>
<body>
<button id="open-sidebar" type="button" aria-label="Open dashboard filters" title="Open dashboard filters">
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M5 5L12 12L5 19M12 5L19 12L12 19" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
</button>
<script>
(() => {
    const parentDocument = window.parent.document;
    const button = document.getElementById("open-sidebar");
    const frame = window.frameElement;

    function findSidebar() {
        return parentDocument.querySelector('[data-testid="stSidebar"]');
    }

    function isSidebarCollapsed() {
        const sidebar = findSidebar();
        if (!sidebar) return false;

        // Streamlit 1.65+ moved the state to data-collapsed on the sidebar.
        const collapsedState = sidebar.getAttribute("data-collapsed");
        if (collapsedState !== null) {
            return collapsedState === "true";
        }

        // Compatibility with earlier Streamlit versions.
        const expandedState = sidebar.getAttribute("aria-expanded");
        if (expandedState !== null) {
            return expandedState === "false";
        }

        // Last-resort fallback for DOM variants without either state attribute.
        return sidebar.getBoundingClientRect().width < 24;
    }

    function updateVisibility() {
        const collapsed = isSidebarCollapsed();
        button.style.display = collapsed ? "flex" : "none";
        if (frame) {
            frame.style.display = collapsed ? "block" : "none";
        }
    }

    function getControl(testId) {
        const element = parentDocument.querySelector(`[data-testid="${testId}"]`);
        if (!element) return null;
        if (element.matches("button")) return element;
        return element.querySelector("button") || element;
    }

    button.addEventListener("click", () => {
        // Streamlit 1.65+ mounts a dedicated expand control in the page header.
        // Older versions expose a collapsed-control wrapper or keep the toggle
        // button inside the sidebar.
        const nativeButton =
            getControl("stExpandSidebarButton") ||
            getControl("stSidebarCollapsedControl") ||
            getControl("collapsedControl") ||
            getControl("stSidebarCollapseButton") ||
            parentDocument.querySelector(
                'button[aria-label="Expand sidebar"], button[title="Expand sidebar"], ' +
                'button[aria-label="Open sidebar"], button[title="Open sidebar"]'
            );

        if (nativeButton) {
            nativeButton.click();
        }
    });

    const observer = new MutationObserver(updateVisibility);
    observer.observe(parentDocument.body, {
        attributes: true,
        attributeFilter: ["aria-expanded", "data-collapsed"],
        childList: true,
        subtree: true
    });
    updateVisibility();
})();
</script>
</body>
</html>"""

    with st.container(key="sidebar_reopen_control"):
        st.iframe(
            toggle_document,
            width=34,
            height=36,
            alt="Reopen dashboard filters when the sidebar is collapsed",
        )

render_sidebar_reopen_button()


def style_chart(fig, height: int = 480):
    fig.update_layout(
        height=height,
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        
        font=dict(
            family="Instrument Sans, sans-serif",
            size=12,
            color="#271111",
        ),

        # Give axis titles and tick labels enough room.
        margin=dict(
            l=82,
            r=30,
            t=25,
            b=78,
            pad=4,
        ),

        hoverlabel=dict(
            bgcolor="#F5EDE9",
            bordercolor="#E2D3CC",
            font=dict(
                family="Instrument Sans, sans-serif",
                color="#271111",
                size=12,
            ),
        ),

        xaxis=dict(
            title=dict(
                standoff=20,
                font=dict(
                    family="Instrument Sans, sans-serif",
                    size=13,
                    color="#271111",
                ),
            ),
            automargin=True,
            showgrid=False,
            zeroline=False,
            linecolor="#EEE3DE",
            tickfont=dict(
                family="Instrument Sans, sans-serif",
                color="#78665F",
                size=12,
            ),
        ),

        yaxis=dict(
            title=dict(
                standoff=24,
                font=dict(
                    family="Instrument Sans, sans-serif",
                    size=13,
                    color="#271111",
                ),
            ),
            automargin=True,
            showgrid=True,
            gridcolor="#EEE3DE",
            zeroline=False,
            linecolor="#EEE3DE",
            tickfont=dict(
                family="Instrument Sans, sans-serif",
                color="#78665F",
                size=12,
            ),
        ),
    )

    return fig



def render_centered_hover_chart(
    fig,
    value_suffix: str = "",
    height: int = 430,
):
    """
    Render a Plotly chart with a custom theme-matched hover card.

    The card displays only the country name, year, and value.
    Plotly's native hover template is removed to prevent a
    second tooltip from appearing underneath the custom card.
    """

    # Important: Plotly Express creates hover templates automatically.
    # Remove those templates and suppress native hover labels.
    fig.update_traces(
        hovertemplate=None,
        hoverinfo="none",
    )

    fig.update_layout(
        hovermode="closest",
        showlegend=False,
        height=height,
    )

    figure_b64 = base64.b64encode(
        fig.to_json().encode("utf-8")
    ).decode("ascii")

    suffix_json = json.dumps(value_suffix)

    document = r"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">

        <link
            rel="stylesheet"
            href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@500;600;700&family=Instrument+Sans:wght@400;500;600;700&display=swap"
        >

        <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>

        <style>
            * {
                box-sizing: border-box;
            }

            html, body {
                margin: 0;
                padding: 0;
                background: transparent;
                font-family: "Instrument Sans", sans-serif;
            }

            .chart-wrap {
                position: relative;
                width: 100%;
                height: __HEIGHT__px;
                overflow: hidden;
                border-radius: 8px;
                background: #FFFFFF;
            }

            #health-chart {
                width: 100%;
                height: 100%;
            }

            .hover-card {
                position: absolute;
                display: none;
                z-index: 1000;
                width: 178px;
                padding: 19px 16px 18px;
                border: 1px solid #E2D3CC;
                border-top: 3px solid #FF4500;
                border-radius: 13px;
                background: #FBF7F5;
                box-shadow: 0 8px 26px rgba(39, 17, 17, 0.14);
                text-align: center;
                pointer-events: none;
            }

            .hover-country {
                color: #FF4500;
                font-family: "Bricolage Grotesque", sans-serif;
                font-size: 21px;
                font-weight: 700;
                line-height: 1.25;
                overflow-wrap: anywhere;
            }

            .hover-divider {
                height: 1px;
                margin: 13px 0;
                background: #E2D3CC;
            }

            .hover-year {
                color: #78665F;
                font-family: "Instrument Sans", sans-serif;
                font-size: 13px;
                font-weight: 500;
                line-height: 1.4;
            }

            .hover-value {
                margin-top: 5px;
                color: #271111;
                font-family: "Bricolage Grotesque", sans-serif;
                font-size: 19px;
                font-weight: 600;
                line-height: 1.3;
                overflow-wrap: anywhere;
            }
        </style>
    </head>

    <body>
        <div class="chart-wrap" id="chart-wrap">
            <div id="health-chart"></div>

            <div class="hover-card" id="hover-card">
                <div
                    class="hover-country"
                    id="hover-country"
                ></div>

                <div class="hover-divider"></div>

                <div
                    class="hover-year"
                    id="hover-year"
                ></div>

                <div
                    class="hover-value"
                    id="hover-value"
                ></div>
            </div>
        </div>

        <script>
            const figure = JSON.parse(atob("__FIGURE_B64__"));
            const suffix = __VALUE_SUFFIX__;

            const wrapper = document.getElementById("chart-wrap");
            const graph = document.getElementById("health-chart");
            const card = document.getElementById("hover-card");

            const countryElement =
                document.getElementById("hover-country");

            const yearElement =
                document.getElementById("hover-year");

            const valueElement =
                document.getElementById("hover-value");


            function positionCard(event) {
                const rect = wrapper.getBoundingClientRect();

                let left = event
                    ? event.clientX - rect.left + 14
                    : 12;

                let top = event
                    ? event.clientY - rect.top + 14
                    : 12;

                const maxLeft =
                    wrapper.clientWidth - card.offsetWidth - 8;

                const maxTop =
                    wrapper.clientHeight - card.offsetHeight - 8;

                if (left > maxLeft) {
                    left -= card.offsetWidth + 28;
                }

                if (top > maxTop) {
                    top -= card.offsetHeight + 28;
                }

                card.style.left =
                    `${Math.max(8, Math.min(left, maxLeft))}px`;

                card.style.top =
                    `${Math.max(8, Math.min(top, maxTop))}px`;
            }


            Plotly.newPlot(
                graph,
                figure.data,
                figure.layout,
                {
                    displayModeBar: false,
                    responsive: true,
                    scrollZoom: false
                }
            ).then(() => {

                graph.on("plotly_hover", (eventData) => {
                    const point =
                        eventData.points && eventData.points[0];

                    if (!point) {
                        return;
                    }

                    const custom = point.customdata;

                    const country =
                        Array.isArray(custom) && custom.length
                            ? custom[0]
                            : (
                                point.data.name ||
                                point.data.legendgroup ||
                                ""
                            );

                    countryElement.textContent = String(country);

                    yearElement.textContent = String(
                        Math.round(Number(point.x))
                    );

                    const value = Number(point.y);

                    valueElement.textContent =
                        Number.isFinite(value)
                            ? value.toLocaleString(undefined, {
                                minimumFractionDigits: 2,
                                maximumFractionDigits: 2
                            }) + suffix
                            : "—";

                    card.style.display = "block";

                    positionCard(eventData.event);
                });


                graph.on("plotly_unhover", () => {
                    card.style.display = "none";
                });


                // Close the card when the pointer leaves the chart.
                wrapper.addEventListener("mouseleave", () => {
                    card.style.display = "none";
                });

            });
        </script>
    </body>
    </html>
    """

    document = (
        document
        .replace("__HEIGHT__", str(height))
        .replace("__FIGURE_B64__", figure_b64)
        .replace("__VALUE_SUFFIX__", suffix_json)
    )

    st.iframe(
        document,
        width="stretch",
        height=height,
        alt="Interactive public health indicator trend chart",
    )


def section_header(
    kicker: str,
    title: str,
    description: str = "",
):
    markup = dedent(
        f"""
        <div class="section-header">
            <div class="section-kicker">{html.escape(kicker)}</div>
            <div class="section-title">{html.escape(title)}</div>
            <div class="section-description">
                {html.escape(description)}
            </div>
        </div>
        """
    ).strip()

    st.markdown(markup, unsafe_allow_html=True)



def render_findings_report(
    data: pd.DataFrame,
    indicator_code: str,
    indicator_name: str,
    unit: str,
    selected_years: tuple[int, int],
) -> None:
    """
    Generate a plain-language descriptive report from the rows currently
    selected in the dashboard. The report never fills missing years.
    """

    if data.empty:
        st.info("There are no observations available for this report.")
        return

    guidance = INDICATOR_GUIDANCE.get(
        indicator_code,
        {
            "meaning": (
                "This indicator records a health-related measure. "
                "Use its definition and unit when interpreting the values."
            ),
            "example": "",
            "interpretation": (
                "The direction and size of change should be interpreted "
                "in the context of the indicator's definition."
            ),
            "direction": "neutral",
            "unit_label": unit,
        },
    )

    ordered = data.sort_values(["country_code", "year"]).copy()

    country_summary = (
        ordered.groupby(
            ["country_code", "country_name"],
            as_index=False,
            sort=True,
        )
        .agg(
            first_year=("year", "first"),
            first_value=("value", "first"),
            latest_year=("year", "last"),
            latest_value=("value", "last"),
            years_with_data=("year", "nunique"),
            observations=("value", "count"),
        )
    )

    # Compare a country's first and latest non-missing observations only
    # when they come from at least two different years.
    comparable = country_summary[
        country_summary["years_with_data"] >= 2
    ].copy()

    comparable["change"] = (
        comparable["latest_value"] - comparable["first_value"]
    )

    tolerance = 1e-9
    increase_count = int((comparable["change"] > tolerance).sum())
    decrease_count = int((comparable["change"] < -tolerance).sum())
    unchanged_count = int(
        (comparable["change"].abs() <= tolerance).sum()
    )

    total_countries = int(country_summary["country_code"].nunique())
    total_observations = int(len(data))
    actual_start = int(data["year"].min())
    actual_end = int(data["year"].max())

    def fmt(value: float) -> str:
        return f"{float(value):,.2f}"

    def fmt_change(value: float) -> str:
        suffix = guidance.get("unit_label", unit)
        if suffix == "percentage points":
            return f"{float(value):+.2f} pp"
        return f"{float(value):+.2f}"

    # Determine movement without assigning a value judgement to
    # indicators where higher or lower is not inherently better.
    largest_increase = None
    largest_decrease = None

    if not comparable.empty:
        positive = comparable[comparable["change"] > tolerance]
        negative = comparable[comparable["change"] < -tolerance]

        if not positive.empty:
            largest_increase = positive.loc[positive["change"].idxmax()]

        if not negative.empty:
            largest_decrease = negative.loc[negative["change"].idxmin()]

    # Latest row per country. The reporting year is kept visible because
    # the latest available year can differ between countries.
    latest_by_country = (
        ordered.sort_values(["country_code", "year"])
        .groupby("country_code", as_index=False)
        .tail(1)
        .sort_values("country_name")
    )

    with st.container(border=True):
        section_header(
            "FINDINGS & INTERPRETATION",
            "What does the data tell us?",
            (
                f"{indicator_name} · selected reporting window "
                f"{selected_years[0]}–{selected_years[1]}. "
                "The findings below are calculated from the observations "
                "currently loaded in the database."
            ),
        )

        k1, k2, k3 = st.columns(3)

        k1.metric("Countries represented", total_countries)
        k2.metric("Latest year in this selection", actual_end)
        k3.metric("Observations analysed", f"{total_observations:,}")

        st.divider()

        # 1. Plain-language meaning of the indicator.
        st.markdown("#### 1. What is being measured?")
        st.write(guidance["meaning"])

        if guidance.get("example"):
            st.info(guidance["example"])

        # Specific explanation of current health expenditure.
        if indicator_code == "SH.XPD.CHEX.GD.ZS":
            st.markdown(
                """
                **What counts as current health expenditure?**

                It includes healthcare goods and services consumed during
                the year, such as consultations, hospital care, medicines,
                diagnostic services and other current healthcare activity.
                It is not limited to public spending. It excludes capital
                expenditure such as constructing hospitals, major
                machinery, health IT infrastructure and vaccine stocks
                held for emergencies or outbreaks.
                """
            )

            st.markdown(
                "**How should the percentage be read?**"
            )
            st.write(
                "The percentage compares current health expenditure with "
                "the value of all goods and services produced in the "
                "economy during the same year (GDP). It is a measure of "
                "health spending relative to the size of the economy, "
                "not a measure of the amount spent per person."
            )

        st.divider()

        # 2. Overall movement in comparable countries.
        st.markdown("#### 2. Overall trend")

        if comparable.empty:
            st.write(
                "There are not enough observations from different years "
                "to compare a starting and latest value for the selected "
                "country or countries."
            )
        else:
            st.write(
                f"Among the **{len(comparable)}** countries with observations "
                f"in at least two different years, the indicator increased "
                f"in **{increase_count}**, decreased in **{decrease_count}**, "
                f"and had no net change in **{unchanged_count}** countries. "
                "Each comparison uses that country's first and latest "
                "available observations inside the selected reporting window."
            )

            if guidance["direction"] == "lower":
                st.write(
                    "For this indicator, a lower value is generally the "
                    "favourable direction. The trend counts describe movement, "
                    "while the indicator definition explains its meaning."
                )
            elif guidance["direction"] == "higher":
                st.write(
                    "For this indicator, a higher value is generally the "
                    "favourable direction. The trend counts describe movement, "
                    "while the indicator definition explains its meaning."
                )
            else:
                st.write(guidance["interpretation"])

        # 3. Country-level changes.
        st.markdown("#### 3. Country-by-country changes")

        if comparable.empty:
            st.caption(
                "A country needs observations from at least two distinct "
                "years before a change can be calculated."
            )
        else:
            trend_table = comparable[
                [
                    "country_name",
                    "first_year",
                    "first_value",
                    "latest_year",
                    "latest_value",
                    "change",
                ]
            ].copy()

            trend_table["Change"] = trend_table["change"].apply(fmt_change)
            trend_table = trend_table.rename(
                columns={
                    "country_name": "Country",
                    "first_year": "First year",
                    "first_value": "First value",
                    "latest_year": "Latest year",
                    "latest_value": "Latest value",
                }
            )

            trend_table = trend_table[
                [
                    "Country",
                    "First year",
                    "First value",
                    "Latest year",
                    "Latest value",
                    "Change",
                ]
            ]

            st.dataframe(
                trend_table,
                hide_index=True,
                width="stretch",
                column_config={
                    "First value": st.column_config.NumberColumn(
                        "First value",
                        format="%.2f",
                    ),
                    "Latest value": st.column_config.NumberColumn(
                        "Latest value",
                        format="%.2f",
                    ),
                },
            )

            # Present the largest movements in a single information panel,
            # matching the blue notice used elsewhere in the dashboard.
            if largest_increase is not None:
                increase_message = (
                    f"{largest_increase['country_name']}: "
                    f"{fmt(largest_increase['first_value'])} "
                    f"({int(largest_increase['first_year'])}) to "
                    f"{fmt(largest_increase['latest_value'])} "
                    f"({int(largest_increase['latest_year'])}), "
                    f"a change of {fmt_change(largest_increase['change'])}."
                )
            else:
                increase_message = "No country recorded an increase."

            if largest_decrease is not None:
                decrease_message = (
                    f"{largest_decrease['country_name']}: "
                    f"{fmt(largest_decrease['first_value'])} "
                    f"({int(largest_decrease['first_year'])}) to "
                    f"{fmt(largest_decrease['latest_value'])} "
                    f"({int(largest_decrease['latest_year'])}), "
                    f"a change of {fmt_change(largest_decrease['change'])}."
                )
            else:
                decrease_message = "No country recorded a decrease."

            st.markdown(
                """
                <style>
                .movement-summary-panel {
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 24px;
                    width: 100%;
                    box-sizing: border-box;
                    margin: 16px 0 4px;
                    padding: 20px 22px;
                    border-radius: 12px;
                    background: #E5EDF9;
                    color: #075EB3;
                }
                .movement-summary-panel h4 {
                    margin: 0 0 8px;
                    padding: 0;
                    color: inherit;
                    font-size: 1.05rem;
                    font-weight: 650;
                    line-height: 1.35;
                }
                .movement-summary-panel p {
                    margin: 0;
                    color: inherit;
                    font-size: 1.08rem;
                    line-height: 1.55;
                }
                @media (max-width: 768px) {
                    .movement-summary-panel {
                        grid-template-columns: minmax(0, 1fr);
                        gap: 16px;
                        padding: 18px;
                    }
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            st.markdown(
                f"""
                <div class="movement-summary-panel" role="note" aria-label="Largest country-level changes">
                    <section>
                        <h4>Largest increase</h4>
                        <p>{html.escape(increase_message)}</p>
                    </section>
                    <section>
                        <h4>Largest decrease</h4>
                        <p>{html.escape(decrease_message)}</p>
                    </section>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.divider()

        # 4. Latest available year for each country.
        st.markdown("#### 4. Latest available data by country")
        st.write(
            "The latest year is not necessarily the same for every country. "
            "This table shows the latest non-missing observation currently "
            "stored for each country."
        )

        latest_table = latest_by_country[
            ["country_name", "year", "value"]
        ].rename(
            columns={
                "country_name": "Country",
                "year": "Latest year",
                "value": "Latest value",
            }
        )

        st.dataframe(
            latest_table,
            hide_index=True,
            width="stretch",
            column_config={
                "Latest value": st.column_config.NumberColumn(
                    "Latest value",
                    format="%.2f",
                ),
            },
        )

        # 5. Meaning, caution and limitations.
        st.divider()
        st.markdown("#### 5. What should the reader take away?")
        st.write(guidance["interpretation"])

        st.markdown("**Important limitations**")
        st.write(
            f"The selected observations cover {actual_start} to {actual_end}. "
            "Missing years are not interpolated or estimated by this "
            "dashboard. First-to-latest comparisons can span different "
            "lengths of time across countries, and latest-value comparisons "
            "may refer to different reporting years. These are descriptive "
            "findings. They do not establish why a change occurred or prove "
            "that one factor caused another."
        )

        st.caption(
            "Source: World Bank Indicators API. "
            "Use the source indicator metadata when citing or reusing "
            "the underlying statistics."
        )


def render_data_coverage_report(database_url: str) -> None:
    """Show database-wide record completeness, missingness and recency."""
    try:
        coverage = load_data_coverage(database_url)
    except SQLAlchemyError as exc:
        st.warning("Data coverage could not be calculated from PostgreSQL.")
        print(f"Coverage query failed: {type(exc).__name__}")
        return

    if coverage.empty:
        st.info("No indicator records are available for a coverage assessment.")
        return

    numeric_columns = [
        "total_records",
        "available_observations",
        "missing_observations",
        "completeness_percentage",
        "countries_covered",
    ]
    for column in numeric_columns:
        coverage[column] = pd.to_numeric(coverage[column], errors="coerce")

    total_records = int(coverage["total_records"].sum())
    available_records = int(coverage["available_observations"].sum())
    missing_records = int(coverage["missing_observations"].sum())
    overall_completeness = (
        available_records / total_records * 100
        if total_records
        else 0.0
    )
    country_count = int(coverage["countries_covered"].max())
    indicator_count = int(coverage["indicator_code"].nunique())

    coverage = coverage.sort_values(
        "completeness_percentage",
        ascending=True,
    ).copy()

    with st.container(border=True):
        section_header(
            "DATA COVERAGE & RELIABILITY",
            "How complete is the dataset?",
            (
                f"Database-wide assessment across {country_count} countries and "
                f"{indicator_count} indicators. These figures describe all stored "
                "country-indicator-year records and are independent of the filters above."
            ),
        )

        # Build the KPI grid as one continuous HTML string. This avoids an
        # indented closing tag being interpreted by Markdown as a code block.
        summary_cards = "".join(
            [
                metric_card(
                    "Stored records",
                    f"{total_records:,}",
                    "Country-indicator-year rows in PostgreSQL",
                ),
                metric_card(
                    "Available observations",
                    f"{available_records:,}",
                    "Records with a non-missing value",
                ),
                metric_card(
                    "Missing observations",
                    f"{missing_records:,}",
                    "Records where the value is NULL",
                ),
                metric_card(
                    "Overall completeness",
                    f"{overall_completeness:.2f}%",
                    "Available observations divided by stored records",
                ),
            ]
        )
        st.markdown(
            '<div class="metric-grid">' + summary_cards + '</div>',
            unsafe_allow_html=True,
        )

        st.markdown("#### Completeness by indicator")

        # Use concise labels in the chart so they remain readable at different
        # viewport widths. The table below retains the full indicator names.
        chart_labels = {
            "SH.DYN.MORT": "Under-5 mortality",
            "SH.IMM.IDPT": "DPT immunisation",
            "SH.STA.MMRT": "Maternal mortality",
            "SH.XPD.CHEX.GD.ZS": "Health expenditure",
        }
        chart_df = coverage.copy()
        chart_df["chart_label"] = chart_df["indicator_code"].map(chart_labels).fillna(
            chart_df["indicator_name"]
        )
        chart_df = chart_df.sort_values(
            "completeness_percentage",
            ascending=True,
        )

        fig = px.bar(
            chart_df,
            x="completeness_percentage",
            y="chart_label",
            orientation="h",
            text="completeness_percentage",
            color_discrete_sequence=[ACCENT],
            labels={
                "completeness_percentage": "Complete records (%)",
                "chart_label": "Indicator",
            },
        )

        fig.update_traces(
            texttemplate="%{x:.2f}%",
            textposition="outside",
            cliponaxis=False,
            marker_line_width=0,
        )

        fig.update_layout(
            height=300,
            paper_bgcolor=SURFACE,
            plot_bgcolor=SURFACE,
            font=dict(
                family="Instrument Sans, sans-serif",
                size=12,
                color=INK,
            ),
            showlegend=False,
            margin=dict(l=24, r=76, t=12, b=48),
            xaxis=dict(
                range=[0, 112],
                title="Records with values (%)",
                ticksuffix="%",
                showgrid=True,
                gridcolor=LINE,
                zeroline=False,
            ),
            yaxis=dict(
                title=None,
                showgrid=False,
                automargin=True,
            ),
        )

        st.plotly_chart(
            fig,
            width="stretch",
            config={"displayModeBar": False, "responsive": True},
        )

        display = coverage.copy()
        display["Completeness"] = display[
            "completeness_percentage"
        ].map(lambda value: f"{float(value):.2f}%")
        display["Latest available year"] = display[
            "latest_available_year"
        ].apply(
            lambda value: (
                str(int(value)) if pd.notna(value) else "No non-missing data"
            )
        )
        display = display.rename(
            columns={
                "indicator_name": "Indicator",
                "total_records": "Stored",
                "available_observations": "Available",
                "missing_observations": "Missing",
            }
        )
        display = display[
            [
                "Indicator",
                "Stored",
                "Available",
                "Missing",
                "Completeness",
                "Latest available year",
            ]
        ]

        st.dataframe(
            display,
            hide_index=True,
            width="stretch",
            height=190,
            column_config={
                "Indicator": st.column_config.TextColumn(
                    "Indicator", width="large"
                ),
                "Stored": st.column_config.NumberColumn(
                    "Stored", format="%d", width="small"
                ),
                "Available": st.column_config.NumberColumn(
                    "Available", format="%d", width="small"
                ),
                "Missing": st.column_config.NumberColumn(
                    "Missing", format="%d", width="small"
                ),
                "Completeness": st.column_config.TextColumn(
                    "Completeness", width="small"
                ),
                "Latest available year": st.column_config.TextColumn(
                    "Latest available year", width="medium"
                ),
            },
        )

        # Give the reader a plain-language summary using the actual query results.
        best = coverage.loc[coverage["completeness_percentage"].idxmax()]
        weakest = coverage.loc[coverage["completeness_percentage"].idxmin()]

        st.markdown("#### What the coverage figures mean")
        st.write(
            f"Across the full dataset, **{available_records:,} of {total_records:,} "
            f"records ({overall_completeness:.2f}%)** contain a value. "
            f"**{best['indicator_name']}** has the highest completeness "
            f"({float(best['completeness_percentage']):.2f}%), while "
            f"**{weakest['indicator_name']}** has the lowest "
            f"({float(weakest['completeness_percentage']):.2f}%)."
        )

        st.write(
            "Completeness measures how many stored records contain a value. "
            "It does not measure whether a reported value is accurate, how "
            "precise an estimate is, or whether the data represent every "
            "community within a country."
        )

        year_labels = {
            "SH.DYN.MORT": "under-5 mortality",
            "SH.IMM.IDPT": "DPT immunisation",
            "SH.STA.MMRT": "maternal mortality",
            "SH.XPD.CHEX.GD.ZS": "current health expenditure",
        }
        latest_year_notes = []
        for _, row in coverage.iterrows():
            label = year_labels.get(row["indicator_code"], row["indicator_name"])
            latest = row["latest_available_year"]
            year_text = str(int(latest)) if pd.notna(latest) else "not available"
            latest_year_notes.append(f"{label}: {year_text}")

        st.info(
            "The latest available year is calculated using non-missing values only. "
            "A row with a NULL value is retained in the observation table but is "
            "not treated as a published observation. Latest years in this dataset: "
            + "; ".join(latest_year_notes)
            + "."
        )

        st.caption(
            "Coverage is calculated from health_observations at the time the report loads. "
            "The report is refreshed using the dashboard's data refresh control or after "
            "the five-minute cache expires."
        )


# =========================================================
# DATABASE STATUS
# =========================================================

if not DATABASE_URL:
    st.error(
        "DATABASE_URL is missing. Check your project's .env file."
    )
    st.stop()

try:
    with get_engine(DATABASE_URL).connect() as connection:
        connection.execute(text("SELECT 1"))

    database_connected = True

except SQLAlchemyError as exc:
    database_connected = False
    print(f"Database connection error: {type(exc).__name__}")

if not database_connected:
    st.error(
        "The dashboard cannot connect to PostgreSQL. "
        "Check that the database service is running and "
        "DATABASE_URL in .env is correct."
    )
    st.stop()


# =========================================================
# BRAND HEADER
# =========================================================

header_left, header_right = st.columns(
    [5, 2],
    vertical_alignment="center",
)

with header_left:
    brand_markup = dedent(
        """
        <div class="brand-lockup">
            <div class="brand-mark">PH</div>
            <div>
                <div class="brand-overline">PUBLIC HEALTH / DATA</div>
                <div class="brand-name">Health Observatory</div>
            </div>
        </div>
        """
    ).strip()

    st.markdown(
        brand_markup,
        unsafe_allow_html=True,
    )

with header_right:
    status_markup = dedent(
        """
        <div class="status-pill connected">
            <span class="status-dot"></span>
            POSTGRESQL CONNECTED
        </div>
        """
    ).strip()

    st.markdown(
        status_markup,
        unsafe_allow_html=True,
    )

hero_markup = dedent(
    """
    <div class="hero-block">
        <div class="hero-copy">
            <div class="hero-kicker">AFRICA · INDICATOR MONITOR</div>
            <h1>Health, seen over time.</h1>
            <p>
                Explore public health indicators across countries.
                Compare trends, examine reporting years, and inspect
                the observations behind each chart.
            </p>
        </div>
        <div class="hero-aside">
            <div class="hero-aside-number">04</div>
            <div class="hero-aside-label">
                INDICATORS<br/>IN THIS DATASET
            </div>
        </div>
    </div>
    """
).strip()

st.markdown(
    hero_markup,
    unsafe_allow_html=True,
)


# =========================================================
# LOAD FILTER OPTIONS
# =========================================================

try:
    countries, indicators = load_filters(DATABASE_URL)

except SQLAlchemyError as exc:
    st.error("Could not load available countries and indicators.")
    print(f"Filter query failed: {type(exc).__name__}")
    st.stop()

if countries.empty or indicators.empty:
    st.warning(
        "No non-missing observations were found. "
        "Run the ingestion pipeline first."
    )
    st.stop()


# =========================================================
# SIDEBAR FILTERS
# =========================================================

sidebar_markup = dedent(
    """
    <div class="sidebar-intro">
        <div class="section-kicker">YOUR WORKSPACE</div>
        <div class="sidebar-title">Explore the data</div>
        <div class="sidebar-description">
            Choose an indicator, narrow the geography,
            and select a reporting period.
        </div>
    </div>
    """
).strip()

st.sidebar.markdown(
    sidebar_markup,
    unsafe_allow_html=True,
)

if st.sidebar.button("Refresh data", width="stretch"):
    load_filters.clear()
    load_observations.clear()
    load_data_coverage.clear()
    st.rerun()

country_name_to_code = dict(
    zip(countries["country_name"], countries["country_code"])
)

country_options = [
    "All countries",
    *list(country_name_to_code.keys()),
]

selected_country = st.sidebar.selectbox(
    "Geography",
    options=country_options,
)

indicator_name_to_code = dict(
    zip(
        indicators["indicator_name"],
        indicators["indicator_code"],
    )
)

selected_indicator = st.sidebar.selectbox(
    "Health indicator",
    options=list(indicator_name_to_code.keys()),
)

indicator_code = indicator_name_to_code[selected_indicator]

try:
    full_df = load_observations(
        DATABASE_URL,
        indicator_code,
    )

except SQLAlchemyError as exc:
    st.error("Could not retrieve the selected indicator.")
    print(f"Observation query failed: {type(exc).__name__}")
    st.stop()

if full_df.empty:
    st.info(
        "There are no non-missing observations for this indicator."
    )
    st.stop()

full_df["year"] = pd.to_numeric(
    full_df["year"],
    errors="coerce",
)

full_df["value"] = pd.to_numeric(
    full_df["value"],
    errors="coerce",
)

full_df = full_df.dropna(subset=["year", "value"]).copy()
full_df["year"] = full_df["year"].astype(int)

if full_df.empty:
    st.warning(
        "The selected indicator has no usable numeric observations."
    )
    st.stop()

min_year = int(full_df["year"].min())
max_year = int(full_df["year"].max())

if min_year < max_year:
    selected_years = st.sidebar.slider(
        "Reporting years",
        min_value=min_year,
        max_value=max_year,
        value=(min_year, max_year),
        step=1,
    )
else:
    selected_years = (min_year, max_year)
    st.sidebar.caption(
        f"Available reporting year: {min_year}"
    )

st.sidebar.markdown("---")

sidebar_source = dedent(
    f"""
    <div class="sidebar-source">
        <div class="section-kicker">DATA SOURCE</div>
        <div class="source-name">World Bank Indicators API</div>
        <div class="source-detail">
            {len(indicators)} indicators available in the database.
        </div>
    </div>
    """
).strip()

st.sidebar.markdown(
    sidebar_source,
    unsafe_allow_html=True,
)


# =========================================================
# APPLY FILTERS
# =========================================================

df = full_df.copy()

if selected_country != "All countries":
    chosen_code = country_name_to_code[selected_country]
    df = df[df["country_code"] == chosen_code]

df = df[
    df["year"].between(
        selected_years[0],
        selected_years[1],
    )
].copy()

if df.empty:
    st.warning(
        "No observations match the current filters. "
        "Try a wider year range or another country."
    )
    st.stop()

unit_values = df["unit"].dropna()

unit = (
    str(unit_values.iloc[0])
    if not unit_values.empty
    else "Reported value"
)

number_of_observations = len(df)
number_of_countries = int(df["country_code"].nunique())
latest_year = int(df["year"].max())
number_of_years = int(df["year"].nunique())


# =========================================================
# KPI CARDS
# =========================================================

metrics_markup = dedent(
    f"""
    <div class="metric-grid">
        {metric_card(
            "Observations in view",
            f"{number_of_observations:,}",
            "Non-missing records matching your filters",
        )}
        {metric_card(
            "Countries represented",
            f"{number_of_countries:02d}",
            "Countries with observations in this view",
        )}
        {metric_card(
            "Latest reporting year",
            str(latest_year),
            "Latest year available in the selection",
        )}
        {metric_card(
            "Years represented",
            f"{number_of_years:02d}",
            f"{selected_years[0]} to {selected_years[1]}",
        )}
    </div>
    """
).strip()

st.markdown(
    metrics_markup,
    unsafe_allow_html=True,
)

selection_markup = dedent(
    f"""
    <div class="selection-note">
        <span class="note-accent"></span>
        <span>
            <strong>{html.escape(selected_indicator)}</strong>
            <span class="note-separator">/</span>
            {html.escape(unit)}
        </span>
    </div>
    """
).strip()

st.markdown(
    selection_markup,
    unsafe_allow_html=True,
)

render_findings_report(
    data=df,
    indicator_code=indicator_code,
    indicator_name=selected_indicator,
    unit=unit,
    selected_years=selected_years,
)

render_data_coverage_report(DATABASE_URL)

st.markdown(
    "<div class='section-spacer'></div>",
    unsafe_allow_html=True,
)


# =========================================================
# CHARTS AND DATA EXPLORER
# =========================================================

section_header(
    "INDICATOR ANALYSIS",
    "Explore the numbers",
    "Switch between historical trends, country comparisons, "
    "and the underlying records.",
)

view = st.radio(
    "Choose a view",
    options=[
        "Trends over time",
        "Latest comparison",
        "Data explorer",
    ],
    horizontal=True,
    label_visibility="collapsed",
)


# =========================================================
# TRENDS OVER TIME
# =========================================================

with st.container(border=True):

    if view == "Trends over time":
        st.markdown(
            '<div class="chart-heading">Historical trend</div>',
            unsafe_allow_html=True,
        )

        st.caption(
            "Track how reported values change across the selected years. "
            "Hover over a point to inspect its value."
        )

        if selected_country == "All countries":

            country_names = sorted(
                df["country_name"].dropna().unique().tolist()
            )

            country_colors = {
                country: CHART_COLORS[index % len(CHART_COLORS)]
                for index, country in enumerate(country_names)
            }

            fig = px.line(
                df,
                x="year",
                y="value",
                color="country_name",
                markers=True,
                custom_data=["country_name"],
                category_orders={
                    "country_name": country_names,
                },
                color_discrete_map=country_colors,
                labels={
                    "year": "Reporting year",
                    "value": unit,
                    "country_name": "Country",
                },
                hover_data={
                    "country_code": False,
                    "indicator_code": False,
                    "unit": False,
                },
            )

            fig.update_traces(
                line=dict(width=2.5),
                marker=dict(size=5),
            )

            fig = style_chart(fig, height=430)

            fig.update_layout(
                showlegend=False,
                margin=dict(l=12, r=20, t=20, b=25),
            )

            hover_suffix = (
                "%"
                if "percent" in unit.lower()
                else ""
            )

            render_centered_hover_chart(
                fig,
                value_suffix=hover_suffix,
                height=430,
            )

            # Country legend stays below the chart,
            # with each colour above its country name.
            legend_items = []

            for country in country_names:
                color = country_colors[country]

                legend_items.append(
                    f"""
                    <div class="country-legend-item">
                        <span
                            class="country-legend-swatch"
                            style="background-color: {color};"
                        ></span>
                        <span class="country-legend-label">
                            {html.escape(country)}
                        </span>
                    </div>
                    """
                )

            legend_markup = f"""
                <div class="country-legend-wrap">
                    <div class="country-legend-title">COUNTRY</div>
                    <div class="country-legend">
                        {''.join(legend_items)}
                    </div>
                </div>
            """

            st.html(legend_markup)

        else:
            fig = px.line(
                df,
                x="year",
                y="value",
                markers=True,
                custom_data=["country_name"],
                color_discrete_sequence=[ACCENT],
                labels={
                    "year": "Reporting year",
                    "value": unit,
                },
                hover_data={
                    "country_code": False,
                    "indicator_code": False,
                    "country_name": False,
                    "unit": False,
                },
            )

            fig.update_traces(
                line=dict(width=2.5),
                marker=dict(size=5),
            )

            fig = style_chart(fig, height=430)
            fig.update_layout(showlegend=False)

            hover_suffix = (
                "%"
                if "percent" in unit.lower()
                else ""
            )

            render_centered_hover_chart(
                fig,
                value_suffix=hover_suffix,
                height=430,
            )


    # =====================================================
    # LATEST COMPARISON
    # =====================================================

    elif view == "Latest comparison":

        st.markdown(
            '<div class="chart-heading">Latest available by country</div>',
            unsafe_allow_html=True,
        )

        st.caption(
            "Each country shows its latest non-missing observation "
            "within your selected reporting period. The years may differ."
        )

        latest_by_country = (
            df.sort_values("year")
            .groupby("country_code", as_index=False)
            .tail(1)
            .sort_values("value", ascending=True)
        )

        country_colors = {
            country: CHART_COLORS[index % len(CHART_COLORS)]
            for index, country in enumerate(
                latest_by_country["country_name"]
            )
        }

        fig = px.bar(
            latest_by_country,
            x="value",
            y="country_name",
            color="country_name",
            orientation="h",
            color_discrete_map=country_colors,
            labels={
                "value": unit,
                "country_name": "Country",
                "year": "Reporting year",
            },
            hover_data={
                "year": True,
                "country_code": False,
                "unit": False,
            },
        )

        fig.update_traces(
            texttemplate="%{x:,.2f}",
            textposition="outside",
            cliponaxis=False,
            showlegend=False,
            marker_line_width=0,
        )

        fig = style_chart(
            fig,
            height=max(
                340,
                len(latest_by_country) * 48 + 100,
            ),
        )

        fig.update_layout(
            showlegend=False,
            margin=dict(l=12, r=70, t=15, b=25),
        )

        st.plotly_chart(
            fig,
            width="stretch",
            config={
                "displayModeBar": False,
                "responsive": True,
            },
        )


    # =====================================================
    # DATA EXPLORER
    # =====================================================

    else:
        st.markdown(
            '<div class="chart-heading">Observation records</div>',
            unsafe_allow_html=True,
        )

        st.caption(
            "Explore or export the non-missing records matching "
            "your current filters."
        )

        table_df = df.sort_values(
            ["country_name", "year"],
            ascending=[True, False],
        ).copy()

        table_df = table_df[
            [
                "country_name",
                "indicator_name",
                "year",
                "value",
                "unit",
            ]
        ].rename(
            columns={
                "country_name": "Country",
                "indicator_name": "Indicator",
                "year": "Year",
                "value": "Value",
                "unit": "Unit",
            }
        )

        st.dataframe(
            table_df,
            hide_index=True,
            width="stretch",
            height=420,
            column_config={
                "Value": st.column_config.NumberColumn(
                    "Value",
                    format="%.2f",
                ),
                "Year": st.column_config.NumberColumn(
                    "Year",
                    format="%d",
                ),
            },
        )

        csv_data = table_df.to_csv(index=False).encode("utf-8")

        st.download_button(
            label="Export filtered observations",
            data=csv_data,
            file_name=(
                f"{indicator_code.replace('.', '_')}_observations.csv"
            ),
            mime="text/csv",
        )


# =========================================================
# LATEST SNAPSHOT AND DATA NOTES
# =========================================================

st.markdown(
    "<div class='section-spacer'></div>",
    unsafe_allow_html=True,
)

left_col, right_col = st.columns(
    [1.45, 1],
    gap="large",
)

latest_by_country = (
    df.sort_values("year")
    .groupby("country_code", as_index=False)
    .tail(1)
    .sort_values("country_name")
)

with left_col:
    section_header(
        "QUICK REFERENCE",
        "Latest available observations",
        "The most recent non-missing record for each country in this view.",
    )

    snapshot = latest_by_country[
        ["country_name", "year", "value", "unit"]
    ].rename(
        columns={
            "country_name": "Country",
            "year": "Year",
            "value": "Value",
            "unit": "Unit",
        }
    )

    st.dataframe(
        snapshot,
        hide_index=True,
        width="stretch",
        column_config={
            "Value": st.column_config.NumberColumn(
                "Value",
                format="%.2f",
            ),
            "Year": st.column_config.NumberColumn(
                "Year",
                format="%d",
            ),
        },
    )

with right_col:
    source_year_min = int(df["year"].min())
    source_year_max = int(df["year"].max())

    context_markup = dedent(
        f"""
        <div class="context-card">
            <div class="section-kicker">ABOUT THIS VIEW</div>
            <div class="context-title">Read the data in context.</div>
            <p class="context-copy">
                Values reflect the observations reported by the source.
                Missing values are excluded from the charts and tables.
            </p>
            <div class="context-row">
                <span>Data source</span>
                <strong>World Bank</strong>
            </div>
            <div class="context-row">
                <span>Reporting years</span>
                <strong>{source_year_min} to {source_year_max}</strong>
            </div>
            <div class="context-row">
                <span>Countries in view</span>
                <strong>{number_of_countries}</strong>
            </div>
            <div class="context-row">
                <span>Indicator unit</span>
                <strong>{html.escape(unit)}</strong>
            </div>
            <div class="context-footnote">
                Country comparisons may use different reporting years.
                Interpret them with that limitation in mind.
            </div>
        </div>
        """
    ).strip()

    st.markdown(
        context_markup,
        unsafe_allow_html=True,
    )


# =========================================================
# FOOTER
# =========================================================

footer_markup = dedent(
    """
    <div class="app-footer">
        <span>HEALTH OBSERVATORY</span>
        <span>Built with Python · PostgreSQL · Plotly</span>
    </div>
    """
).strip()

st.markdown(
    footer_markup,
    unsafe_allow_html=True,
)
