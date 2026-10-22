"""Command-line interface for PeakWindow.

Exit status for ``validate-data``:

* 0 -- the input passed every quality check
* 1 -- the input was read but failed one or more quality checks
* 2 -- usage error, or the input/output file could not be read/written
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from peakwindow import __version__
from peakwindow.csv_adapter import CsvReadError, read_canonical_csv
from peakwindow.validation import validate_hourly_frame

EXIT_OK = 0
EXIT_QUALITY_FAILURE = 1
EXIT_USAGE_OR_IO_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="peakwindow",
        description=(
            "PeakWindow: historical Czech electricity-demand forecasting laboratory. "
            "Phase 0 provides offline validation of canonical hourly load files only; "
            "no forecasts are produced."
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
    return parser


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
