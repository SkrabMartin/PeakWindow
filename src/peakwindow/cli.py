"""Command-line interface for PeakWindow.

Exit status:

* 0 -- success (``validate-data``: every quality check passed;
  ``prepare-opsd``: processed series written; ``evaluate-baselines``,
  ``select-model``, ``evaluate-holdout``, ``backtest``, ``evaluate-intervals``:
  record written)
* 1 -- the data was read but failed (``validate-data``: a quality check;
  ``prepare-opsd``: the series could not be processed or UTC/local alignment failed)
* 2 -- usage error, network/download failure, checksum mismatch, a locked holdout,
  a missing or mismatched model-selection record, or a file that could not be read
  or written
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from peakwindow import __version__, baselines, opsd
from peakwindow.acquisition import AcquisitionError, ProgressCallback, fetch, sha256_of
from peakwindow.backtest import rolling_backtest
from peakwindow.csv_adapter import CsvReadError, read_canonical_csv
from peakwindow.evaluation import HoldoutLockedError, ModelMetrics, evaluate_baselines
from peakwindow.features import FEATURE_COLUMNS
from peakwindow.prepare import PreparationError, load_processed, prepare_opsd
from peakwindow.splits import SPLIT_VERSION, Period
from peakwindow.study import RIDGE, final_evaluation, interval_evaluation, select_model
from peakwindow.validation import validate_hourly_frame

EXIT_OK = 0
EXIT_QUALITY_FAILURE = 1
EXIT_USAGE_OR_IO_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="peakwindow",
        description=(
            "PeakWindow: historical Czech electricity-demand forecasting laboratory. "
            "Provides OPSD data acquisition, offline preparation, data-quality audits, "
            "seasonal naive baselines, Ridge model selection, the final holdout evaluation, "
            "rolling backtests and conformal prediction intervals."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    validate = commands.add_parser(
        "validate-data",
        help="validate an hourly load CSV (columns timestamp_utc, load_mw)",
        description=(
            "Validate a CSV in the canonical schema (timestamp_utc, load_mw) and emit a JSON "
            "quality report. Exit status: 0 passed, 1 quality failure, 2 usage or I/O error."
        ),
    )
    validate.add_argument("--input", required=True, type=Path, help="path to the CSV file")
    validate.add_argument(
        "--output",
        type=Path,
        help="write the JSON report here (parent directories are created); "
        "without it the report is printed to stdout",
    )
    validate.add_argument(
        "--synthetic",
        action="store_true",
        help="declare the input as synthetic data; recorded in the report "
        "(otherwise provenance is recorded as undeclared: null)",
    )
    validate.set_defaults(handler=_validate_data)

    data_dir_help = "project data directory (default: ./data)"
    fetch_parser = commands.add_parser(
        "fetch-opsd",
        help="download the OPSD 2020-10-06 hourly file (~130 MB) into the local cache",
        description=(
            f"Download {opsd.FILE_NAME} from the OPSD {opsd.RELEASE} release into "
            "<data-dir>/raw/, verify its size and SHA-256, and write a provenance manifest. "
            "A verified cached copy is reused. This is the only command that uses the network."
        ),
    )
    fetch_parser.add_argument("--data-dir", type=Path, default=Path("data"), help=data_dir_help)
    fetch_parser.add_argument(
        "--force", action="store_true", help="download again even if a cached copy exists"
    )
    fetch_parser.set_defaults(handler=_fetch_opsd)

    prepare_parser = commands.add_parser(
        "prepare-opsd",
        help="verify the cached OPSD file, extract the Czech load series and audit it (offline)",
        description=(
            "Verify the cached OPSD file, map utc_timestamp/CZ_load_actual_entsoe_transparency "
            "to timestamp_utc/load_mw, check UTC vs CET/CEST alignment, audit coverage, and "
            "write a Parquet series plus a JSON audit record. Missing values are kept, not filled."
        ),
    )
    prepare_parser.add_argument("--data-dir", type=Path, default=Path("data"), help=data_dir_help)
    prepare_parser.add_argument(
        "--output-dir",
        type=Path,
        help="where to write outputs (default: <data-dir>/processed)",
    )
    prepare_parser.set_defaults(handler=_prepare_opsd)

    evaluate_parser = commands.add_parser(
        "evaluate-baselines",
        help="score the daily and weekly seasonal naive baselines on one split period",
        description=(
            "Score the seasonal naive baselines on the origins of one period of the frozen "
            "split (split-v1) and write a JSON experiment record. The final holdout is locked."
        ),
    )
    evaluate_parser.add_argument(
        "--period",
        required=True,
        choices=[period.value for period in Period if period is not Period.HOLDOUT],
        help="split period to evaluate (the holdout is locked)",
    )
    evaluate_parser.add_argument("--data-dir", type=Path, default=Path("data"), help=data_dir_help)
    evaluate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experiments"),
        help="where to write the experiment record (default: ./experiments)",
    )
    evaluate_parser.set_defaults(handler=_evaluate_baselines)

    experiments_help = "experiment records directory (default: ./experiments)"
    select_parser = commands.add_parser(
        "select-model",
        help="fit Ridge candidates on train and choose one by validation MAE",
        description=(
            "Fit every Ridge candidate (feature spec and alpha) on the training origins, "
            "score each on validation, and record the choice in model_selection.json. "
            "The holdout is not touched."
        ),
    )
    select_parser.add_argument("--data-dir", type=Path, default=Path("data"), help=data_dir_help)
    select_parser.add_argument(
        "--output-dir", type=Path, default=Path("experiments"), help=experiments_help
    )
    select_parser.set_defaults(handler=_select_model)

    holdout_parser = commands.add_parser(
        "evaluate-holdout",
        help="FINAL: refit the selected model on train + validation and score the holdout",
        description=(
            "Read the configuration chosen by select-model, refit it on train + validation, "
            "and score it with both baselines on the 2020 holdout. This is the study's final "
            "evaluation; it requires --confirm."
        ),
    )
    holdout_parser.add_argument("--data-dir", type=Path, default=Path("data"), help=data_dir_help)
    holdout_parser.add_argument(
        "--output-dir", type=Path, default=Path("experiments"), help=experiments_help
    )
    holdout_parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm that models are frozen and the holdout may be opened",
    )
    holdout_parser.set_defaults(handler=_evaluate_holdout)

    backtest_parser = commands.add_parser(
        "backtest",
        help="rolling-origin backtest 2016-2019 with yearly refits (holdout excluded)",
        description=(
            "Refit the configuration chosen by select-model on all origins before each test "
            "year (2016-2019) and score it with both baselines on that year."
        ),
    )
    backtest_parser.add_argument("--data-dir", type=Path, default=Path("data"), help=data_dir_help)
    backtest_parser.add_argument(
        "--output-dir", type=Path, default=Path("experiments"), help=experiments_help
    )
    backtest_parser.set_defaults(handler=_backtest)

    intervals_parser = commands.add_parser(
        "evaluate-intervals",
        help="FINAL: calibrate conformal intervals and measure their coverage on the holdout",
        description=(
            "Calibrate per-horizon split-conformal intervals (80 %% and 90 %%) on the "
            "calibration period for the train + validation Ridge, then measure coverage once "
            "on the 2020 holdout. Requires --confirm."
        ),
    )
    intervals_parser.add_argument("--data-dir", type=Path, default=Path("data"), help=data_dir_help)
    intervals_parser.add_argument(
        "--output-dir", type=Path, default=Path("experiments"), help=experiments_help
    )
    intervals_parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm that the interval method is fixed and the holdout may be opened",
    )
    intervals_parser.set_defaults(handler=_evaluate_intervals)
    return parser


def _progress_printer() -> ProgressCallback:
    last_reported = [-1]

    def report(written: int, total: int | None) -> None:
        if not total:
            return
        tenth = written * 10 // total
        if tenth != last_reported[0]:
            last_reported[0] = tenth
            print(f"  downloaded {written / 1e6:.0f} / {total / 1e6:.0f} MB", file=sys.stderr)

    return report


def _fetch_opsd(args: argparse.Namespace) -> int:
    destination = opsd.raw_path(args.data_dir)
    print(f"fetch-opsd: {opsd.SOURCE.url}", file=sys.stderr)
    try:
        manifest = fetch(
            opsd.SOURCE,
            destination,
            citation=opsd.CITATION,
            force=args.force,
            progress=_progress_printer(),
        )
    except AcquisitionError as exc:
        print(f"peakwindow: error: {exc}", file=sys.stderr)
        return EXIT_USAGE_OR_IO_ERROR
    if manifest is None:
        print(
            f"cached copy verified (SHA-256 matches), not downloaded: {destination}",
            file=sys.stderr,
        )
    else:
        print(
            f"downloaded and verified (SHA-256 {manifest.sha256}): {destination}", file=sys.stderr
        )
    print(f"cite as: {opsd.CITATION}", file=sys.stderr)
    return EXIT_OK


def _prepare_opsd(args: argparse.Namespace) -> int:
    output_dir: Path = (
        args.output_dir if args.output_dir is not None else args.data_dir / "processed"
    )
    try:
        result = prepare_opsd(args.data_dir, output_dir)
    except PreparationError as exc:
        print(f"peakwindow: error: {exc}", file=sys.stderr)
        return EXIT_USAGE_OR_IO_ERROR

    audit = result.audit
    print(f"prepare-opsd: {audit.source}", file=sys.stderr)
    print(
        f"  UTC vs CET/CEST alignment: {'ok' if result.alignment.passed else 'FAILED'} "
        f"({result.alignment.rows_checked} rows)",
        file=sys.stderr,
    )
    if audit.processable:
        print(
            f"  observed span {audit.observed_first_utc} .. {audit.observed_last_utc} "
            f"({audit.observed_hours} hours, {audit.missing_load_hours_in_span} without load "
            f"in {len(audit.missing_runs or ())} run(s))",
            file=sys.stderr,
        )
    for issue in audit.issues:
        print(f"  - {issue}", file=sys.stderr)
    print(f"audit record written to {result.record_path}", file=sys.stderr)
    if result.processed_path is None:
        print("prepare-opsd FAILED: processed series not written", file=sys.stderr)
        return EXIT_QUALITY_FAILURE
    print(f"processed series written to {result.processed_path}", file=sys.stderr)
    return EXIT_OK


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, data: dict[str, object]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, allow_nan=False, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"peakwindow: error: cannot write {path}: {exc.strerror or exc}", file=sys.stderr)
        return False
    return True


def _print_metrics(models: dict[str, ModelMetrics]) -> None:
    for name, metrics in models.items():
        print(
            f"  {name}: MAE {metrics.mae:.1f} MW, RMSE {metrics.rmse:.1f} MW, "
            f"nMAE {metrics.nmae_pct:.2f} %, relative MAE {metrics.relative_mae:.3f}",
            file=sys.stderr,
        )


def _select_model(args: argparse.Namespace) -> int:
    try:
        series, data_sha256 = load_processed(args.data_dir / "processed")
    except PreparationError as exc:
        print(f"peakwindow: error: {exc}", file=sys.stderr)
        return EXIT_USAGE_OR_IO_ERROR
    result = select_model(series)
    record: dict[str, object] = {
        "schema_version": "1",
        "experiment": "model_selection",
        "generated_at_utc": _utc_now(),
        "peakwindow_version": __version__,
        "split_version": SPLIT_VERSION,
        "data": {"processed_sha256": data_sha256, "source_doi": opsd.DOI_URL},
        "feature_columns": list(FEATURE_COLUMNS),
        "seed": None,
        "selection": result.to_dict(),
    }
    output_path: Path = args.output_dir / "model_selection.json"
    if not _write_json(output_path, record):
        return EXIT_USAGE_OR_IO_ERROR
    chosen = result.chosen
    print(
        f"select-model [{SPLIT_VERSION}]: {len(result.candidates)} candidates fitted on "
        f"{result.training_origins} training origins; chosen spec={chosen.spec} "
        f"alpha={chosen.alpha} (validation MAE {chosen.validation.mae:.1f} MW)",
        file=sys.stderr,
    )
    _print_metrics(result.validation.models)
    print(f"selection record written to {output_path}", file=sys.stderr)
    return EXIT_OK


def _load_selection(args: argparse.Namespace) -> tuple[pd.DataFrame, str, dict[str, Any]] | None:
    """Load the processed series and a model selection made on that same data."""
    selection_path: Path = args.output_dir / "model_selection.json"
    try:
        selection: dict[str, Any] = json.loads(selection_path.read_text(encoding="utf-8"))
        series, data_sha256 = load_processed(args.data_dir / "processed")
    except FileNotFoundError:
        print(
            f"peakwindow: error: no model selection at {selection_path}; run select-model first",
            file=sys.stderr,
        )
        return None
    except (OSError, json.JSONDecodeError, PreparationError) as exc:
        print(f"peakwindow: error: {exc}", file=sys.stderr)
        return None
    if selection.get("data", {}).get("processed_sha256") != data_sha256:
        print(
            "peakwindow: error: the model selection was made on different data; "
            "re-run select-model",
            file=sys.stderr,
        )
        return None
    return series, data_sha256, selection


def _backtest(args: argparse.Namespace) -> int:
    loaded = _load_selection(args)
    if loaded is None:
        return EXIT_USAGE_OR_IO_ERROR
    series, data_sha256, selection = loaded
    chosen = selection["selection"]["chosen"]
    result = rolling_backtest(series, chosen["spec"], float(chosen["alpha"]))
    record: dict[str, object] = {
        "schema_version": "1",
        "experiment": "rolling_backtest",
        "generated_at_utc": _utc_now(),
        "peakwindow_version": __version__,
        "split_version": SPLIT_VERSION,
        "data": {"processed_sha256": data_sha256, "source_doi": opsd.DOI_URL},
        "seed": None,
        "result": result.to_dict(),
    }
    output_path: Path = args.output_dir / "backtest.json"
    if not _write_json(output_path, record):
        return EXIT_USAGE_OR_IO_ERROR
    print(f"backtest: spec={result.spec} alpha={result.alpha}, yearly refit", file=sys.stderr)
    for fold in result.folds:
        print(f" {fold.test_year} (fit on {fold.fit_origins} origins):", file=sys.stderr)
        _print_metrics(fold.models)
    print(f"backtest record written to {output_path}", file=sys.stderr)
    return EXIT_OK


def _evaluate_intervals(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "peakwindow: error: evaluate-intervals opens the final holdout; re-run with "
            "--confirm once the interval method is fixed",
            file=sys.stderr,
        )
        return EXIT_USAGE_OR_IO_ERROR
    loaded = _load_selection(args)
    if loaded is None:
        return EXIT_USAGE_OR_IO_ERROR
    series, data_sha256, selection = loaded
    chosen = selection["selection"]["chosen"]
    result = interval_evaluation(series, chosen["spec"], float(chosen["alpha"]))

    final_model_path: Path = args.output_dir / "ridge_final_model.json"
    if final_model_path.is_file():
        stored = json.loads(final_model_path.read_text(encoding="utf-8"))
        if stored != json.loads(json.dumps(result.model_export)):
            print(
                f"peakwindow: error: the refitted model differs from {final_model_path.name}",
                file=sys.stderr,
            )
            return EXIT_USAGE_OR_IO_ERROR
    record: dict[str, object] = {
        "schema_version": "1",
        "experiment": "intervals_holdout",
        "generated_at_utc": _utc_now(),
        "peakwindow_version": __version__,
        "split_version": SPLIT_VERSION,
        "data": {"processed_sha256": data_sha256, "source_doi": opsd.DOI_URL},
        "model_matches_final_holdout_model": final_model_path.is_file(),
        "seed": None,
        "result": result.to_dict(),
    }
    output_path: Path = args.output_dir / "intervals_holdout.json"
    if not _write_json(output_path, record):
        return EXIT_USAGE_OR_IO_ERROR
    print(
        f"evaluate-intervals: calibrated on {result.calibration_origins} origins, "
        f"measured on {result.holdout_origins} holdout origins",
        file=sys.stderr,
    )
    for level, metrics in result.holdout.items():
        print(
            f"  {level:.0%} interval: coverage {metrics.coverage:.1%}, "
            f"mean width {metrics.mean_width_mw:.0f} MW",
            file=sys.stderr,
        )
    print(f"interval record written to {output_path}", file=sys.stderr)
    return EXIT_OK


def _evaluate_holdout(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "peakwindow: error: evaluate-holdout opens the final holdout; re-run with "
            "--confirm once the model selection is frozen",
            file=sys.stderr,
        )
        return EXIT_USAGE_OR_IO_ERROR
    loaded = _load_selection(args)
    if loaded is None:
        return EXIT_USAGE_OR_IO_ERROR
    series, data_sha256, selection = loaded
    chosen = selection["selection"]["chosen"]

    result = final_evaluation(series, chosen["spec"], float(chosen["alpha"]))
    model_path: Path = args.output_dir / "ridge_final_model.json"
    if not _write_json(model_path, result.model_export):
        return EXIT_USAGE_OR_IO_ERROR
    record: dict[str, object] = {
        "schema_version": "1",
        "experiment": "final_holdout",
        "generated_at_utc": _utc_now(),
        "peakwindow_version": __version__,
        "split_version": SPLIT_VERSION,
        "data": {"processed_sha256": data_sha256, "source_doi": opsd.DOI_URL},
        "model_selection_generated_at_utc": selection.get("generated_at_utc"),
        "model_artifact": {"file_name": model_path.name, "sha256": sha256_of(model_path)},
        "seed": None,
        "result": result.to_dict(),
    }
    output_path: Path = args.output_dir / "final_holdout.json"
    if not _write_json(output_path, record):
        return EXIT_USAGE_OR_IO_ERROR
    holdout = result.holdout
    print(
        f"evaluate-holdout [{SPLIT_VERSION}]: {RIDGE} spec={result.spec} alpha={result.alpha} "
        f"refit on {result.fit_origins} origins; holdout {holdout.origins_evaluated} of "
        f"{holdout.origins_in_period} origins",
        file=sys.stderr,
    )
    _print_metrics(holdout.models)
    print(f"final record written to {output_path}", file=sys.stderr)
    return EXIT_OK


def _evaluate_baselines(args: argparse.Namespace) -> int:
    period = Period(args.period)
    processed_dir: Path = args.data_dir / "processed"
    try:
        series, data_sha256 = load_processed(processed_dir)
        result = evaluate_baselines(series, period)
    except (PreparationError, HoldoutLockedError) as exc:
        print(f"peakwindow: error: {exc}", file=sys.stderr)
        return EXIT_USAGE_OR_IO_ERROR

    record: dict[str, object] = {
        "schema_version": "1",
        "experiment": f"baselines_{period.value}",
        "generated_at_utc": _utc_now(),
        "peakwindow_version": __version__,
        "data": {"processed_sha256": data_sha256, "source_doi": opsd.DOI_URL},
        "config": {
            "models": baselines.BASELINES,
            "reference_model": baselines.REFERENCE_BASELINE,
            "feature_columns": list(FEATURE_COLUMNS),
            "spike_policy": "scored as published; no outlier removal",
        },
        "evaluation": result.to_dict(),
    }
    output_path: Path = args.output_dir / f"baselines_{period.value}.json"
    if not _write_json(output_path, record):
        return EXIT_USAGE_OR_IO_ERROR

    print(
        f"evaluate-baselines [{result.split_version} / {period.value}]: "
        f"{result.origins_evaluated} of {result.origins_in_period} origins evaluated, "
        f"{len(result.exceptions)} data-gap exception(s)",
        file=sys.stderr,
    )
    _print_metrics(result.models)
    print(f"experiment record written to {output_path}", file=sys.stderr)
    return EXIT_OK


def _validate_data(args: argparse.Namespace) -> int:
    input_path: Path = args.input
    try:
        frame = read_canonical_csv(input_path)
    except CsvReadError as exc:
        print(f"peakwindow: error: {exc}", file=sys.stderr)
        return EXIT_USAGE_OR_IO_ERROR

    report = validate_hourly_frame(
        frame, source=str(input_path), synthetic=True if args.synthetic else None
    )
    payload = report.to_json() + "\n"

    output_path: Path | None = args.output
    if output_path is None:
        sys.stdout.write(payload)
    else:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            print(
                f"peakwindow: error: cannot write report to {output_path}: {exc.strerror or exc}",
                file=sys.stderr,
            )
            return EXIT_USAGE_OR_IO_ERROR

    label = " [SYNTHETIC]" if report.synthetic else ""
    status = "PASSED" if report.passed else "FAILED"
    print(
        f"validate-data {status}{label}: {input_path} "
        f"({report.total_rows} rows, {len(report.issues)} issue(s))",
        file=sys.stderr,
    )
    for issue in report.issues:
        print(f"  - {issue}", file=sys.stderr)
    if output_path is not None:
        print(f"report written to {output_path}", file=sys.stderr)
    return EXIT_OK if report.passed else EXIT_QUALITY_FAILURE


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit status."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits on --help, --version and usage errors
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE_OR_IO_ERROR
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)
