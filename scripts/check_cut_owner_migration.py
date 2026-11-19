"""Read-only safety checks before moving only Andrey's account to the new DB.

Run from the CUT project root:
    .\\.venv\\Scripts\\python.exe .\\scripts\\check_cut_owner_migration.py

This script NEVER copies or changes data, secrets, settings, or notifications.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

OLD_DB = "cut_improvement_auto"
NEW_DB = "cut_improvement_auto_prod"
EXPECTED_REVISION = "246171d7b8c5"
EXPECTED_NAME = "Андрей Кузлякин"


def stop(message: str) -> None:
    raise SystemExit("STOP: " + message)


def main() -> None:
    root = Path.cwd()
    if not (root / "backend/app/db/session.py").is_file():
        stop("Open PowerShell in the CUT project folder")
    sys.path.insert(0, str(root / "backend"))
    from app.db.session import engine as old_engine

    print("=== CUT OWNER ACCOUNT / READ-ONLY PRECHECK ===")
    if old_engine.url.database != OLD_DB:
        stop("Application is not configured for expected old database")

    new_engine = create_engine(old_engine.url.set(database=NEW_DB), pool_pre_ping=True)
    try:
        with old_engine.connect() as old:
            old.execute(text("SET TRANSACTION READ ONLY"))
            if old.scalar(text("SELECT current_database()")) != OLD_DB:
                stop("Old database mismatch")
            owners = old.execute(text("""
                SELECT full_name, login, role, is_active, employee_id, password_hash
                FROM public.app_users
                WHERE role = 'owner' AND deleted_at IS NULL
            """)).mappings().all()
            all_old_accounts = old.scalar(text("SELECT count(*) FROM public.app_users"))

        if len(owners) != 1:
            stop(f"Expected exactly one active/not-deleted owner, found {len(owners)}")
        owner = owners[0]
        if owner["full_name"] != EXPECTED_NAME or not owner["is_active"]:
            stop("Owner's name or active status does not match expected account")
        if not owner["login"] or not owner["password_hash"]:
            stop("Owner account has no usable login/password hash")
        print("OLD DATABASE:", OLD_DB)
        print("OLD TOTAL ACCOUNTS:", all_old_accounts)
        print("ACCOUNT TO TRANSFER:", owner["full_name"])
        print("ROLE:", owner["role"])
        print("ACTIVE:", owner["is_active"])
        print("LOGIN AND PASSWORD HASH: present (values hidden)")
        print("LINKED TO EMPLOYEE:", owner["employee_id"] is not None)

        with new_engine.connect() as new:
            new.execute(text("SET TRANSACTION READ ONLY"))
            if new.scalar(text("SELECT current_database()")) != NEW_DB:
                stop("New database mismatch")
            if new.scalar(text("SELECT current_user")) != "cut_app":
                stop("Wrong technical PostgreSQL user")
            if new.scalar(text("SELECT version_num FROM public.alembic_version")) != EXPECTED_REVISION:
                stop("Unexpected migration revision in new database")
            inspector = inspect(new)
            table_names = inspector.get_table_names(schema="public")
            if len(table_names) != 23:
                stop(f"Unexpected table count: {len(table_names)}")
            account_fields = {c["name"]: c for c in inspector.get_columns("app_users", schema="public")}
            if "employee_id" not in account_fields:
                stop("Employee association field not found")
            employee_optional = bool(account_fields["employee_id"]["nullable"])
            print("NEW DATABASE:", NEW_DB)
            print("NEW MIGRATION:", EXPECTED_REVISION)
            print("NEW TABLES:", len(table_names))
            print("OWNER EMPLOYEE LINK CAN BE EMPTY:", employee_optional)
            for table in (
                "app_users", "employees", "user_sessions", "clients", "vehicles",
                "appointments", "work_orders", "customer_notifications",
                "client_notification_preferences", "service_reminders",
            ):
                if table not in table_names:
                    stop(f"Missing table: {table}")
                count = new.scalar(text(f'SELECT count(*) FROM public."{table}"'))
                print(f"NEW {table}: {count}")
                if count != 0:
                    stop("New database contains existing data: " + table)

        if not employee_optional:
            stop("Owner account requires linked employee; migration plan needs adjustment")
        print("\nCHECK PASSED. Migration can be planned.")
        print("NO DATA COPIED. NO DATABASE, MAX OR BACKUP SETTINGS CHANGED.")
    finally:
        new_engine.dispose()


if __name__ == "__main__":
    main()
