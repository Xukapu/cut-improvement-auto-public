# -*- coding: utf-8 -*-
"""Task Scheduler runner: backup first, validate, then apply conservative retention."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from cut_backup_retention import prune_backup_root

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = PROJECT_ROOT.parent / "CUT_Improvement_Auto_Backups"
LOG_ROOT = BACKUP_ROOT / "logs"
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup_cut.py"


def main() -> int:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = LOG_ROOT / ("run_" + run_id + ".log")
    status_path = LOG_ROOT / "last_result.json"
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    messages: list[str] = []
    success = False
    created: str | None = None
    status = "FAILED"
    try:
        if not BACKUP_SCRIPT.is_file():
            raise RuntimeError("Missing scripts/backup_cut.py")
        proc = subprocess.run(
            [sys.executable, "-u", str(BACKUP_SCRIPT)],
            cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=2400, check=False,
        )
        messages.extend(["=== BACKUP STDOUT ===", proc.stdout,
                         "=== BACKUP STDERR ===", proc.stderr])
        if proc.returncode != 0 or "OK: BACKUP CREATED" not in proc.stdout or "CHECKSUMS: OK" not in proc.stdout:
            raise RuntimeError("Backup did not complete successfully: return code " + str(proc.returncode))
        found = re.findall(r"^BACKUP FOLDER:\s*(.+)\s*$", proc.stdout, flags=re.MULTILINE)
        if len(found) != 1:
            raise RuntimeError("Backup did not return exactly one output folder")
        folder = Path(found[0].strip())
        created = str(folder)
        if not folder.is_dir():
            raise RuntimeError("New backup directory missing")
        success = True
        try:
            kept, deleted, notes = prune_backup_root(BACKUP_ROOT, folder)
            messages.append(f"RETENTION SUCCESS: kept={kept}; deleted={deleted}")
            messages.extend(notes)
            status = "SUCCESS"
        except Exception as retention_error:
            # The new backup is still useful. Never run deletion again blindly.
            status = "BACKUP_OK_RETENTION_FAILED"
            messages.append("RETENTION ERROR (backups not intentionally modified): " + str(retention_error))
    except Exception as exc:
        messages.append("BACKUP ERROR: " + type(exc).__name__ + ": " + str(exc))
        messages.append(traceback.format_exc())
    finally:
        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log_path.write_text(
            "CUT Improvement Auto - daily backup 12:00 local\n"
            "Started UTC: " + started + "\nFinished UTC: " + finished + "\n\n"
            + "\n".join(messages) + "\n", encoding="utf-8")
        result = {
            "status": status,
            "backup_created": success,
            "backup_folder": created,
            "started_utc": started,
            "completed_utc": finished,
            "log_file": str(log_path),
        }
        temporary = status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(status_path)
        print("RESULT:", status)
        if created:
            print("BACKUP:", created)
        print("LOG:", log_path)
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
