# -*- coding: utf-8 -*-
r"""Install a safe daily 12:00 Windows backup task with 7/4/12 retention.

Place this file in the project's scripts folder and run from the project root:
    .\.venv\Scripts\python.exe .\scripts\install_cut_smart_backup.py

Installer does not connect to the database, run backups, or remove files.
The scheduled runner creates + verifies a backup BEFORE any old copies may be deleted.
No existing scheduled task or script is overwritten.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "CUT Improvement Auto Daily Backup"
SCHEDULE = "12:00"

RETENTION = r'''# -*- coding: utf-8 -*-
"""Retention policy for validated CUT Improvement Auto backups.

Keep one latest copy for every day of the last 7 calendar days,
for every ISO Monday-based week of the last 4 weeks,
and for every month of the last 12 calendar months.
Also keep the newest two copies and all copies newer than 48 hours.
A backup can satisfy more than one category, so actual count may be <23.
Never delete anything if validations fail or fewer than 3 valid copies exist.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

NAME_PATTERN = re.compile(r"^backup_(\d{8})_(\d{6})(?:_(\d+))?$")
DB_NAME = "cut_improvement_auto"
FILES = ("postgresql.dump", "data.zip")


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _validated_backup(folder: Path) -> datetime:
    match = NAME_PATTERN.fullmatch(folder.name)
    if not match or folder.is_symlink() or not folder.is_dir():
        raise ValueError("Unexpected backup folder name/type: " + folder.name)
    stamp = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    with (folder / "manifest.json").open("r", encoding="utf-8") as fp:
        manifest = json.load(fp)
    if manifest.get("database") != DB_NAME:
        raise ValueError("Wrong source database in " + folder.name)
    for name, key in (("postgresql.dump", "database_archive"), ("data.zip", "data_archive")):
        info = manifest.get(key)
        if not isinstance(info, dict) or info.get("file") != name:
            raise ValueError("Missing manifest entry for " + name + " in " + folder.name)
        target = folder / name
        if not target.is_file() or target.is_symlink() or target.stat().st_size <= 0:
            raise ValueError("Missing/empty archive " + name + " in " + folder.name)
        if target.stat().st_size != info.get("bytes"):
            raise ValueError("Size mismatch in " + folder.name + "/" + name)
        checksum = info.get("sha256")
        if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
            raise ValueError("Invalid checksum in " + folder.name + "/" + name)
        if _digest(target).lower() != checksum.lower():
            raise ValueError("SHA-256 mismatch in " + folder.name + "/" + name)
    with ZipFile(folder / "data.zip", "r") as zf:
        if zf.testzip() is not None:
            raise ValueError("Corrupt data.zip in " + folder.name)
    return stamp


def _keepers(items: list[tuple[Path, datetime]], now: datetime) -> set[Path]:
    # items sorted oldest -> newest; one newest per bucket
    items = sorted(items, key=lambda it: (it[1], it[0].name))
    keep: set[Path] = {folder for folder, _ in items[-2:]}
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    month_index = today.year * 12 + today.month
    daily: dict[str, Path] = {}
    weekly: dict[str, Path] = {}
    monthly: dict[str, Path] = {}
    for folder, stamp in items:
        age = now - stamp
        if age < timedelta(hours=48):
            keep.add(folder)
        date = stamp.date()
        days_ago = (today - date).days
        if 0 <= days_ago < 7:
            daily[date.isoformat()] = folder
        week_start = date - timedelta(days=date.weekday())
        weeks_ago = (monday - week_start).days // 7
        if 0 <= weeks_ago < 4:
            weekly[week_start.isoformat()] = folder
        months_ago = month_index - (date.year * 12 + date.month)
        if 0 <= months_ago < 12:
            monthly[f"{date.year}-{date.month:02d}"] = folder
    keep.update(daily.values())
    keep.update(weekly.values())
    keep.update(monthly.values())
    return keep


def prune_backup_root(root: Path, just_created: Path, *, dry_run: bool = False,
                      now: datetime | None = None) -> tuple[int, int, list[str]]:
    """Returns kept count, deleted count, notes; raises before deleting on uncertainty."""
    now = now or datetime.now()
    root = root.resolve(strict=True)
    if just_created.parent.resolve(strict=True) != root:
        raise RuntimeError("New backup is outside the expected backup root")
    if not just_created.is_dir() or just_created.is_symlink():
        raise RuntimeError("The new backup directory is missing")

    # Never touch unknown folders, ._unfinished, logs or arbitrary user files.
    candidates: list[tuple[Path, datetime]] = []
    for path in root.iterdir():
        if not path.name.startswith("backup_"):
            continue
        if path.is_symlink():
            raise RuntimeError("Unexpected symlink; retention cancelled: " + path.name)
        if not path.is_dir() or not NAME_PATTERN.fullmatch(path.name):
            raise RuntimeError("Unexpected backup_* path; retention cancelled: " + path.name)
        candidates.append((path, _validated_backup(path)))

    if just_created not in {p for p, _ in candidates}:
        raise RuntimeError("New verified backup was not found in the backup root")
    if len(candidates) < 3:
        return len(candidates), 0, ["Fewer than 3 valid backups: nothing deleted"]
    keep = _keepers(candidates, now)
    keep.add(just_created)
    if len(keep) < 2:
        raise RuntimeError("Less than 2 protected backups: cancelled")
    removal = [path for path, _ in sorted(candidates, key=lambda it: (it[1], it[0].name))
               if path not in keep]
    notes: list[str] = []
    for path in removal:
        # Critical: no path outside backup root, no links, never current backup.
        if path.parent.resolve(strict=True) != root or path.is_symlink() or path == just_created:
            raise RuntimeError("Unsafe delete path: " + str(path))
    for path in removal:
        if dry_run:
            notes.append("WOULD DELETE: " + path.name)
        else:
            shutil.rmtree(path)
            notes.append("DELETED: " + path.name)
    return len(keep), len(removal), notes
