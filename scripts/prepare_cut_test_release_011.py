# -*- coding: utf-8 -*-
"""Prepare an isolated 0.1.1 frontend switch test from verified 0.1.0.

From the CUT Improvement Auto project root:
  .\\.venv\\Scripts\\python.exe .\\scripts\\prepare_cut_test_release_011.py

This ONLY creates a test release (0.1.1) and its manifest. It does not
activate anything, restart the server, alter source code, or touch database,
photos, backups, environment settings, or the Windows scheduled task.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE_VERSION = "0.1.0"
TEST_VERSION = "0.1.1"
TEST_MARKER = "release-check-0.1.1.txt"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    root = Path.cwd().resolve()
    required = [
        root / "pyproject.toml",
        root / "backend/app/main.py",
        root / "backend/app/services/frontend_release.py",
        root / "scripts/cut_frontend_releases.py",
        root / "scripts/backup_cut.py",
    ]
    if not all(p.is_file() for p in required):
        raise SystemExit("ERROR: Please run from the CUT Improvement Auto project root. Nothing changed.")

    sys.path.insert(0, str(root / "backend"))
    from app.services.frontend_release import read_selection, verify_release

    selection = read_selection(root)
    if selection is None or selection.get("current") != SOURCE_VERSION:
        raise SystemExit(
            "ERROR: Expected active release 0.1.0. "
            "Do not create test candidate until the current release is checked."
        )

    source, source_count = verify_release(root, SOURCE_VERSION)
    releases = root / "deploy/frontend_releases"
    test_folder = releases / TEST_VERSION
    test_manifest = releases / f"{TEST_VERSION}-manifest.json"
    if test_folder.exists() or test_folder.is_symlink() or test_manifest.exists() or test_manifest.is_symlink():
        raise SystemExit("STOP: Test release 0.1.1 already exists. No files overwritten.")

    print("=== CUT FRONTEND TEST RELEASE PREPARATION ===")
    print("CURRENT:", SOURCE_VERSION, "(verified", source_count, "files)")
    print("TEST TARGET:", TEST_VERSION)

    # Use a staging folder inside the same releases filesystem; no live paths replaced.
    staging = Path(tempfile.mkdtemp(prefix="._cut_test_release_", dir=releases))
    promoted_folder = False
    promoted_manifest = False
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True)
        marker = staging / TEST_MARKER
        marker.write_text(
            "CUT Improvement Auto - temporary frontend release check 0.1.1\n"
            "This is the original 0.1.0 interface plus this test marker.\n"
            "No application functions were modified.\n",
            encoding="utf-8",
        )

        hashes = {}
        for file in staging.rglob("*"):
            if file.is_symlink():
                raise RuntimeError("Refusing symlink inside test release")
            if file.is_file():
                hashes[file.relative_to(staging).as_posix()] = digest(file)
        if "index.html" not in hashes or TEST_MARKER not in hashes:
            raise RuntimeError("Incomplete test release")

        manifest = {
            "application": "CUT Improvement Auto",
            "version": TEST_VERSION,
            "files": dict(sorted(hashes.items())),
        }
        staging_manifest = releases / (staging.name + "-manifest.tmp")
        try:
            with staging_manifest.open("x", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
                f.write("\n")

            if test_folder.exists() or test_manifest.exists():
                raise RuntimeError("Test target appeared during preparation; cancelling")
            staging.rename(test_folder)
            promoted_folder = True
            staging_manifest.rename(test_manifest)
            promoted_manifest = True
        finally:
            staging_manifest.unlink(missing_ok=True)

        verified, count = verify_release(root, TEST_VERSION)
        if verified.resolve() != test_folder.resolve() or count != len(hashes):
            raise RuntimeError("Final verification mismatch")

        new_selection = read_selection(root)
        if new_selection != selection:
            raise RuntimeError("Active release unexpectedly changed during test preparation")

        print("OK: test release prepared (verified", count, "files)")
        print("OK: marker present:", TEST_MARKER)
        print("OK: SHA-256 manifest verified")
        print("ACTIVE RELEASE: 0.1.0 (UNCHANGED)")
        print("SOURCE CODE / DATABASE / PHOTOS / BACKUPS / GIT: UNCHANGED")
        print("TEST RELEASE:", test_folder)
        print("NEXT: verify, then activate 0.1.1 with server stopped")
    except Exception:
        # Only remove paths created by THIS run. Never remove pre-existing releases.
        if promoted_manifest:
            test_manifest.unlink(missing_ok=True)
        if promoted_folder:
            shutil.rmtree(test_folder)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("ERROR: " + str(error)) from None
