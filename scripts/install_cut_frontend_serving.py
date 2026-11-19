# -*- coding: utf-8 -*-
"""Install static React delivery via FastAPI, with defensive guards and backup.

Run only from CUT Improvement Auto project root:
    python scripts/install_cut_frontend_serving.py

It does not contact network, start servers, or modify DB.
"""
from __future__ import annotations

import ast
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

START = "# === CUT FRONTEND STATIC SERVING: BEGIN ==="
END = "# === CUT FRONTEND STATIC SERVING: END ==="

BLOCK = '''\n\n# === CUT FRONTEND STATIC SERVING: BEGIN ===
# The mount is deliberately LAST. API, MAX routes and /health always win.
# Vite dev mode at :5173 is unaffected.
from os import environ as _cut_environ
from pathlib import Path as _CutPath
from fastapi.staticfiles import StaticFiles as _CutStaticFiles

_cut_frontend_override = _cut_environ.get("CUT_FRONTEND_DIST", "").strip()
_cut_frontend_dist = (
    _CutPath(_cut_frontend_override).expanduser()
    if _cut_frontend_override
    else _CutPath(__file__).resolve().parents[2] / "frontend" / "dist"
)
if _cut_frontend_dist.is_dir() and (_cut_frontend_dist / "index.html").is_file():
    app.mount(
        "/",
        _CutStaticFiles(directory=str(_cut_frontend_dist), html=True),
        name="cut_frontend",
    )
# === CUT FRONTEND STATIC SERVING: END ===
'''


def die(message: str) -> None:
    raise SystemExit("ERROR: " + message + "\nNo project files modified.")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    root = Path.cwd().resolve()
    main_py = root / "backend" / "app" / "main.py"
    dist = root / "frontend" / "dist"
    scripts = root / "scripts"
    required = [root / "pyproject.toml", root / "frontend" / "package.json", scripts / "backup_cut.py", main_py, dist / "index.html"]
    if not all(p.is_file() for p in required):
        die("This does not look like the expected project root, or frontend/dist is not built.")

    source = main_py.read_text(encoding="utf-8-sig")
    if START in source or END in source:
        die("Static frontend block already present; refusing to install twice.")
    if 'app.mount(' in source and ('"/"' in source or "'/'" in source):
        die("An existing mount was found. Review main.py manually before proceeding.")
    guards = (
        "app = FastAPI(",
        "app.include_router(api_router)",
        "app.include_router(",
        "def healthcheck()",
        'prefix="/api/v1"',
    )
    absent = [guard for guard in guards if guard not in source]
    if absent:
        die("main.py differs from previously inspected version: " + ", ".join(absent))

    tree = ast.parse(source, filename=str(main_py))
    app_mount_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "mount":
            app_mount_calls.append(node)
    if app_mount_calls:
        die("main.py already mounts static content; refusing an unreviewed modification.")
    if not (dist / "assets").is_dir():
        die("frontend/dist/assets not found. Check that production build is complete.")

    revised = source.rstrip() + "\n" + BLOCK
    ast.parse(revised, filename=str(main_py))

    scripts.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = scripts / f"main_before_frontend_serving_{stamp}.bak"
    while backup.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = scripts / f"main_before_frontend_serving_{stamp}.bak"
    original_digest = sha(main_py)
    shutil.copy2(main_py, backup)
    if sha(backup) != original_digest:
        backup.unlink(missing_ok=True)
        die("Could not verify source backup.")

    try:
        main_py.write_text(revised, encoding="utf-8", newline="\n")
        compile(main_py.read_bytes(), str(main_py), "exec")
    except BaseException:
        shutil.copy2(backup, main_py)
        raise

    print("OK: FastAPI frontend static serving installed.")
    print("OK: /api/v1, MAX routes and /health keep their priority.")
    print("OK: frontend/dist root, assets, sw.js and manifest are served.")
    print("OK: Vite development port 5173 is unchanged.")
    print("OK: Python syntax check passed.")
    print("OK: no database, Git, or backup schedule changes.")
    print("BACKUP:", backup)
    print("NEXT: restart FastAPI, then check http://127.0.0.1:8000/")


if __name__ == "__main__":
    main()
