# -*- coding: utf-8 -*-
"""Read-only release verification and active frontend resolution."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def releases_root(project_root: Path) -> Path:
    return project_root / "deploy" / "frontend_releases"


def verify_release(project_root: Path, version: str) -> tuple[Path, int]:
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError("Invalid release version")
    root = releases_root(project_root)
    folder = root / version
    manifest_path = root / f"{version}-manifest.json"
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError("Release folder missing or unsafe: " + version)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Release manifest missing or unsafe: " + version)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("application") != "CUT Improvement Auto":
        raise ValueError("Unexpected release application")
    if manifest.get("version") != version:
        raise ValueError("Release manifest version mismatch")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Manifest has no files")
    if "index.html" not in expected:
        raise ValueError("Release index.html missing from manifest")

    actual = {}
    for entry in folder.rglob("*"):
        if entry.is_symlink():
            raise ValueError("Symlink not allowed in release: " + str(entry))
        if entry.is_file():
            rel = entry.relative_to(folder).as_posix()
            hasher = hashlib.sha256()
            with entry.open("rb") as fp:
                for block in iter(lambda: fp.read(1024 * 1024), b""):
                    hasher.update(block)
            actual[rel] = hasher.hexdigest()
    if set(actual) != set(expected):
        raise ValueError("Release file list does not match manifest")
    for rel, value in expected.items():
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            raise ValueError("Invalid SHA-256 value in release manifest")
        if actual[rel].lower() != value.lower():
            raise ValueError("Release SHA-256 mismatch: " + rel)
    return folder, len(actual)


def read_selection(project_root: Path) -> dict | None:
    path = releases_root(project_root) / "active.json"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("Unsafe active release selection")
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("Invalid active release selection")
    version = result.get("current")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError("Invalid active frontend version")
    previous = result.get("previous")
    if previous is not None and (
        not isinstance(previous, str) or not VERSION_RE.fullmatch(previous)
    ):
        raise ValueError("Invalid previous frontend version")
    return result


def resolve_frontend_dist(project_root: Path, explicit_override: str = "") -> Path:
    # Explicit override remains highest priority, as in the previous FastAPI version.
    if explicit_override:
        return Path(explicit_override).expanduser()
    selected = read_selection(project_root)
    if selected is None:
        return project_root / "frontend" / "dist"
    # Verify every startup: a missing/damaged release should not be served.
    folder, _ = verify_release(project_root, selected["current"])
    return folder
