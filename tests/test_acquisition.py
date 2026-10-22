"""Download, verification and cache behavior, using file:// URLs only."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from peakwindow.acquisition import (
    AcquisitionError,
    fetch,
    manifest_path_for,
    read_manifest,
    verify_cached,
)

from conftest import OpsdWriter, source_for

CITATION = "test citation"


def test_fetch_downloads_verifies_and_writes_manifest(
    write_opsd: OpsdWriter, tmp_path: Path
) -> None:
    source = source_for(write_opsd(name="remote.csv"))
    destination = tmp_path / "cache" / "raw" / "file.csv"

    manifest = fetch(source, destination, citation=CITATION)

    assert manifest is not None
    assert destination.read_bytes() == (tmp_path / "remote.csv").read_bytes()
    assert manifest.sha256 == source.sha256
    assert manifest.size_bytes == source.size_bytes
    stored = read_manifest(destination)
    assert stored["sha256"] == source.sha256
    assert stored["source_url"] == source.url
    assert stored["citation"] == CITATION
    assert stored["acquired_at_utc"].endswith("Z")
    assert not destination.with_name(destination.name + ".part").exists()


def test_verified_cache_is_reused_without_download(write_opsd: OpsdWriter, tmp_path: Path) -> None:
    remote = write_opsd(name="remote.csv")
    source = source_for(remote)
    destination = tmp_path / "file.csv"
    fetch(source, destination, citation=CITATION)
    manifest_before = manifest_path_for(destination).read_text(encoding="utf-8")
    remote.unlink()  # a second download would now fail

    assert fetch(source, destination, citation=CITATION) is None
    assert manifest_path_for(destination).read_text(encoding="utf-8") == manifest_before


def test_checksum_mismatch_leaves_nothing_in_cache(write_opsd: OpsdWriter, tmp_path: Path) -> None:
    source = replace(source_for(write_opsd(name="remote.csv")), sha256="0" * 64)
    destination = tmp_path / "file.csv"

    with pytest.raises(AcquisitionError, match="does not match the declared source"):
        fetch(source, destination, citation=CITATION)

    assert list(tmp_path.glob("file.csv*")) == []


def test_size_mismatch_is_rejected(write_opsd: OpsdWriter, tmp_path: Path) -> None:
    source = source_for(write_opsd(name="remote.csv"))
    source = replace(source, size_bytes=source.size_bytes + 1)

    with pytest.raises(AcquisitionError, match="does not match"):
        fetch(source, tmp_path / "file.csv", citation=CITATION)


def test_tampered_cache_is_detected(write_opsd: OpsdWriter, tmp_path: Path) -> None:
    source = source_for(write_opsd(name="remote.csv"))
    destination = tmp_path / "file.csv"
    fetch(source, destination, citation=CITATION)
    destination.write_bytes(destination.read_bytes() + b"x")

    with pytest.raises(AcquisitionError, match="re-run the download with --force"):
        fetch(source, destination, citation=CITATION)
    with pytest.raises(AcquisitionError, match="SHA-256"):
        verify_cached(destination, source)


def test_force_replaces_tampered_cache(write_opsd: OpsdWriter, tmp_path: Path) -> None:
    source = source_for(write_opsd(name="remote.csv"))
    destination = tmp_path / "file.csv"
    fetch(source, destination, citation=CITATION)
    destination.write_bytes(b"tampered")

    assert fetch(source, destination, citation=CITATION, force=True) is not None
    assert verify_cached(destination, source) == source.sha256


def test_cached_file_without_manifest_is_not_adopted(
    write_opsd: OpsdWriter, tmp_path: Path
) -> None:
    remote = write_opsd(name="remote.csv")
    source = source_for(remote)
    destination = tmp_path / "file.csv"
    destination.write_bytes(remote.read_bytes())

    with pytest.raises(AcquisitionError, match="no provenance manifest"):
        fetch(source, destination, citation=CITATION)


def test_unreachable_source_raises_readable_error(write_opsd: OpsdWriter, tmp_path: Path) -> None:
    remote = write_opsd(name="remote.csv")
    source = source_for(remote)
    remote.unlink()

    with pytest.raises(AcquisitionError, match="download failed"):
        fetch(source, tmp_path / "file.csv", citation=CITATION)
    assert list(tmp_path.glob("file.csv*")) == []


def test_unreadable_manifest(tmp_path: Path) -> None:
    target = tmp_path / "file.csv"
    with pytest.raises(AcquisitionError, match="no provenance manifest"):
        read_manifest(target)
    manifest_path_for(target).write_text("{not json", encoding="utf-8")
    with pytest.raises(AcquisitionError, match="cannot read provenance manifest"):
        read_manifest(target)


def test_progress_callback_reports_bytes(write_opsd: OpsdWriter, tmp_path: Path) -> None:
    source = source_for(write_opsd(name="remote.csv"))
    calls: list[tuple[int, int | None]] = []

    fetch(
        source, tmp_path / "file.csv", citation=CITATION, progress=lambda w, t: calls.append((w, t))
    )

    assert calls
    assert calls[-1][0] == source.size_bytes
    assert json.loads(manifest_path_for(tmp_path / "file.csv").read_text())["schema_version"] == "1"
