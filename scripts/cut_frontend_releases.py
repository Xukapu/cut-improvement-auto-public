# -*- coding: utf-8 -*-
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
