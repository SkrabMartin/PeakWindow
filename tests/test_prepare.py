"""End-to-end fetch-opsd / prepare-opsd on an OPSD-shaped synthetic file (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from peakwindow import opsd
from peakwindow.acquisition import fetch
from peakwindow.cli import EXIT_OK, EXIT_QUALITY_FAILURE, EXIT_USAGE_OR_IO_ERROR, main
from peakwindow.prepare import PreparationError, output_paths, prepare_opsd

from conftest import OpsdWriter, opsd_rows, source_for


def reject_constant(name: str) -> None:
    raise ValueError(f"nonstandard JSON constant {name}")


@pytest.fixture
def cached_source(write_opsd: OpsdWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point opsd.SOURCE at a synthetic file and fetch it into ``<tmp>/data``."""
    loads = ["", "6000.0000", "6100.0000", "", "", "6200.0000", *["6300.0000"] * 5, ""]
    remote = write_opsd(opsd_rows(loads=loads), name="remote.csv")
    monkeypatch.setattr(opsd, "SOURCE", source_for(remote))
    data_dir = tmp_path / "data"
    fetch(opsd.SOURCE, opsd.raw_path(data_dir), citation=opsd.CITATION)
    return data_dir


def test_prepare_writes_series_and_record(cached_source: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "out"

    result = prepare_opsd(cached_source, output_dir)

    assert result.succeeded
    parquet, record_path = output_paths(output_dir)
    series = pd.read_parquet(parquet)
    assert len(series) == 10  # trimmed: one leading and one trailing empty row
    assert int(series["load_mw"].isna().sum()) == 2  # interior gap kept
    assert str(series["timestamp_utc"].dtype).endswith("UTC]")

    record = json.loads(record_path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    assert record["raw"]["sha256_matches_declared"] is True
    assert record["raw"]["acquired_at_utc"].endswith("Z")
    assert record["alignment"]["passed"] is True
    assert record["column_mapping"] == {
        "utc_timestamp": "timestamp_utc",
        "CZ_load_actual_entsoe_transparency": "load_mw",
    }
    assert record["processed"]["rows"] == 10
    assert record["audit"]["missing_load_hours_in_span"] == 2
    assert record["audit"]["quality"]["synthetic"] is False


def test_prepare_refuses_unverified_raw_file(cached_source: Path, tmp_path: Path) -> None:
    raw = opsd.raw_path(cached_source)
    raw.write_text(raw.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(PreparationError, match="fetch-opsd"):
        prepare_opsd(cached_source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_prepare_without_download_explains_next_step(tmp_path: Path) -> None:
    with pytest.raises(PreparationError, match="fetch-opsd"):
        prepare_opsd(tmp_path / "data", tmp_path / "out")


def test_misaligned_source_writes_record_but_no_series(
    write_opsd: OpsdWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = opsd_rows()
    rows.loc[2, opsd.LOCAL_COLUMN] = "2019-10-26T22:00:00+0200"  # one hour off
    remote = write_opsd(rows, name="remote.csv")
    monkeypatch.setattr(opsd, "SOURCE", source_for(remote))
    data_dir = tmp_path / "data"
    fetch(opsd.SOURCE, opsd.raw_path(data_dir), citation=opsd.CITATION)
    parquet, record_path = output_paths(tmp_path / "out")
    parquet.parent.mkdir()
    parquet.write_bytes(b"stale")

    result = prepare_opsd(data_dir, tmp_path / "out")

    assert not result.succeeded
    assert not parquet.exists()  # stale output removed
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["alignment"]["instant_mismatch_rows"] == 1
    assert record["processed"] is None


# --- CLI --------------------------------------------------------------------


def test_cli_fetch_then_prepare(
    write_opsd: OpsdWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(opsd, "SOURCE", source_for(write_opsd(name="remote.csv")))
    data_dir = tmp_path / "data"

    assert main(["fetch-opsd", "--data-dir", str(data_dir)]) == EXIT_OK
    assert "downloaded and verified" in capsys.readouterr().err
    assert main(["fetch-opsd", "--data-dir", str(data_dir)]) == EXIT_OK
    assert "cached copy verified" in capsys.readouterr().err

    assert main(["prepare-opsd", "--data-dir", str(data_dir)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "alignment: ok" in err
    assert "processed series written" in err
    assert output_paths(data_dir / "processed")[0].is_file()


def test_cli_prepare_reports_failure_exit_one(
    write_opsd: OpsdWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = opsd_rows(hours=3, loads=["1", "-5", "2"])
    monkeypatch.setattr(opsd, "SOURCE", source_for(write_opsd(rows, name="remote.csv")))
    data_dir = tmp_path / "data"
    assert main(["fetch-opsd", "--data-dir", str(data_dir)]) == EXIT_OK

    assert main(["prepare-opsd", "--data-dir", str(data_dir)]) == EXIT_QUALITY_FAILURE
    assert "negative" in capsys.readouterr().err


def test_cli_errors_exit_two(
    write_opsd: OpsdWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["prepare-opsd", "--data-dir", str(tmp_path / "none")]) == EXIT_USAGE_OR_IO_ERROR
    assert "fetch-opsd" in capsys.readouterr().err

    remote = write_opsd(name="remote.csv")
    monkeypatch.setattr(opsd, "SOURCE", source_for(remote))
    remote.unlink()
    assert main(["fetch-opsd", "--data-dir", str(tmp_path / "d")]) == EXIT_USAGE_OR_IO_ERROR
    assert "download failed" in capsys.readouterr().err
