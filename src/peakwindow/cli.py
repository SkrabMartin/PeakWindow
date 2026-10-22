"""Command-line interface for PeakWindow.

Exit status:

* 0 -- success (``validate-data``: every quality check passed;
  ``prepare-opsd``: processed series written)
* 1 -- the data was read but failed (``validate-data``: a quality check;
  ``prepare-opsd``: the series could not be processed or UTC/local alignment failed)
* 2 -- usage error, network/download failure, checksum mismatch, or a file that
  could not be read or written
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from peakwindow import __version__, opsd
from peakwindow.acquisition import AcquisitionError, ProgressCallback, fetch
from peakwindow.csv_adapter import CsvReadError, read_canonical_csv
from peakwindow.prepare import PreparationError, prepare_opsd
from peakwindow.validation import validate_hourly_frame

EXIT_OK = 0
EXIT_QUALITY_FAILURE = 1
EXIT_USAGE_OR_IO_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="peakwindow",
        description=(
            "PeakWindow: historical Czech electricity-demand forecasting laboratory. "
            "Provides OPSD data acquisition, offline preparation and data-quality audits; "
            "no forecasts are produced yet."
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
