"""CLI behavior: help, exit statuses, report output and error messages."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from peakwindow.cli import EXIT_OK, EXIT_QUALITY_FAILURE, EXIT_USAGE_OR_IO_ERROR, main

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "synthetic_hourly_load.csv"


def test_help_exits_zero_and_writes_nothing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--help"]) == EXIT_OK
    assert main(["validate-data", "--help"]) == EXIT_OK

    out = capsys.readouterr().out
    assert "validate-data" in out
    assert "--output" in out
    assert list(tmp_path.iterdir()) == []


def test_valid_fixture_emits_report_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-data", "--input", str(FIXTURE), "--synthetic"]) == EXIT_OK

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["passed"] is True
    assert report["synthetic"] is True
    assert report["total_rows"] == 240
    assert "PASSED [SYNTHETIC]" in captured.err


def test_provenance_is_null_unless_declared(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-data", "--input", str(FIXTURE)]) == EXIT_OK

    captured = capsys.readouterr()
    assert json.loads(captured.out)["synthetic"] is None
    assert "SYNTHETIC" not in captured.err


def test_output_path_creates_parents_and_writes_valid_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output = tmp_path / "nested" / "dir" / "quality.json"

    code = main(["validate-data", "--input", str(FIXTURE), "--output", str(output), "--synthetic"])

    assert code == EXIT_OK
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is True
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "report written to" in captured.err


def test_quality_failure_exits_one_with_issues(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(
        "timestamp_utc,load_mw\n"
        "2020-01-01T00:00:00Z,7000\n"
        "2020-01-01T02:00:00Z,-1\n"
        "2020-01-01T03:00:00,7000\n",
        encoding="utf-8",
    )

    assert main(["validate-data", "--input", str(path)]) == EXIT_QUALITY_FAILURE

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["passed"] is False
    assert report["missing_hour_count"] == 1
    assert report["negative_load_count"] == 1
    assert report["naive_timestamp_count"] == 1
    assert "FAILED" in captured.err
    assert "negative load" in captured.err


def test_missing_columns_exit_one_without_traceback(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "opsd_like.csv"
    path.write_text(
        "utc_timestamp,CZ_load_actual_entsoe_transparency\n2020-01-01T00:00:00Z,1\n",
        encoding="utf-8",
    )

    assert main(["validate-data", "--input", str(path)]) == EXIT_QUALITY_FAILURE

    err = capsys.readouterr().err
    assert "missing required column(s)" in err
    assert "Traceback" not in err


def test_missing_input_exits_two(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = main(["validate-data", "--input", str(tmp_path / "absent.csv")])

    assert code == EXIT_USAGE_OR_IO_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "input file not found" in captured.err


def test_malformed_csv_exits_two(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")

    assert main(["validate-data", "--input", str(path)]) == EXIT_USAGE_OR_IO_ERROR
    assert "empty" in capsys.readouterr().err


def test_unwritable_output_exits_two(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")

    code = main(["validate-data", "--input", str(FIXTURE), "--output", str(blocker / "r.json")])

    assert code == EXIT_USAGE_OR_IO_ERROR
    assert "cannot write report" in capsys.readouterr().err


def test_usage_errors_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_USAGE_OR_IO_ERROR
    assert main(["validate-data"]) == EXIT_USAGE_OR_IO_ERROR
    assert main(["no-such-command"]) == EXIT_USAGE_OR_IO_ERROR
    assert "usage:" in capsys.readouterr().err


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False, timeout=120)


def test_python_dash_m_runs_from_outside_repository(tmp_path: Path) -> None:
    help_result = _run([sys.executable, "-m", "peakwindow", "--help"], cwd=tmp_path)
    assert help_result.returncode == EXIT_OK
    assert "validate-data" in help_result.stdout

    result = _run(
        [sys.executable, "-m", "peakwindow", "validate-data", "--input", str(FIXTURE)],
        cwd=tmp_path,
    )
    assert result.returncode == EXIT_OK, result.stderr
    assert json.loads(result.stdout)["passed"] is True
    assert list(tmp_path.iterdir()) == []


def test_installed_console_script(tmp_path: Path) -> None:
    script = shutil.which("peakwindow", path=str(Path(sys.executable).parent))
    assert script is not None, "console script 'peakwindow' is not installed"

    result = _run([script, "validate-data", "--input", str(FIXTURE), "--synthetic"], cwd=tmp_path)

    assert result.returncode == EXIT_OK, result.stderr
    assert "PASSED [SYNTHETIC]" in result.stderr
