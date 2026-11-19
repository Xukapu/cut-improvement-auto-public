# -*- coding: utf-8 -*-
r"""Synchronize frontend release metadata and service worker cache for CUT Improvement Auto.

Usage (from project root):
    .\.venv\Scripts\python.exe .\scripts\prepare_cut_release_version.py

Edits ONLY frontend/package.json, optional frontend/package-lock.json, and frontend/public/sw.js.
Keeps timestamped originals outside project. Does not touch FastAPI, database, secrets,
existing dist, Windows tasks, or live backups. Does not run npm or network requests.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

TARGET_VERSION = "0.1.0"
EXPECTED_OLD_FRONTEND_VERSION = "0.0.0"
CACHE_PREFIX = "cut-improvement-auto-shell-"


def fail(message: str) -> None:
    raise RuntimeError(message)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def dump_json(obj: object, original: str) -> str:
    # npm's JSON files are conventionally pretty-printed with two-space indentation.
    newline = "\r\n" if "\r\n" in original else "\n"
    result = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    return result.replace("\n", newline)


def replace_cache_version(text: str) -> str:
    pattern = r'(const\s+CACHE_NAME\s*=\s*"' + re.escape(CACHE_PREFIX) + r')([^"\r\n]+)("\s*;)'
    occurrences = list(re.finditer(pattern, text))
    if len(occurrences) != 1:
        fail("Expected exactly one app shell cache name in frontend/public/sw.js")
    actual = occurrences[0].group(2)
    if actual not in ("v1", f"v{TARGET_VERSION}"):
        fail("Unexpected service-worker cache version: " + actual)
    result = re.sub(pattern, lambda m: m.group(1) + f"v{TARGET_VERSION}" + m.group(3), text, count=1)

    # Restrict cleanup to our own cache family: don't clear unrelated CacheStorage.
    if 'key.startsWith("cut-improvement-auto-shell-")' not in result:
        old = "key !==\n                  CACHE_NAME,"
        if result.count(old) != 1:
            fail("Unknown activate cache-cleanup layout. No changes made.")
        result = result.replace(
            old,
            'key.startsWith("cut-improvement-auto-shell-") &&\n'
            '                  key !==\n'
            '                  CACHE_NAME,',
            1,
        )
    return result


def atomic_write(path: Path, data: bytes) -> None:
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
        ) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    required = [root / "backend/app/main.py", root / "backend/app/core/config.py", root / "frontend/package.json", root / "frontend/public/sw.js", root / ".git"]
    if not all(p.exists() for p in required):
        fail("Project layout not recognized; run script from scripts/ of CUT Improvement Auto")

    config = (root / "backend/app/core/config.py").read_text(encoding="utf-8-sig")
    version_match = re.search(r'^\s*app_version\s*:\s*str\s*=\s*[\'\"]([^\'\"]+)[\'\"]', config, re.MULTILINE)
    if not version_match:
        fail("Cannot find backend app_version default. No changes made.")
    backend_version = version_match.group(1)
    if backend_version != TARGET_VERSION:
        fail(f"Backend version {backend_version} != expected {TARGET_VERSION}. No changes made.")

    pkg_path = root / "frontend/package.json"
    pkg_text = pkg_path.read_text(encoding="utf-8-sig")
    pkg = json.loads(pkg_text)
    previous_version = pkg.get("version")
    if previous_version not in (EXPECTED_OLD_FRONTEND_VERSION, TARGET_VERSION):
        fail(f"Frontend package.json version {previous_version!r} is unexpected; no changes made.")
    pkg["version"] = TARGET_VERSION

    changes: dict[Path, bytes] = {}
    new_pkg = dump_json(pkg, pkg_text).encode("utf-8")
    if new_pkg != pkg_path.read_bytes():
        changes[pkg_path] = new_pkg

    lock_path = root / "frontend/package-lock.json"
    if lock_path.exists():
        lock_text = lock_path.read_text(encoding="utf-8-sig")
        lock = json.loads(lock_text)
        lock_version = lock.get("version")
        if lock_version not in (EXPECTED_OLD_FRONTEND_VERSION, TARGET_VERSION):
            fail(f"package-lock.json version {lock_version!r} unexpected; no changes made.")
        lock["version"] = TARGET_VERSION
        packages = lock.get("packages", {})
        if isinstance(packages, dict) and isinstance(packages.get(""), dict):
            root_package = packages[""]
            if root_package.get("version") not in (EXPECTED_OLD_FRONTEND_VERSION, TARGET_VERSION):
                fail("package-lock.json root-package version unexpected; no changes made.")
            root_package["version"] = TARGET_VERSION
        new_lock = dump_json(lock, lock_text).encode("utf-8")
        if new_lock != lock_path.read_bytes():
            changes[lock_path] = new_lock

    sw_path = root / "frontend/public/sw.js"
    sw_text = sw_path.read_text(encoding="utf-8-sig")
    new_sw = replace_cache_version(sw_text).encode("utf-8")
    if new_sw != sw_path.read_bytes():
        changes[sw_path] = new_sw

    print("=== CUT IMPROVEMENT AUTO: RELEASE VERSION PREP ===")
    print("BACKEND DEFAULT:", backend_version)
    print("FRONTEND BEFORE:", previous_version)
    print("FRONTEND TARGET:", TARGET_VERSION)
    print("SERVICE WORKER CACHE:", CACHE_PREFIX + "v" + TARGET_VERSION)
    if not changes:
        print("OK: all versions already aligned; nothing modified")
        return 0

    archive_root = root.parent / "CUT_Improvement_Auto_DevArchive" / "release_metadata_backups"
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = archive_root / ("release_" + stamp)
    backup_dir.mkdir(exist_ok=False)

    originals = {}
    manifest = {"version": TARGET_VERSION, "files": []}
    for path in changes:
        rel = path.relative_to(root)
        old = path.read_bytes()
        originals[path] = old
        dest = backup_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(old)
        manifest["files"].append({"path": rel.as_posix(), "before_sha256": sha256(old), "after_sha256": sha256(changes[path])})
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    written = []
    try:
        for path, contents in changes.items():
            atomic_write(path, contents)
            written.append(path)
        # Read back for integrity.
        for path, contents in changes.items():
            if path.read_bytes() != contents:
                fail("Failed verification after writing " + str(path))
    except Exception:
        for path in reversed(written):
            try:
                atomic_write(path, originals[path])
            except Exception:
                pass
        raise

    for path in changes:
        print("UPDATED:", path.relative_to(root))
    print("BACKUP:", backup_dir)
    print("OK: version metadata and app-specific browser cache policy updated")
    print("OK: backend, DB, MAX, offline store, backup tasks and frontend/dist untouched")
    print("NEXT: build frontend and test it on port 8000 before committing")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("ERROR:", error, file=sys.stderr)
        raise SystemExit(1)
