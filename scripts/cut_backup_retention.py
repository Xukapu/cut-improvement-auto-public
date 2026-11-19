# -*- coding: utf-8 -*-
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
