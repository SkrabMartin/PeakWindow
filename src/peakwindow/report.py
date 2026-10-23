"""A reproducible Markdown report built only from the JSON experiment records.

The report restates configuration, seed, split boundaries, data and model hashes,
and the measured metrics. It computes nothing new, so it can never disagree with
the records. Figures (see ``figures.py``) are embedded when their files were written.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from peakwindow.figures import FIGURE_ALT
from peakwindow.splits import SPLIT_V1, SPLIT_VERSION

MODEL_ORDER = ("seasonal_naive_daily", "seasonal_naive_weekly", "ridge")
FIGURES_DIR = "figures"


def _metrics_table(models: dict[str, Any]) -> list[str]:
    lines = [
        "| Model | MAE (MW) | RMSE (MW) | nMAE | Relative MAE | Abs. error p90 |",
        "|---|---|---|---|---|---|",
    ]
    for name in MODEL_ORDER:
        if name in models:
            m = models[name]
            lines.append(
                f"| {name} | {m['mae']:.1f} | {m['rmse']:.1f} | {m['nmae_pct']:.2f} % | "
                f"{m['relative_mae']:.3f} | {m.get('abs_error_p90', float('nan')):.0f} |"
            )
    return lines


def _figure(name: str, figures: Collection[str]) -> list[str]:
    if name not in figures:
        return []
    return ["", f"![{FIGURE_ALT[name]}]({FIGURES_DIR}/{name})"]


def build_report(records: dict[str, dict[str, Any]], figures: Collection[str] = ()) -> str:
    """Render the available records; sections without a record say so.

    ``figures`` names the SVG files written to ``figures/`` next to the report.
    """
    lines = [
        "# PeakWindow experiment report",
        "",
        "> Historical reconstruction on OPSD 2020-10-06 data. Not a live forecast.",
        "> Generated from the JSON records in `experiments/`; every number below is copied",
        "> from those records.",
        "",
        "## Configuration",
        "",
        f"- Split: `{SPLIT_VERSION}` (origins at 00:00 UTC, inclusive ranges)",
    ]
    for period, period_range in SPLIT_V1.items():
        lines.append(
            f"  - {period.value}: {period_range.first_origin} … {period_range.last_origin}"
        )
    selection = records.get("model_selection")
    if selection:
        chosen = selection["selection"]["chosen"]
        lines += [
            f"- Data SHA-256: `{selection['data']['processed_sha256']}`",
            f"- Source: {selection['data']['source_doi']}",
            f"- Features: {', '.join(f'`{c}`' for c in selection['feature_columns'])}",
            f"- Chosen model: Ridge per horizon, spec `{chosen['spec']}`, "
            f"alpha `{chosen['alpha']}`",
            f"- Seed: `{selection['seed']}` (closed-form Ridge, deterministic)",
            f"- PeakWindow version: `{selection['peakwindow_version']}`",
        ]
    else:
        lines.append("- Model selection record not found.")

    lines += ["", "## Validation (model selection)", ""]
    if selection:
        lines += _metrics_table(selection["selection"]["validation"]["models"])
    else:
        lines.append("Not available.")

    final = records.get("final_holdout")
    lines += ["", "## Final holdout (point forecasts)", ""]
    if final:
        result = final["result"]
        holdout = result["holdout"]
        lines += [
            f"Ridge refitted on {result['fit_origins']} origins "
            f"({' + '.join(result['fit_periods'])}); "
            f"{holdout['origins_evaluated']} of {holdout['origins_in_period']} holdout origins "
            f"scored. Model artifact SHA-256: `{final['model_artifact']['sha256']}`.",
            "",
            *_metrics_table(holdout["models"]),
            "",
            f"Sensitivity window {' … '.join(result['sensitivity']['window_origins'])}:",
            "",
            *_metrics_table(result["sensitivity"]["inside_window"]),
            "",
            "Remaining holdout origins:",
            "",
            *_metrics_table(result["sensitivity"]["outside_window"]),
            *_figure("holdout_mae_by_horizon.svg", figures),
            *_figure("holdout_example_week.svg", figures),
        ]
    else:
        lines.append("Not available.")

    intervals = records.get("intervals_holdout")
    lines += ["", "## Prediction intervals (holdout)", ""]
    if intervals:
        result = intervals["result"]
        lines += [
            f"Split conformal per horizon, calibrated on {result['calibration_origins']} "
            f"origins, measured on {result['holdout_origins']} holdout origins.",
            "",
            "| Nominal | Coverage | Mean width (MW) | Holiday coverage | Below | Above |",
            "|---|---|---|---|---|---|",
        ]
        for level, m in result["holdout"].items():
            holiday = m["coverage_holiday"]
            lines.append(
                f"| {float(level):.0%} | {m['coverage']:.1%} | {m['mean_width_mw']:.0f} | "
                f"{'n/a' if holiday is None else f'{holiday:.1%}'} | {m['below_lower']:.2%} | "
                f"{m['above_upper']:.2%} |"
            )
        lines += _figure("interval_coverage_by_horizon.svg", figures)
    else:
        lines.append("Not available.")

    backtest = records.get("backtest")
    lines += ["", "## Rolling backtest", ""]
    if backtest:
        lines += [
            "| Test year | Fit origins | Daily naive MAE | Weekly naive MAE | Ridge MAE |",
            "|---|---|---|---|---|",
        ]
        for fold in backtest["result"]["folds"]:
            m = fold["models"]
            lines.append(
                f"| {fold['test_year']} | {fold['fit_origins']} | "
                f"{m['seasonal_naive_daily']['mae']:.1f} | "
                f"{m['seasonal_naive_weekly']['mae']:.1f} | {m['ridge']['mae']:.1f} |"
            )
        lines += _figure("backtest_mae_by_year.svg", figures)
    else:
        lines.append("Not available.")
    return "\n".join(lines) + "\n"
