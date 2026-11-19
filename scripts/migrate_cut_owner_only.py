"""Safely copy ONLY the existing owner account to the empty CUT production DB.

Run from the project root:
    .\\.venv\\Scripts\\python.exe .\\scripts\\migrate_cut_owner_only.py

No configuration file, existing database, MAX token, queued notification, photo,
or scheduled task is modified. The target INSERT happens in one transaction.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

SOURCE_DB = "cut_improvement_auto"
TARGET_DB = "cut_improvement_auto_prod"
EXPECTED_OWNER_NAME = "Андрей Кузлякин"
EXPECTED_HEAD = "246171d7b8c5"
EXPECTED_DB_USER = "cut_app"
EXPECTED_TABLE_COUNT = 23


def stop(message: str) -> None:
    raise SystemExit("STOP: " + message)


def db_name(connection) -> str:
    return connection.scalar(text("SELECT current_database()"))


def table_counts(connection, names: list[str]) -> dict[str, int]:
    preparer = connection.dialect.identifier_preparer
    return {
        name: int(
            connection.scalar(
                text(f"SELECT count(*) FROM public.{preparer.quote(name)}")
            )
        )
        for name in names
    }


def main() -> None:
    root = Path.cwd()
    if not (root / "alembic.ini").is_file() or not (
        root / "backend/app/db/session.py"
    ).is_file():
        stop("Run this script from the CUT Improvement Auto project root")

    sys.path.insert(0, str(root / "backend"))
    from app.db.session import engine as source_engine

    print("=== CUT OWNER ONLY / SAFE ACCOUNT TRANSFER ===")
    if source_engine.url.get_backend_name() != "postgresql":
        stop("Expected PostgreSQL")
    if source_engine.url.database != SOURCE_DB:
        stop("Application does not point to the expected ORIGINAL database")

    destination_engine = create_engine(
        source_engine.url.set(database=TARGET_DB), pool_pre_ping=True
    )
    try:
        # Never write to the original database. Read-only transaction enforced by PG.
        with source_engine.connect() as old:
            old.execute(text("SET TRANSACTION READ ONLY"))
            if db_name(old) != SOURCE_DB:
                stop("Wrong source database connection")
            if old.scalar(text("SELECT current_user")) != EXPECTED_DB_USER:
                stop("Unexpected source PostgreSQL user")

            owners = old.execute(
                text(
                    "SELECT * FROM public.app_users "
                    "WHERE role = 'owner' AND deleted_at IS NULL"
                )
            ).mappings().all()
            if len(owners) != 1:
                stop(f"Expected one source owner, found {len(owners)}")
            owner = dict(owners[0])

            if owner.get("full_name") != EXPECTED_OWNER_NAME:
                stop("Owner identity does not match the verified account")
            if owner.get("role") != "owner" or owner.get("is_active") is not True:
                stop("Owner account is not active with the correct role")
            if not owner.get("login") or not owner.get("password_hash"):
                stop("Owner login or password hash missing")
            if not owner.get("id"):
                stop("Owner ID missing")

            print("SOURCE DATABASE:", SOURCE_DB)
            print("ACCOUNT:", owner["full_name"])
            print("ROLE: owner")
            print("LOGIN AND PASSWORD HASH: present (values hidden)")
            print("OLD EMPLOYEE LINK: will not be transferred")

            with destination_engine.begin() as new:
                if db_name(new) != TARGET_DB:
                    stop("Wrong destination database connection")
                if new.scalar(text("SELECT current_user")) != EXPECTED_DB_USER:
                    stop("Unexpected destination PostgreSQL user")
                if new.scalar(
                    text("SELECT version_num FROM public.alembic_version")
                ) != EXPECTED_HEAD:
                    stop("Target migrations are not at the expected revision")

                inspector = inspect(new)
                names = inspector.get_table_names(schema="public")
                if len(names) != EXPECTED_TABLE_COUNT:
                    stop(f"Expected {EXPECTED_TABLE_COUNT} target tables; found {len(names)}")
                counts = table_counts(
                    new, [name for name in names if name != "alembic_version"]
                )
                populated = [name for name, count in counts.items() if count]
                if populated:
                    stop("New database is no longer empty: " + ", ".join(populated))

                users = Table(
                    "app_users", MetaData(), schema="public", autoload_with=new
                )
                dest_columns = {column.name for column in users.columns}
                if dest_columns != set(owner):
                    stop("Owner column layout changed; manual review required")
                employee_col = users.c.employee_id
                if not employee_col.nullable:
                    stop("Target owner cannot be unlinked from an employee")

                # Preserve identity, role, login and existing password hash;
                # never copy a linked employee, lockout or old session.
                owner["employee_id"] = None
                if "failed_login_attempts" in owner:
                    owner["failed_login_attempts"] = 0
                if "locked_until" in owner:
                    owner["locked_until"] = None
                if "last_login_at" in owner:
                    owner["last_login_at"] = None

                new.execute(users.insert().values(**owner))

                saved = new.execute(
                    select(users).where(users.c.id == owner["id"])
                ).mappings().one()
                if (
                    saved["full_name"] != EXPECTED_OWNER_NAME
                    or saved["role"] != "owner"
                    or saved["is_active"] is not True
                    or saved["login"] != owner["login"]
                    or saved["password_hash"] != owner["password_hash"]
                    or saved["employee_id"] is not None
                ):
                    raise RuntimeError("Copied owner verification failed; rolling back")
                if new.scalar(text("SELECT count(*) FROM public.app_users")) != 1:
                    raise RuntimeError("Unexpected number of owners; rolling back")
                print("OK: Owner inserted and verified inside a transaction")
            # Commit occurred successfully on exit from `begin()`.

        with destination_engine.connect() as final:
            final.execute(text("SET TRANSACTION READ ONLY"))
            if db_name(final) != TARGET_DB:
                stop("Final verification connected to wrong database")
            counts_after = table_counts(
                final,
                [
                    "app_users", "employees", "user_sessions", "clients", "vehicles",
                    "appointments", "work_orders", "customer_notifications",
                    "client_notification_preferences", "service_reminders",
                    "audit_logs", "sync_devices", "sync_operations",
                ],
            )
        if counts_after["app_users"] != 1 or any(
            count != 0 for name, count in counts_after.items() if name != "app_users"
        ):
            stop("Post-commit state needs review. Do not rerun this script")

        print("\n=== NEW DATABASE VERIFIED ===")
        print("DATABASE:", TARGET_DB)
        for name, count in counts_after.items():
            print(f"{name}: {count}")
        print("\nSUCCESS: ONLY the owner account was copied")
        print("Original database: unchanged")
        print("Application database setting: unchanged")
        print("MAX / backups / photos / Windows tasks: unchanged")
        print("NEXT: Test owner login against the new DB in an ISOLATED process")

    finally:
        destination_engine.dispose()


if __name__ == "__main__":
    main()
