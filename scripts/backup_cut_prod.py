r"""Безопасная ручная копия PostgreSQL и локальной папки data для ЦУТ Improvement Auto.

Запускать из корня проекта:
    .\.venv\Scripts\python.exe .\scripts\backup_cut.py

Скрипт НЕ удаляет старые копии, НЕ меняет базу и НЕ выполняет восстановление.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = PROJECT_ROOT.parent / "CUT_Improvement_Auto_Backups_PROD"
PG_BIN = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL" / "17" / "bin"
DB_ARCHIVE = "postgresql.dump"
DATA_ARCHIVE = "data.zip"
MANIFEST = "manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pg_tools() -> tuple[str, str]:
    dump = PG_BIN / "pg_dump.exe"
    restore = PG_BIN / "pg_restore.exe"
    if dump.is_file() and restore.is_file():
        return str(dump), str(restore)
    dump_in_path = shutil.which("pg_dump")
    restore_in_path = shutil.which("pg_restore")
    if dump_in_path and restore_in_path:
        return dump_in_path, restore_in_path
    raise RuntimeError("Не найдены pg_dump и pg_restore от PostgreSQL 17")


def get_db_connection():
    # Берём ту же настройку подключения, что использует приложение.
    # Пароль НЕ выводится и НЕ передаётся через аргументы командной строки.
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    os.environ["DB_NAME"] = "cut_improvement_auto_prod"
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        url = db.get_bind().url
        if url.database != "cut_improvement_auto_prod":
            raise RuntimeError("STOP: Wrong production database")
        with db.connection() as connection:
            connection.exec_driver_sql("SELECT 1")
        if not url.database:
            raise RuntimeError("В конфигурации приложения не указана база данных")
        if not url.username:
            raise RuntimeError("В конфигурации приложения не указан пользователь базы")
        if not url.drivername.startswith("postgresql"):
            raise RuntimeError("Ожидалось подключение PostgreSQL")
        return url


def execute(command: list[str], env: dict[str, str], description: str) -> None:
    result = subprocess.run(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=600,
        check=False,
    )
    if result.returncode:
        # Не показываем соединение / пароль, даже при ошибке команды.
        detail = result.stderr.strip().splitlines()[-1:] or ["без пояснения"]
        raise RuntimeError(f"{description}: код {result.returncode}. {detail[0][:350]}")


def pack_data(folder: Path, archive: Path) -> int:
    folder.mkdir(exist_ok=True, parents=True)
    count = 0
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in sorted(folder.rglob("*")):
            if item.is_symlink():
                raise RuntimeError(f"В data обнаружена символическая ссылка: {item.name}. Копирование остановлено")
            if item.is_file():
                zf.write(item, arcname=Path("data") / item.relative_to(folder))
                count += 1
    # Проверять ZIP можно только после закрытия файла для записи.
    with zipfile.ZipFile(archive, "r") as check_zip:
        broken = check_zip.testzip()
        if broken is not None:
            raise RuntimeError(f"Ошибка проверки ZIP-архива data: {broken}")
    return count


def main() -> int:
    work: Path | None = None
    try:
        if not (PROJECT_ROOT / "backend" / "app" / "db" / "session.py").exists():
            raise RuntimeError("Не найден backend/app/db/session.py. Поместите скрипт в scripts проекта")

        print("=== ЦУТ IMPROVEMENT AUTO: РЕЗЕРВНАЯ КОПИЯ ===", flush=True)
        pg_dump, pg_restore = pg_tools()
        url = get_db_connection()
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="._unfinished_", dir=str(BACKUP_ROOT)))

        env = os.environ.copy()
        if url.password is not None:
            env["PGPASSWORD"] = url.password
        if "sslmode" in url.query and isinstance(url.query["sslmode"], str):
            env["PGSSLMODE"] = url.query["sslmode"]

        base = [
            "--host", url.host or "localhost",
            "--port", str(url.port or 5432),
            "--username", url.username,
            "--no-password",
        ]
        db_path = work / DB_ARCHIVE
        print("1/4: Создаём архив PostgreSQL...", flush=True)
        execute(
            [pg_dump, *base, "--format=custom", "--compress=6", "--no-owner", "--no-acl",
             "--file", str(db_path), "--dbname", url.database],
            env,
            "Не удалось создать архив PostgreSQL",
        )
        if not db_path.is_file() or db_path.stat().st_size == 0:
            raise RuntimeError("Архив PostgreSQL пустой")

        print("2/4: Проверяем формат архива PostgreSQL...", flush=True)
        execute([pg_restore, "--list", str(db_path)], env, "pg_restore не смог прочитать архив")

        print("3/4: Сохраняем папку data...", flush=True)
        source_data = PROJECT_ROOT / "data"
        if not source_data.is_dir():
            raise RuntimeError("Не найдена папка data. Проверим расположение файлов до резервирования")
        data_path = work / DATA_ARCHIVE
        count = pack_data(source_data, data_path)

        manifest = {
            "created_local": datetime.now().astimezone().isoformat(timespec="seconds"),
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "database": url.database,
            "database_user": url.username,
            "database_archive": {"file": DB_ARCHIVE, "bytes": db_path.stat().st_size, "sha256": sha256(db_path)},
            "data_archive": {"file": DATA_ARCHIVE, "files": count, "bytes": data_path.stat().st_size,
                             "sha256": sha256(data_path)},
            "checked": "pg_restore --list + zipfile.testzip (not a full restore test)",
        }
        (work / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        print("4/4: Проверяем контрольные суммы и завершаем...", flush=True)
        for name, data in ((DB_ARCHIVE, manifest["database_archive"]), (DATA_ARCHIVE, manifest["data_archive"])):
            if sha256(work / name) != data["sha256"]:
                raise RuntimeError(f"Не сошлась контрольная сумма файла {name}")
        label = datetime.now().strftime("backup_%Y%m%d_%H%M%S")
        final = BACKUP_ROOT / label
        suffix = 2
        while final.exists():
            final = BACKUP_ROOT / f"{label}_{suffix}"
            suffix += 1
        work.rename(final)
        work = None

        print("\nOK: BACKUP CREATED")
        print(f"DATABASE: {url.database}")
        print(f"DB ARCHIVE: {manifest['database_archive']['bytes'] / (1024 * 1024):.2f} MB")
        print(f"DATA: {count} files")
        print("PG ARCHIVE CHECK: OK (structure readable)")
        print("CHECKSUMS: OK")
        print(f"BACKUP FOLDER: {final}")
        print("OLD BACKUPS: untouched")
        print("IMPORTANT: restore has NOT been tested yet")
        return 0
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        print("Existing backups and live database were not modified.", file=sys.stderr)
        return 1
    finally:
        if work is not None and work.is_dir():
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
