import sys
import getpass
from pathlib import Path
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path.cwd() / "backend"))

from app.db.session import engine
from app.core.security import verify_password

SOURCE = "cut_improvement_auto"
TARGET = "cut_improvement_auto_prod"

print("\n=== CUT OWNER LOGIN CHECK ===")

if engine.url.database != SOURCE:
    raise SystemExit("STOP: Wrong source database")

new_engine = create_engine(
    engine.url.set(database=TARGET)
)

query = text("""
    SELECT id, full_name, login, password_hash,
           role, is_active, employee_id
    FROM app_users
    WHERE role = 'owner' AND deleted_at IS NULL
""")

try:
    with engine.connect() as old, new_engine.connect() as new:
        old.execute(text("SET TRANSACTION READ ONLY"))
        new.execute(text("SET TRANSACTION READ ONLY"))

        source = old.execute(query).mappings().all()
        target = new.execute(query).mappings().all()

        if len(source) != 1 or len(target) != 1:
            raise SystemExit("STOP: Unexpected owner count")

        a, b = source[0], target[0]

        for field in ("id", "login", "password_hash"):
            if a[field] != b[field]:
                raise SystemExit("STOP: Owner data mismatch")

        if b["role"] != "owner" or not b["is_active"]:
            raise SystemExit("STOP: Owner role or status incorrect")

        if b["employee_id"] is not None:
            raise SystemExit("STOP: Old employee link found")

        count = new.scalar(
            text("SELECT count(*) FROM user_sessions")
        )
        if count != 0:
            raise SystemExit("STOP: Unexpected sessions")

        print("OK: Owner account verified")
        print("OK: Login and password hash preserved")
        print("OK: No old sessions or employee link")

        login = input("\nOwner login: ").strip()
        password = getpass.getpass("Owner password: ")

        valid = (
            login.casefold() == b["login"].casefold()
            and verify_password(password, b["password_hash"])
        )

        password = None

        if not valid:
            raise SystemExit("FAILED: Invalid login or password")

        print("\nOK: OWNER LOGIN AND PASSWORD VERIFIED")
        print("DATABASE:", TARGET)
        print("NO DATABASE CHANGES")

finally:
    new_engine.dispose()
