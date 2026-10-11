"""Model bundles: versioned, verifiable, portable model storage.

A bundle is one directory (``<model_dir>/artifacts/<version>/``) holding:

* the model files in each library's native, pickle-free format (``models/<target>/``);
* ``pipeline.json.gz`` — the fitted data pipeline (scaler, PCA, SVM, DI, DBSCAN);
* ``bundle.json`` — strategy, feature names, targets, label statistics, thresholds;
* ``manifest.json`` — SHA-256 and size of every file, library and Python versions, the
  training data fingerprint and the creation time.

``load_bundle`` verifies every hash before anything is used: a modified or truncated
file raises ``IntegrityError`` and the model is not loaded. ``export_bundle`` /
``import_bundle`` move a bundle between hosts as one ``.tar.gz`` (extraction refuses
absolute paths, ``..`` and links). ``purge`` keeps the newest ``keep`` bundles and never
deletes a protected one (champion, shadow, a rollback target).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MANIFEST = "manifest.json"


class IntegrityError(ValueError):
    """A bundle file does not match its manifest."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json_gz(path: Path, data: Any) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as fh:
        json.dump(data, fh, separators=(",", ":"))


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_manifest(bundle: Path, *, version: str, extra: dict) -> dict:
    from skopaq.adaptive.models import library_versions

    files = {}
    for p in sorted(bundle.rglob("*")):
        if p.is_file() and p.name != MANIFEST:
            rel = p.relative_to(bundle).as_posix()
            files[rel] = {"sha256": _sha256(p), "bytes": p.stat().st_size}
    manifest = {"format": "skopaq-adaptive-bundle/1", "version": version,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "python": platform.python_version(), "libraries": library_versions(),
                "files": files, **extra}
    (bundle / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return manifest


def verify(bundle: Path) -> dict:
    """The manifest, after checking every listed file (and that no file is unlisted)."""
    bundle = Path(bundle)
    try:
        manifest = json.loads((bundle / MANIFEST).read_text())
    except (OSError, ValueError) as exc:
        raise IntegrityError(f"{bundle}: no readable manifest ({exc})") from exc
    listed = manifest.get("files") or {}
    for rel, info in listed.items():
        p = bundle / rel
        if not p.is_file():
            raise IntegrityError(f"{bundle.name}: {rel} is missing")
        if p.stat().st_size != info["bytes"] or _sha256(p) != info["sha256"]:
            raise IntegrityError(f"{bundle.name}: {rel} does not match its checksum")
    extra = [p.relative_to(bundle).as_posix() for p in bundle.rglob("*")
             if p.is_file() and p.name != MANIFEST
             and p.relative_to(bundle).as_posix() not in listed
             and not p.name.endswith("predictions.jsonl")]
    if extra:
        raise IntegrityError(f"{bundle.name}: unlisted files {extra[:3]}")
    return manifest


def export_bundle(bundle: Path, target: Path) -> Path:
    verify(bundle)
    target = Path(target)
    with tarfile.open(target, "w:gz") as tar:
        tar.add(bundle, arcname=Path(bundle).name)
    return target


def import_bundle(archive: Path, artifacts_dir: Path) -> Path:
    """Unpack a bundle archive safely and verify it; returns the bundle directory."""
    artifacts_dir = Path(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        roots = {m.name.split("/")[0] for m in members}
        if len(roots) != 1:
            raise IntegrityError("the archive must hold exactly one bundle directory")
        for m in members:
            if m.issym() or m.islnk() or m.isdev() or os.path.isabs(m.name) \
                    or ".." in Path(m.name).parts:
                raise IntegrityError(f"unsafe archive member {m.name!r}")
        root = roots.pop()
        dest = artifacts_dir / root
        if dest.exists():
            raise IntegrityError(f"bundle {root} already exists")
        tar.extractall(artifacts_dir, members=members, filter="data")
    verify(dest)
    return dest


def purge(artifacts_dir: Path, *, keep: int, protected: Iterable[str]) -> list[str]:
    """Delete all but the newest ``keep`` bundles (protected ones always stay)."""
    artifacts_dir = Path(artifacts_dir)
    if keep < 1 or not artifacts_dir.is_dir():
        return []
    protected = set(p for p in protected if p)
    bundles = sorted((p for p in artifacts_dir.iterdir() if (p / MANIFEST).is_file()),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for p in bundles[keep:]:
        if p.name in protected:
            continue
        shutil.rmtree(p, ignore_errors=True)
        removed.append(p.name)
    return removed