'''

RUNNER = r'''# -*- coding: utf-8 -*-
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
'''


def quote_ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    try:
        if os.name != "nt":
            raise RuntimeError("Run this installer on Windows")
        project = Path(__file__).resolve().parent.parent
        scripts = project / "scripts"
        backup_script = scripts / "backup_cut.py"
        python_exe = project / ".venv" / "Scripts" / "python.exe"
        if not (project / "backend" / "app" / "db" / "session.py").is_file():
            raise RuntimeError("Project not found. Place file in scripts folder")
        if not backup_script.is_file() or not python_exe.is_file():
            raise RuntimeError("Missing backup_cut.py or project Python environment")
        code = backup_script.read_text(encoding="utf-8-sig")
        compile(code, str(backup_script), "exec")
        # Require the successfully tested ZIP fix and check markers; do not modify backup_cut.py.
        if 'with zipfile.ZipFile(archive, "r") as check_zip:' not in code:
            raise RuntimeError("backup_cut.py does not contain verified ZIP fix; no changes made")
        if "OK: BACKUP CREATED" not in code or "CHECKSUMS: OK" not in code:
            raise RuntimeError("Unexpected backup_cut.py; no changes made")
        for filename, content in (("cut_backup_retention.py", RETENTION),
                                  ("run_cut_smart_backup.py", RUNNER)):
            path = scripts / filename
            compile(content, filename, "exec")
            if path.exists():
                if path.read_text(encoding="utf-8-sig").replace("\r\n", "\n") != content.replace("\r\n", "\n"):
                    raise RuntimeError(filename + " already exists with different contents. Nothing overwritten")
        runner_path = scripts / "run_cut_smart_backup.py"
        # Abort rather than replacing/altering any preexisting scheduled task.
        ps_script = f'''$ErrorActionPreference = 'Stop'
Import-Module ScheduledTasks -ErrorAction Stop
$taskName = {quote_ps(TASK_NAME)}
$old = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $old) {{ throw 'A task with this name already exists; no overwrite performed.' }}
$python = {quote_ps(str(python_exe))}
$runner = {quote_ps(str(runner_path))}
$project = {quote_ps(str(project))}
$action = New-ScheduledTaskAction -Execute $python -Argument ('-u "' + $runner + '"') -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At {quote_ps(SCHEDULE)}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Daily backup at 12:00. Verified PostgreSQL + data. 7 daily, 4 weekly, 12 monthly retention.' -ErrorAction Stop | Out-Null
$actual = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
Write-Output 'OK: TASK INSTALLED'
Write-Output ('TASK: ' + $actual.TaskName)
Write-Output 'SCHEDULE: 12:00 EVERY DAY (computer local time)'
Write-Output 'RETENTION: 7 DAILY + 4 WEEKLY + 12 MONTHLY'
Write-Output 'SAFETY: backups are only deleted after a fresh verified backup'
Write-Output 'REQUIREMENT: Windows user session logged on'
Write-Output 'TASK IS NOT RUN IMMEDIATELY'
'''
        encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
        # Preflight checks FIRST. Then write new files. Register as a separate action.
        # If task creation fails, scripts remain for diagnostics; no backups were touched.
        for filename, content in (("cut_backup_retention.py", RETENTION),
                                  ("run_cut_smart_backup.py", RUNNER)):
            path = scripts / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            cwd=str(project), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, check=False,
        )
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.returncode or "OK: TASK INSTALLED" not in result.stdout:
            if result.stderr.strip():
                print("PowerShell error:", result.stderr.strip()[-1800:])
            raise RuntimeError("Task Scheduler registration failed. No backups deleted or DB modified")
        print("RUNNER:", runner_path)
        print("RETENTION SCRIPT:", scripts / "cut_backup_retention.py")
        print("BACKUP LOG:", project.parent / "CUT_Improvement_Auto_Backups" / "logs" / "last_result.json")
        print("NEXT: run the task once manually to check logging and backup")
        return 0
    except Exception as exc:
        print("ERROR:", type(exc).__name__ + ":", exc)
        print("Live PostgreSQL and existing backup folders were NOT changed by installer.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
