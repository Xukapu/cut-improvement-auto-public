# -*- coding: utf-8 -*-
r"""Install release selection for CUT Improvement Auto, safely.

From the project root (after stopping FastAPI):
    .\.venv\Scripts\python.exe .\scripts\install_cut_release_switcher.py

Installs a small CLI for activation and rollback and makes FastAPI read the
persistent active release on startup. Never touches PostgreSQL, photos, .env,
Windows tasks, real backups, or the contents of existing releases.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

RELEASE_LIB = r'''# -*- coding: utf-8 -*-
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
'''

MANAGER = r'''# -*- coding: utf-8 -*-
"""Switch between verified frontend releases (restart FastAPI to take effect).

Run from project root:
    python scripts/cut_frontend_releases.py status
    python scripts/cut_frontend_releases.py activate 0.2.0
    python scripts/cut_frontend_releases.py rollback
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from app.services.frontend_release import (  # noqa: E402
    read_selection,
    releases_root,
    verify_release,
)


def save_selection(current: str, previous: str | None) -> None:
    root = releases_root(PROJECT_ROOT)
    path = root / "active.json"
    if path.is_symlink():
        raise RuntimeError("Refusing to overwrite symlink")
    data = {
        "current": current,
        "previous": previous,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    temp = root / (".active_" + str(os.getpid()) + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="CUT frontend release switcher")
    parser.add_argument("action", choices=("status", "activate", "rollback", "verify"))
    parser.add_argument("version", nargs="?")
    args = parser.parse_args()
    if not (PROJECT_ROOT / "backend" / "app" / "main.py").is_file():
        raise SystemExit("ERROR: wrong project root")
    selected = read_selection(PROJECT_ROOT)
    if args.action == "status":
        print("=== CUT FRONTEND RELEASE STATUS ===")
        if selected is None:
            print("CURRENT: frontend/dist (no active selection)")
        else:
            folder, count = verify_release(PROJECT_ROOT, selected["current"])
            print("CURRENT:", selected["current"])
            print("FILES VERIFIED:", count)
            print("RELEASE:", folder)
            print("PREVIOUS:", selected.get("previous") or "-")
        print("LIVE PROCESS: unchanged; release selection is read at FastAPI startup")
        return
    if args.action == "verify":
        if not args.version:
            parser.error("verify needs a release version")
        folder, count = verify_release(PROJECT_ROOT, args.version)
        print("OK: verified", count, "files in", folder)
        return
    if selected is None:
        raise SystemExit("ERROR: missing active selection; run installer first")
    old = selected["current"]
    if args.action == "activate":
        if not args.version:
            parser.error("activate needs a release version")
        new = args.version
        if new == old:
            print("OK: already active:", old)
            return
        folder, count = verify_release(PROJECT_ROOT, new)
        save_selection(new, old)
        print("OK: SELECTED", new, "(verified", count, "files)")
        print("PREVIOUS:", old)
        print("RELEASE:", folder)
    else:
        new = selected.get("previous")
        if not new:
            raise SystemExit("ERROR: no previous release to roll back to")
        folder, count = verify_release(PROJECT_ROOT, new)
        save_selection(new, old)
        print("OK: ROLLBACK SELECTED", new, "(verified", count, "files)")
        print("PREVIOUS:", old)
        print("RELEASE:", folder)
    print("IMPORTANT: restart FastAPI WITHOUT CUT_FRONTEND_DIST override to apply")
    print("DATABASE / PHOTOS / BACKUPS / .env / WINDOWS TASKS: untouched")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit("ERROR: " + str(exc)) from None
'''

OLD = '''_cut_frontend_override = _cut_environ.get("CUT_FRONTEND_DIST", "").strip()
_cut_frontend_dist = (
    _CutPath(_cut_frontend_override).expanduser()
    if _cut_frontend_override
    else _CutPath(__file__).resolve().parents[2] / "frontend" / "dist"
)'''

NEW = '''from app.services.frontend_release import resolve_frontend_dist as _cut_resolve_frontend_dist

_cut_frontend_override = _cut_environ.get("CUT_FRONTEND_DIST", "").strip()
_cut_frontend_dist = _cut_resolve_frontend_dist(
    _CutPath(__file__).resolve().parents[2],
    _cut_frontend_override,
)'''


def check_release(project_root: Path) -> int:
    versions = project_root / "deploy" / "frontend_releases"
    folder = versions / "0.1.0"
    manifest_path = versions / "0.1.0-manifest.json"
    if not folder.is_dir() or not manifest_path.is_file():
        raise ValueError("Release 0.1.0 or manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("application") != "CUT Improvement Auto" or manifest.get("version") != "0.1.0":
        raise ValueError("Unexpected release manifest")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or "index.html" not in expected:
        raise ValueError("Release manifest has no index.html")
    actual = {}
    for entry in folder.rglob("*"):
        if entry.is_symlink():
            raise ValueError("Symlink inside release")
        if entry.is_file():
            actual[entry.relative_to(folder).as_posix()] = hashlib.sha256(entry.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("Release files or checksums mismatch")
    return len(actual)


def main() -> None:
    root = Path.cwd().resolve()
    main_py = root / "backend" / "app" / "main.py"
    helper = root / "backend" / "app" / "services" / "frontend_release.py"
    manager = root / "scripts" / "cut_frontend_releases.py"
    selection = root / "deploy" / "frontend_releases" / "active.json"
    if not all(p.is_file() for p in (main_py, root / "pyproject.toml", root / "scripts" / "backup_cut.py")):
        raise SystemExit("ERROR: wrong project root or missing project files. No changes made.")
    if any(p.exists() for p in (helper, manager, selection)):
        raise SystemExit("ERROR: release manager already exists; will not overwrite. No changes made.")
    source = main_py.read_text(encoding="utf-8-sig")
    if source.count(OLD) != 1 or "# === CUT FRONTEND STATIC SERVING: END ===" not in source:
        raise SystemExit("ERROR: main.py differs from inspected code. No changes made.")
    try:
        file_count = check_release(root)
    except Exception as err:
        raise SystemExit("ERROR: Release validation failed: " + str(err) + ". No changes made.")
    revised = source.replace(OLD, NEW)
    ast.parse(revised, filename=str(main_py))
    ast.parse(RELEASE_LIB, filename=str(helper))
    ast.parse(MANAGER, filename=str(manager))

    archive_root = root.parent / "CUT_Improvement_Auto_DevArchive" / "release_switcher_backups"
    archive_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    saved_main = archive_root / f"main_before_release_switcher_{timestamp}.bak"
    shutil.copy2(main_py, saved_main)
    if hashlib.sha256(main_py.read_bytes()).digest() != hashlib.sha256(saved_main.read_bytes()).digest():
        saved_main.unlink(missing_ok=True)
        raise SystemExit("ERROR: backup verification failed. No project files changed.")

    added = []
    try:
        with helper.open("x", encoding="utf-8", newline="\n") as fp:
            fp.write(RELEASE_LIB)
        added.append(helper)
        with manager.open("x", encoding="utf-8", newline="\n") as fp:
            fp.write(MANAGER)
        added.append(manager)
        with selection.open("x", encoding="utf-8", newline="\n") as fp:
            json.dump({"current": "0.1.0", "previous": None, "updated_at": datetime.now().astimezone().isoformat()}, fp, ensure_ascii=False, indent=2)
        added.append(selection)
        # Verify the installed module and pointer prior to switching main.py.
        sys.path.insert(0, str(root / "backend"))
        from app.services.frontend_release import resolve_frontend_dist
        resolved = resolve_frontend_dist(root)
        if resolved != root / "deploy" / "frontend_releases" / "0.1.0":
            raise RuntimeError("Release resolver returned unexpected location")
        main_py.write_text(revised, encoding="utf-8", newline="\n")
        compile(main_py.read_bytes(), str(main_py), "exec")
    except BaseException:
        shutil.copy2(saved_main, main_py)
        for p in reversed(added):
            p.unlink(missing_ok=True)
        raise

    print("=== CUT FRONTEND RELEASE SWITCHER ===")
    print(f"OK: verified release 0.1.0 ({file_count} files)")
    print("OK: active version stored in deploy/frontend_releases/active.json")
    print("OK: FastAPI will read active release on NEXT startup")
    print("OK: explicit CUT_FRONTEND_DIST override remains supported")
    print("OK: activation and rollback CLI installed")
    print("OK: syntax check passed")
    print("BACKUP:", saved_main)
    print("DATABASE / MAX / PHOTOS / BACKUPS / .env / WINDOWS TASKS: untouched")
    print("NEXT: .\\.venv\\Scripts\\python.exe .\\scripts\\cut_frontend_releases.py status")
    print("NOTE: current FastAPI process will NOT change until restarted")


if __name__ == "__main__":
    main()
