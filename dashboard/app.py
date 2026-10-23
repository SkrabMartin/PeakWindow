"""PeakWindow historical dashboard (Streamlit).

Reads only precomputed files: ``<experiments>/dashboard/forecasts.parquet`` and the
JSON experiment records. It never fits a model. Run from the repository root:

    uv run --frozen --group dashboard streamlit run dashboard/app.py

Set ``PEAKWINDOW_EXPERIMENTS`` to read another experiments directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from peakwindow.dashboard_data import (
    MODEL_LABELS,
    MODELS,
    WEEKDAYS,
    daily_mae,
    day_view,
    interval_coverage,
    load_bundle,
    local_day_irregular,
    mae_by,
    mae_by_day_type,
    mae_by_source,
)
from peakwindow.opsd import CITATION

# Validated categorical slots 1-3 (light / dark) and neutral ink for the actuals.
PALETTE = {
    "light": {
        "series": ["#2a78d6", "#eb6834", "#1baf7a"],
        "ink": "#0b0b0b",
        "muted": "#898781",
        "grid": "#e1e0d9",
    },
    "dark": {
        "series": ["#3987e5", "#d95926", "#199e70"],
        "ink": "#f0efec",
        "muted": "#898781",
        "grid": "#2c2c2a",
    },
}
ACTUAL_LABEL = "Actual load"
SERIES_ORDER = ["ridge", "seasonal_naive_weekly", "seasonal_naive_daily"]
SOURCE_LABELS = {
    "holdout": "Holdout 2020 (final model)",
    "backtest_2016": "Backtest 2016",
    "backtest_2017": "Backtest 2017",
    "backtest_2018": "Backtest 2018",
    "backtest_2019": "Backtest 2019",
}
FIT_DESCRIPTION = {
    "holdout": "Ridge fitted on origins 2015-01-09 … 2019-06-30 (train + validation); "
    "intervals calibrated on 2019-07-01 … 2019-12-31.",
}

st.set_page_config(page_title="PeakWindow", layout="wide")


def colors() -> dict[str, Any]:
    theme = getattr(getattr(st.context, "theme", None), "type", None)
    return PALETTE["dark" if theme == "dark" else "light"]


def series_scale(with_actual: bool = False) -> alt.Scale:
    """Fixed color per model (never by rank); actual load, when shown, in neutral ink."""
    c = colors()
    domain = [MODEL_LABELS[name] for name in SERIES_ORDER]
    palette = list(c["series"])
    if with_actual:
        return alt.Scale(domain=[ACTUAL_LABEL, *domain], range=[c["ink"], *palette])
    return alt.Scale(domain=domain, range=palette)


def styled(chart: alt.Chart | alt.LayerChart, height: int = 320) -> alt.Chart | alt.LayerChart:
    c = colors()
    return (
        chart.properties(height=height)
        .configure_axis(
            gridColor=c["grid"],
            domainColor=c["grid"],
            labelColor=c["muted"],
            titleColor=c["muted"],
            tickColor=c["grid"],
        )
        .configure_legend(labelColor=c["muted"], titleColor=c["muted"], orient="top")
        .configure_view(strokeWidth=0)
    )


@st.cache_data(show_spinner=False)
def bundle(experiments_dir: str) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    return load_bundle(Path(experiments_dir))


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def fit_description(source: str) -> str:
    if source in FIT_DESCRIPTION:
        return FIT_DESCRIPTION[source]
    year = source.removeprefix("backtest_")
    return f"Ridge refitted on origins 2015-01-09 … {int(year) - 1}-12-31 only; no intervals."


def render_day(in_source: pd.DataFrame, source: str, origin: pd.Timestamp) -> None:
    """Chart, metrics and table for the 24 targets of one origin."""
    view = day_view(in_source, origin)
    last_input = (origin - pd.Timedelta(hours=1)).strftime("%Y-%m-%d %H:%M UTC")
    st.caption(
        f"Issued {origin:%A %Y-%m-%d} 00:00 UTC · inputs up to {last_input} · "
        f"targets shown in Europe/Prague time · {fit_description(source)}"
    )
    if bool(view["is_holiday"].any()):
        st.caption("Includes a Czech public holiday.")
    irregular = local_day_irregular(view)
    if irregular:
        st.caption(irregular)

    long = view.melt(
        id_vars=["horizon_h", "local_label"],
        value_vars=["actual", *MODELS],
        var_name="series",
        value_name="load_mw",
    )
    long["series"] = long["series"].map({"actual": ACTUAL_LABEL, **MODEL_LABELS})
    x = alt.X(
        "local_label:N",
        sort=list(view["local_label"]),
        title="Target hour (Prague)",
        axis=alt.Axis(labelAngle=-45, labelOverlap=True),
    )
    color = alt.Color("series:N", scale=series_scale(with_actual=True), title=None)
    hover = alt.selection_point(
        fields=["local_label"], nearest=True, on="pointerover", empty=False, clear="pointerout"
    )
    lines = (
        alt.Chart(long)
        .mark_line(strokeWidth=2)
        .encode(
            x=x, y=alt.Y("load_mw:Q", title="Load (MW)", scale=alt.Scale(zero=False)), color=color
        )
    )
    layers: list[alt.Chart] = []
    if view["ridge_lower_90"].notna().all():
        blue = colors()["series"][0]
        for level, opacity in ((90, 0.10), (80, 0.18)):
            layers.append(
                alt.Chart(view)
                .mark_area(color=blue, opacity=opacity)
                .encode(x=x, y=f"ridge_lower_{level}:Q", y2=f"ridge_upper_{level}:Q")
            )
    points = (
        lines.mark_point(size=60, filled=True)
        .encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[
                alt.Tooltip("local_label:N", title="Hour"),
                alt.Tooltip("series:N"),
                alt.Tooltip("load_mw:Q", title="MW", format=",.0f"),
            ],
        )
        .add_params(hover)
    )
    rule = alt.Chart(view).mark_rule(color=colors()["muted"]).encode(x=x).transform_filter(hover)
    st.altair_chart(styled(alt.layer(*layers, lines, points, rule)), width="stretch")
    if layers:
        st.caption("Shaded: Ridge 90 % (light) and 80 % (darker) conformal intervals.")

    metric_columns = st.columns(len(MODELS))
    for column, name in zip(metric_columns, SERIES_ORDER, strict=True):
        mae = float((view[name] - view["actual"]).abs().mean())
        column.metric(f"{MODEL_LABELS[name]}: daily MAE", f"{mae:,.0f} MW")
    with st.expander("Data table"):
        table = view[["local_label", "actual", *MODELS, "ridge_lower_80", "ridge_upper_80"]]
        st.dataframe(table.round(0), hide_index=True, width="stretch")


# --- page -------------------------------------------------------------------

experiments = os.environ.get("PEAKWINDOW_EXPERIMENTS", "experiments")
st.title("PeakWindow: Czech electricity demand, next 24 hours")
st.warning(
    "**Historical reconstruction, not a live forecast.** Every forecast below was "
    "reconstructed from the OPSD 2020-10-06 data (Czech load 2015 to Sep 2020) as if "
    "issued at 00:00 UTC on the chosen date, using only measurements before that "
    "moment (assuming they were already published). Nothing here describes today's grid.",
    icon="⚠️",
)

try:
    forecasts, records = bundle(experiments)
except FileNotFoundError:
    st.error(
        f"No dashboard data in `{experiments}`. Create it with "
        "`uv run peakwindow export-dashboard` (after `fetch-opsd`, `prepare-opsd` "
        "and `select-model`)."
    )
    st.stop()

day_tab, performance_tab, failure_tab, about_tab = st.tabs(
    ["Forecast day", "Performance", "Failures", "About"]
)

# --- forecast day -----------------------------------------------------------

with day_tab:
    sources = [s for s in SOURCE_LABELS if s in set(forecasts["source"])]
    left, right = st.columns([1, 1])
    source = left.selectbox("Period", sources, format_func=source_label)
    in_source = forecasts.loc[forecasts["source"] == source]
    origins = sorted(in_source["origin_utc"].unique())
    dates = [pd.Timestamp(o).date() for o in origins]
    chosen_date = right.date_input(
        "Forecast issued at 00:00 UTC on",
        value=dates[0],
        min_value=dates[0],
        max_value=dates[-1],
    )
    origin = pd.Timestamp(chosen_date, tz="UTC")
    if origin not in set(pd.DatetimeIndex(origins)):
        st.info(
            "No forecast for this date: it is excluded because inputs or targets are "
            "missing (data-gap exception). Choose another date."
        )

    else:
        render_day(in_source, source, origin)

# --- performance -------------------------------------------------------------

with performance_tab:
    final = records.get("final_holdout")
    if final:
        st.subheader("Final holdout 2020 (evaluated once, pre-registered protocol)")
        models = final["result"]["holdout"]["models"]
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Model": MODEL_LABELS[name],
                        "MAE (MW)": models[name]["mae"],
                        "RMSE (MW)": models[name]["rmse"],
                        "nMAE (%)": models[name]["nmae_pct"],
                        "Relative MAE": models[name]["relative_mae"],
                    }
                    for name in SERIES_ORDER
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    coverage = interval_coverage(forecasts)
    if not coverage.empty:
        st.subheader("Ridge prediction intervals on the holdout")
        st.dataframe(
            coverage.assign(coverage=lambda d: (d["coverage"] * 100).round(1))
            .rename(
                columns={
                    "nominal": "Nominal",
                    "coverage": "Coverage (%)",
                    "mean_width_mw": "Mean width (MW)",
                }
            )
            .round(0),
            hide_index=True,
            width="stretch",
        )
        st.caption("The 80 % interval undercovers in 2020; see the methodology for why.")

    st.subheader("MAE by period")
    by_source = mae_by_source(forecasts)
    by_source["period"] = by_source["source"].map(source_label)
    long_source = by_source.melt(
        id_vars=["period"], value_vars=list(MODELS), var_name="series", value_name="mae"
    )
    long_source["series"] = long_source["series"].map(MODEL_LABELS)
    st.altair_chart(
        styled(
            alt.Chart(long_source)
            .mark_bar(cornerRadiusEnd=4, size=14)
            .encode(
                y=alt.Y("period:N", title=None, sort=list(by_source["period"])),
                yOffset=alt.YOffset("series:N", sort=[MODEL_LABELS[n] for n in SERIES_ORDER]),
                x=alt.X("mae:Q", title="MAE (MW)"),
                color=alt.Color("series:N", scale=series_scale(), title=None),
                tooltip=["period:N", "series:N", alt.Tooltip("mae:Q", format=",.0f")],
            ),
            height=360,
        ),
        width="stretch",
    )

    st.subheader("MAE by forecast horizon")
    scope = st.radio("Rows", ["Holdout 2020", "Backtest 2016-2019"], horizontal=True)
    rows = forecasts["source"].eq("holdout")
    by_horizon = mae_by(forecasts.loc[rows if scope.startswith("Holdout") else ~rows], "horizon_h")
    long_h = by_horizon.melt(
        id_vars=["horizon_h"], value_vars=list(MODELS), var_name="series", value_name="mae"
    )
    long_h["series"] = long_h["series"].map(MODEL_LABELS)
    hover_h = alt.selection_point(
        fields=["horizon_h"], nearest=True, on="pointerover", empty=False, clear="pointerout"
    )
    base_h = alt.Chart(long_h).encode(
        x=alt.X("horizon_h:Q", title="Hours after the 00:00 UTC origin"),
        y=alt.Y("mae:Q", title="MAE (MW)"),
        color=alt.Color("series:N", scale=series_scale(), title=None),
    )
    st.altair_chart(
        styled(
            alt.layer(
                base_h.mark_line(strokeWidth=2),
                base_h.mark_point(size=60, filled=True)
                .encode(
                    opacity=alt.condition(hover_h, alt.value(1), alt.value(0)),
                    tooltip=["horizon_h:Q", "series:N", alt.Tooltip("mae:Q", format=",.0f")],
                )
                .add_params(hover_h),
            )
        ),
        width="stretch",
    )
    with st.expander("Data table"):
        st.dataframe(by_horizon.round(0), hide_index=True, width="stretch")

# --- failures ----------------------------------------------------------------

with failure_tab:
    scope_f = st.radio(
        "Rows", ["All out-of-sample days", "Holdout 2020"], horizontal=True, key="failure_scope"
    )
    rows_f = (
        forecasts if scope_f.startswith("All") else forecasts.loc[forecasts["source"] == "holdout"]
    )

    st.subheader("MAE by day type")
    by_type = mae_by_day_type(rows_f)
    long_t = by_type.melt(
        id_vars=["day_type"], value_vars=list(MODELS), var_name="series", value_name="mae"
    )
    long_t["series"] = long_t["series"].map(MODEL_LABELS)
    st.altair_chart(
        styled(
            alt.Chart(long_t)
            .mark_bar(cornerRadiusEnd=4, size=16)
            .encode(
                y=alt.Y("day_type:N", title=None, sort=list(by_type["day_type"])),
                yOffset=alt.YOffset("series:N", sort=[MODEL_LABELS[n] for n in SERIES_ORDER]),
                x=alt.X("mae:Q", title="MAE (MW)"),
                color=alt.Color("series:N", scale=series_scale(), title=None),
                tooltip=["day_type:N", "series:N", alt.Tooltip("mae:Q", format=",.0f")],
            ),
            height=260,
        ),
        width="stretch",
    )

    st.subheader("Ridge daily MAE over time")
    daily = daily_mae(rows_f)
    daily["day"] = daily["origin_utc"].dt.tz_convert(None)
    daily["day_kind"] = daily["is_holiday"].map({True: "Public holiday", False: "Other day"})
    blue = colors()["series"][0]
    base_d = alt.Chart(daily).encode(
        x=alt.X("day:T", title=None), y=alt.Y("ridge:Q", title="Ridge daily MAE (MW)")
    )
    st.altair_chart(
        styled(
            alt.layer(
                base_d.mark_line(strokeWidth=1, color=blue, opacity=0.7),
                base_d.transform_filter("datum.is_holiday")
                .mark_point(size=70, filled=True, color=colors()["ink"], shape="diamond")
                .encode(
                    tooltip=[
                        alt.Tooltip("day:T", format="%Y-%m-%d"),
                        alt.Tooltip("ridge:Q", format=",.0f", title="MAE"),
                    ]
                ),
            ),
            height=280,
        ),
        width="stretch",
    )
    st.caption("Diamonds mark public holidays.")

    st.subheader("Worst days for Ridge")
    st.caption(
        "Ridge leans on the last observed hour. One bad input moves its whole day: the worst "
        "day, 2016-04-28, follows an isolated spike at 2016-04-27 23:00 UTC (8,706 MW between "
        "values near 6,900 MW) that the data audit lists and that is scored as published. "
        "Holiday-adjacent days such as 31 Dec and 2 Jan are the other weak spot."
    )
    worst = daily.nlargest(10, "ridge")
    st.dataframe(
        pd.DataFrame(
            {
                "Origin (UTC)": worst["origin_utc"].dt.strftime("%Y-%m-%d"),
                "Weekday": worst["local_weekday"].map(lambda d: WEEKDAYS[int(d)]),
                "Public holiday": worst["is_holiday"],
                "Period": worst["source"].map(source_label),
                **{f"{MODEL_LABELS[n]} MAE": worst[n].round(0) for n in SERIES_ORDER},
            }
        ),
        hide_index=True,
        width="stretch",
    )

# --- about -------------------------------------------------------------------

with about_tab:
    st.markdown(
        """
**What this is.** A historical study of next-24-hour forecasts of Czech national
electricity load. Forecasts are issued at 00:00 UTC and cover the following 24 hours.
Inputs are strictly measurements before the issue time, plus the calendar.

**Models.** Two seasonal naive baselines (same hour yesterday or last week), and a
per-horizon Ridge regression with holiday and weekday-dependent lag features.

**Evaluation.**
- Every model is scored on the same hours.
- The split is frozen: train 2015-01-09…2018-12-31, validation 2019 H1, calibration
  2019 H2, holdout 2020-01-01…2020-09-29.
- The holdout was evaluated once, following protocols committed beforehand.
- Backtest days come from yearly refits on earlier data only.

**Limitations.**
- The forecasts assume past measurements were published immediately.
- Public holidays remain the largest errors.
- The 80 % interval undercovers in 2020.
- National demand says nothing about local network constraints. This is not a
  decision tool.
"""
    )
    st.caption(f"Data: {CITATION}")
