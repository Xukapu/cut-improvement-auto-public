# -*- coding: utf-8 -*-
r"""Conservatively archive known one-off CUT Improvement Auto developer files.

Run from project root:
  .\.venv\Scripts\python.exe .\scripts\cleanup_cut_dev_files.py
  .\.venv\Scripts\python.exe .\scripts\cleanup_cut_dev_files.py --apply

Preview is the default. No files are deleted. An external sibling archive stores
moved items with a JSON manifest, and active backup scripts are never moved.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

# Explicit allowlist: do not automatically move any newly discovered script.
ONE_OFF = {
    'add_max_button.py',
    'fix_backup_zip.py',
    'fix_short_reminders.py',
    'install_cancel_ia.py',
    'install_cancel_ia_v2.py',
    'install_max_crash_safety.py',
    'install_max_popup.py',
    'install_max_reliability.py',
    'install_service_reminder_edit.py',
}
MANUAL_TESTS = {
    'max_bot_test.py',
    'test_cut_backup_retention.py',
    'test_cut_backup_retention_fixed.py',
    'test_restore_cut.py',
}
OLD_BACKUP_FILES = (
    re.compile(r'^backup_cut_before_zip_fix_\d{8}_\d{6}\.bak$'),
    re.compile(r'^notification_scheduler_before_crash_safety_\d{8}_\d{6}(?:_\d+)?\.bak$'),
    re.compile(r'^notification_scheduler_before_reliability_\d{8}_\d{6}\.bak$'),
)
OLD_BACKUP_DIRS = (
    re.compile(r'^cancel_backup_\d{8}_\d{6}$'),
    re.compile(r'^service_edit_backup_\d{8}_\d{6}$'),
)
PROTECTED = {
    'backup_cut.py',
    'run_cut_smart_backup.py',
    'cut_backup_retention.py',
    'install_cut_smart_backup.py',
    'bootstrap_owner.py',
    'update_max_token.py',
}


def label(path: Path) -> str | None:
    if path.name in ONE_OFF and path.is_file():
        return 'one_off_installers'
    if path.name in MANUAL_TESTS and path.is_file():
        return 'manual_tests'
    if path.is_file() and any(p.fullmatch(path.name) for p in OLD_BACKUP_FILES):
        return 'old_code_backups'
    if path.is_dir() and any(p.fullmatch(path.name) for p in OLD_BACKUP_DIRS):
        return 'old_code_backups'
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Move allowlisted files outside project (never delete)')
    args = parser.parse_args()

    script_path = Path(__file__).resolve()
    scripts = script_path.parent
    project = scripts.parent
    if (Path.cwd().resolve() != project.resolve()
            or scripts.name.lower() != 'scripts'
            or not all((project / name).exists() for name in ('.git', 'backend', 'frontend', 'pyproject.toml'))):
        print('STOP: run from the expected CUT project root; no changes made.')
        return 2

    archive_base = project.parent / 'CUT_Improvement_Auto_DevArchive'
    if archive_base.resolve() == project.resolve() or project in archive_base.parents:
        print('STOP: archive must be outside the project; no changes made.')
        return 2
    if archive_base.is_symlink():
        print('STOP: archive location is a symlink; no changes made.')
        return 2

    selections: list[tuple[Path, str]] = []
    for entry in sorted(scripts.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_symlink() or entry.name in PROTECTED:
            continue
        category = label(entry)
        if category is not None:
            selections.append((entry, category))

    print('=== CUT DEVELOPMENT FILE ARCHIVE ===')
    print('MODE:', 'APPLY (move to external archive)' if args.apply else 'PREVIEW ONLY (no changes)')
    print('PROJECT:', project)
    print('EXTERNAL ARCHIVE ROOT:', archive_base)
    print('PROTECTED BACKUP SCRIPTS:', ', '.join(sorted(PROTECTED)))
    print('MATCHED ITEMS:', len(selections))
    for source, group in selections:
        print('  ', group, '/', source.name)
    print('UNMATCHED ITEMS: left untouched, including unknown scripts')
    print('NEVER TOUCHED: database, data, .env, Git, backend/tests, frontend/dist, Windows tasks')

    if not args.apply:
        print('PREVIEW COMPLETE: no files moved or deleted')
        print('NEXT: review the list; run with --apply only after approval')
        return 0

    if not selections:
        print('OK: no allowlisted files to archive')
        return 0

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    target_root = archive_base / ('cleanup_' + stamp)
    manifest = []
    moved: list[tuple[Path, Path]] = []
    try:
        target_root.mkdir(parents=True, exist_ok=False)
        for source, group in selections:
            # Only direct children of the scripts directory may be moved.
            if source.parent != scripts or source.is_symlink():
                raise RuntimeError('Source safety check failed for ' + source.name)
            target = target_root / group / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise RuntimeError('Archive destination already exists: ' + str(target))
            shutil.move(str(source), str(target))
            moved.append((source, target))
            manifest.append({'original': str(source), 'archived': str(target), 'group': group})
        (target_root / 'manifest.json').write_text(
            json.dumps({'created_at': datetime.now().isoformat(), 'items': manifest},
                       indent=2, ensure_ascii=False), encoding='utf-8'
        )
    except Exception as exc:
        print('ERROR:', type(exc).__name__, str(exc))
        for original, archived in reversed(moved):
            try:
                if archived.exists() and not original.exists():
                    shutil.move(str(archived), str(original))
                    print('ROLLED BACK:', original.name)
            except Exception as rollback_exc:
                print('MANUAL RESTORE NEEDED:', archived, rollback_exc)
        print('No files were deleted; check archive folder before retrying.')
        return 1

    print('OK: ARCHIVED', len(moved), 'items')
    print('ARCHIVE:', target_root)
    print('MANIFEST:', target_root / 'manifest.json')
    print('ACTIVE BACKUP SCRIPTS: UNTOUCHED')
    print('REAL BACKUPS: UNTOUCHED')
    print('GIT TRACKED FILES: UNTOUCHED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
