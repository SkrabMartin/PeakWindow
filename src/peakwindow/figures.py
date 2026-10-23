"""Static SVG figures for the report, drawn without a plotting dependency.

Each figure is rendered from the JSON experiment records, except the example week,
which plots stored forecasts from the dashboard table. Like the report, the figures
compute no new metrics. The output is deterministic: the same inputs give
byte-identical SVG files.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from typing import Any

import pandas as pd

from peakwindow.dashboard_data import MODEL_LABELS, MODELS
from peakwindow.evaluation import ACTUAL
from peakwindow.study import RIDGE

# Validated categorical slots 1-3, the same as the dashboard's light theme.
MODEL_COLORS = dict(zip(MODELS, ("#2a78d6", "#eb6834", "#1baf7a"), strict=True))
INK = "#0b0b0b"
MUTED = "#6b6a65"
GRID = "#e1e0d9"
SURFACE = "#ffffff"

WIDTH, HEIGHT = 720, 360
LEFT, RIGHT, TOP, BOTTOM = 64, 24, 72, 44

# File name -> alt text; the report embeds the files that were written.
FIGURE_ALT = {
    "holdout_mae_by_horizon.svg": "Holdout MAE by forecast horizon for each model",
    "interval_coverage_by_horizon.svg": "Holdout interval coverage by horizon against nominal",
    "backtest_mae_by_year.svg": "Rolling-backtest MAE by test year for each model",
    "holdout_example_week.svg": "Actual load, Ridge forecast and 90 % interval for one week",
}


@dataclass(frozen=True)
class Series:
    label: str
    color: str
    values: Sequence[float]
    dashed: bool = False
    markers: bool = False


@dataclass(frozen=True)
class Band:
    lower: Sequence[float]
    upper: Sequence[float]
    color: str


def _nice_step(span: float) -> float:
    raw = span / 5
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * magnitude:
            return factor * magnitude
    return 10 * magnitude  # pragma: no cover - the loop always returns


def _y_ticks(low: float, high: float, zero: bool) -> list[float]:
    if zero:
        low = min(0.0, low)
    if high <= low:
        high = low + 1
    step = _nice_step(high - low)
    start = math.floor(low / step) * step
    stop = math.ceil(high / step) * step
    count = round((stop - start) / step)
    return [start + i * step for i in range(count + 1)]


def line_chart(
    title: str,
    x: Sequence[float],
    x_ticks: Sequence[tuple[float, str]],
    series: Sequence[Series],
    *,
    band: Band | None = None,
    y_label: str,
    y_format: str = "{:,.0f}",
    y_zero: bool = True,
    x_pad: float = 0.0,
) -> str:
    """One chart with a single y axis, a legend row and recessive grid lines."""
    values = [v for s in series for v in s.values]
    if band is not None:
        values += [*band.lower, *band.upper]
    ticks = _y_ticks(min(values), max(values), y_zero)
    y_low, y_high = ticks[0], ticks[-1]
    x_low, x_high = min(x) - x_pad, max(x) + x_pad
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

    def px(value: float) -> float:
        return LEFT + (value - x_low) / (x_high - x_low) * plot_w

    def py(value: float) -> float:
        return TOP + (y_high - value) / (y_high - y_low) * plot_h

    def points(xs: Sequence[float], ys: Sequence[float]) -> str:
        return " ".join(f"{px(a):.1f},{py(b):.1f}" for a, b in zip(xs, ys, strict=True))

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(title)}" '
        'font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f"<title>{escape(title)}</title>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{SURFACE}"/>',
        f'<text x="{LEFT}" y="24" font-size="15" font-weight="600" fill="{INK}">'
        f"{escape(title)}</text>",
    ]
    legend_x = float(LEFT)
    for s in series:
        dash = ' stroke-dasharray="6 4"' if s.dashed else ""
        out.append(
            f'<line x1="{legend_x:.0f}" y1="46" x2="{legend_x + 22:.0f}" y2="46" '
            f'stroke="{s.color}" stroke-width="2"{dash}/>'
        )
        out.append(
            f'<text x="{legend_x + 28:.0f}" y="50" font-size="12" fill="{INK}">'
            f"{escape(s.label)}</text>"
        )
        legend_x += 40 + 6.6 * len(s.label)
    if band is not None:
        out.append(
            f'<rect x="{legend_x:.0f}" y="40" width="22" height="12" rx="2" '
            f'fill="{band.color}" fill-opacity="0.2"/>'
        )
        out.append(
            f'<text x="{legend_x + 28:.0f}" y="50" font-size="12" fill="{INK}">90 % interval</text>'
        )
    for tick in ticks:
        y = py(tick)
        out.append(
            f'<line x1="{LEFT}" y1="{y:.1f}" x2="{WIDTH - RIGHT}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{LEFT - 8}" y="{y + 4:.1f}" font-size="11" fill="{MUTED}" '
            f'text-anchor="end">{escape(y_format.format(tick))}</text>'
        )
    for value, label in x_ticks:
        out.append(
            f'<text x="{px(value):.1f}" y="{HEIGHT - BOTTOM + 18}" font-size="11" '
            f'fill="{MUTED}" text-anchor="middle">{escape(label)}</text>'
        )
    out.append(
        f'<text transform="translate(16 {TOP + plot_h / 2:.0f}) rotate(-90)" font-size="11" '
        f'fill="{MUTED}" text-anchor="middle">{escape(y_label)}</text>'
    )
    if band is not None:
        outline = points([*x, *reversed(x)], [*band.upper, *reversed(band.lower)])
        out.append(f'<polygon points="{outline}" fill="{band.color}" fill-opacity="0.2"/>')
    for s in series:
        dash = ' stroke-dasharray="6 4"' if s.dashed else ""
        out.append(
            f'<polyline points="{points(x, s.values)}" fill="none" stroke="{s.color}" '
            f'stroke-width="2" stroke-linejoin="round"{dash}/>'
        )
        if s.markers:
            for a, b in zip(x, s.values, strict=True):
                out.append(
                    f'<circle cx="{px(a):.1f}" cy="{py(b):.1f}" r="4.5" fill="{s.color}" '
                    f'stroke="{SURFACE}" stroke-width="2"/>'
                )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _horizon_ticks() -> list[tuple[float, str]]:
    return [(h, f"{h:02d}:00") for h in range(0, 24, 3)]


def holdout_mae_by_horizon(final: dict[str, Any]) -> str:
    models = final["result"]["holdout"]["models"]
    return line_chart(
        "Holdout 2020: MAE by target hour (UTC)",
        list(range(24)),
        _horizon_ticks(),
        [
            Series(MODEL_LABELS[m], MODEL_COLORS[m], models[m]["mae_by_horizon"])
            for m in MODELS
            if m in models
        ],
        y_label="MAE (MW)",
    )


def interval_coverage_by_horizon(intervals: dict[str, Any]) -> str:
    holdout = intervals["result"]["holdout"]
    colors = (MODEL_COLORS["seasonal_naive_daily"], MODEL_COLORS["seasonal_naive_weekly"])
    series: list[Series] = []
    for (level, metrics), color in zip(sorted(holdout.items()), colors, strict=True):
        nominal = float(level)
        series.append(
            Series(f"{nominal * 100:.0f} % coverage", color, metrics["coverage_by_horizon"])
        )
        series.append(Series(f"{nominal * 100:.0f} % nominal", color, [nominal] * 24, dashed=True))
    return line_chart(
        "Holdout 2020: Ridge interval coverage by target hour (UTC)",
        list(range(24)),
        _horizon_ticks(),
        series,
        y_label="Coverage",
        y_format="{:.0%}",
        y_zero=False,
    )


def backtest_mae_by_year(backtest: dict[str, Any]) -> str:
    folds = backtest["result"]["folds"]
    years = [int(fold["test_year"]) for fold in folds]
    return line_chart(
        "Rolling backtest: MAE by test year",
        years,
        [(year, str(year)) for year in years],
        [
            Series(
                MODEL_LABELS[m],
                MODEL_COLORS[m],
                [fold["models"][m]["mae"] for fold in folds],
                markers=True,
            )
            for m in MODELS
        ],
        y_label="MAE (MW)",
        x_pad=0.25,
    )


def example_week(forecasts: pd.DataFrame) -> str | None:
    """The first Monday-to-Sunday holdout week with all 7 origins, or None."""
    holdout = forecasts.loc[forecasts["source"] == "holdout"].sort_values(
        ["origin_utc", "horizon_h"]
    )
    origins = pd.DatetimeIndex(holdout["origin_utc"].unique())
    available = set(origins)
    for start in origins[origins.day_of_week == 0]:
        days = [start + pd.Timedelta(days=i) for i in range(7)]
        if all(day in available for day in days):
            break
    else:
        return None
    week = holdout.loc[holdout["origin_utc"].isin(days)]
    hours = list(range(len(week)))
    return line_chart(
        f"Holdout week from {start:%Y-%m-%d}: actual load and Ridge forecast",
        hours,
        [(24 * i + 12, f"{day:%a %d %b}") for i, day in enumerate(days)],
        [
            Series("Actual load", INK, week[ACTUAL].tolist()),
            Series(MODEL_LABELS[RIDGE], MODEL_COLORS[RIDGE], week[RIDGE].tolist()),
        ],
        band=Band(
            week["ridge_lower_90"].tolist(),
            week["ridge_upper_90"].tolist(),
            MODEL_COLORS[RIDGE],
        ),
        y_label="Load (MW)",
        y_zero=False,
    )


def build_figures(
    records: dict[str, dict[str, Any]], forecasts: pd.DataFrame | None = None
) -> dict[str, str]:
    """File name -> SVG text for every figure whose inputs are available."""
    figures: dict[str, str] = {}
    if "final_holdout" in records:
        figures["holdout_mae_by_horizon.svg"] = holdout_mae_by_horizon(records["final_holdout"])
    if "intervals_holdout" in records:
        figures["interval_coverage_by_horizon.svg"] = interval_coverage_by_horizon(
            records["intervals_holdout"]
        )
    if "backtest" in records:
        figures["backtest_mae_by_year.svg"] = backtest_mae_by_year(records["backtest"])
    if forecasts is not None:
        week = example_week(forecasts)
        if week is not None:
            figures["holdout_example_week.svg"] = week
    return figures
