"""Explicit, verified download of a source file into a local cache.

Downloading is always an opt-in setup step (``peakwindow fetch-opsd``); nothing in
the package downloads on import, and tests use ``file://`` URLs only. A file is
written to ``<name>.part`` first and moved into place only after its size and
SHA-256 match the expected values, so the cache never holds an unverified file.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from peakwindow import __version__

MANIFEST_SCHEMA_VERSION = "1"
CHUNK_BYTES = 1 << 20
DOWNLOAD_TIMEOUT_SECONDS = 60

ProgressCallback = Callable[[int, int | None], None]


class AcquisitionError(Exception):
    """Raised when a source file cannot be downloaded, verified or read back."""


@dataclass(frozen=True)
class SourceFile:
    """A remote file with the size and SHA-256 its publisher declares."""

    url: str
    file_name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Manifest:
    """Provenance record written next to a cached source file."""

    source_url: str
    file_name: str
    acquired_at_utc: str
    size_bytes: int
    sha256: str
    declared_sha256: str
    declared_size_bytes: int
    peakwindow_version: str
    citation: str
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".manifest.json")


def read_manifest(path: Path) -> dict[str, Any]:
    """Read the manifest stored next to ``path``."""
    manifest = manifest_path_for(path)
    try:
        data: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AcquisitionError(f"no provenance manifest found at {manifest}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AcquisitionError(f"cannot read provenance manifest {manifest}: {exc}") from exc
    return data


def verify_cached(path: Path, source: SourceFile) -> str:
    """Check that ``path`` exists and matches the declared SHA-256; return the digest."""
    if not path.is_file():
        raise AcquisitionError(f"cached file not found: {path}")
    digest = sha256_of(path)
    if digest != source.sha256:
        raise AcquisitionError(
            f"cached file {path} has SHA-256 {digest}, expected {source.sha256}; "
            "re-run the download with --force"
        )
    return digest


def _download(url: str, part: Path, progress: ProgressCallback | None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": f"peakwindow/{__version__}"})
    digest = hashlib.sha256()
    written = 0
    try:
        with (
            urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,
            part.open("wb") as handle,
        ):
            length = response.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
            while chunk := response.read(CHUNK_BYTES):
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress is not None:
                    progress(written, total)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise AcquisitionError(f"download failed for {url}: {reason}") from exc
    return written, digest.hexdigest()


def fetch(
    source: SourceFile,
    destination: Path,
    *,
    citation: str,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> Manifest | None:
    """Download ``source`` to ``destination`` unless a verified copy is already cached.

    Returns the new manifest, or ``None`` when a cached copy with a manifest was
    re-verified and reused. A cached file without a manifest is never adopted,
    because its acquisition date is unknown; use ``force=True`` to re-download.
    """
    if destination.exists() and not force:
        if not manifest_path_for(destination).is_file():
            raise AcquisitionError(
                f"{destination} exists but has no provenance manifest; "
                "re-run the download with --force"
            )
        verify_cached(destination, source)
        return None

    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    try:
        size, digest = _download(source.url, part, progress)
        if size != source.size_bytes or digest != source.sha256:
            raise AcquisitionError(
                f"downloaded file does not match the declared source: got {size} bytes "
                f"with SHA-256 {digest}, expected {source.size_bytes} bytes with "
                f"SHA-256 {source.sha256}"
            )
        os.replace(part, destination)
    finally:
        part.unlink(missing_ok=True)

    manifest = Manifest(
        source_url=source.url,
        file_name=destination.name,
        acquired_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        size_bytes=size,
        sha256=digest,
        declared_sha256=source.sha256,
        declared_size_bytes=source.size_bytes,
        peakwindow_version=__version__,
        citation=citation,
    )
    manifest_path_for(destination).write_text(manifest.to_json(), encoding="utf-8")
    return manifest
