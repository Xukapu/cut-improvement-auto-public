from pathlib import Path
import hashlib
import ast

original = Path("backend/app/services/notification_scheduler.py")
destination = Path("scripts/notification_scheduler_MAX_safe.py")

expected_original = "aa2a78fcbfa969f6487456152a6e8076512002c94fcb47718d54157f32170fc8"
expected_result = "122c5c2b23358f0a474c7ff7112c5be54f43083a85835ec023d68f42c73e9563"

if not original.is_file():
    raise SystemExit("STOP: Original scheduler not found")

raw = original.read_bytes()
if hashlib.sha256(raw).hexdigest() != expected_original:
    raise SystemExit("STOP: Original file differs from reviewed version")

source = raw.decode("utf-8-sig").replace("\r\n", "\n")

def change(old, new):
    global source
    if source.count(old) != 1:
        raise SystemExit("STOP: Unexpected source code structure")
    source = source.replace(old, new, 1)

change(
    "На этапе тестирования автоматическая отправка MAX\nограничена тестовым клиентом №3.",
    "Безопасные режимы MAX:\n- исходная тестовая БД: только клиент №3;\n- новая рабочая БД: все привязанные клиенты, только после явного включения;\n- любые другие БД: автоматическая отправка выключена."
)

change(
    "from app.db.session import SessionLocal as SessionFactory\n",
    "from app.db.session import SessionLocal as SessionFactory\nfrom app.db.session import engine as db_engine\n"
)

change(
    "# ВАЖНО:\n# Пока отправляем только нашему тестовому клиенту.\n# Для остальных клиентов автоматическая отправка выключена.\nTEST_CLIENT_NUMBER = 3\n",
    '''# MAX_PRODUCTION_SCOPE_V1: never send to all clients from the old test DB.
TEST_CLIENT_NUMBER = 3
LEGACY_DB_NAME = "cut_improvement_auto"
PRODUCTION_DB_NAME = "cut_improvement_auto_prod"
MAX_ALL_CLIENTS_ENV = "CUT_MAX_ALL_CLIENTS_ENABLED"


def get_max_dispatch_mode() -> str:
    """Return 'test', 'all' or 'disabled' for the actual connected DB.

    The new DB is opt-in; setting the flag against the old DB never enables
    broad dispatch. This setting does not alter migrations or database data.
    """
    database_name = db_engine.url.database
    if database_name == LEGACY_DB_NAME:
        return "test"
    if database_name == PRODUCTION_DB_NAME:
        if os.getenv(MAX_ALL_CLIENTS_ENV, "").strip() == "1":
            return "all"
    return "disabled"

'''
)

change(
    '''    Each loop iteration locks just ONE due row; committing releases its lock.
    """
    token = get_max_token()
''',
    '''    Each loop iteration locks just ONE due row; committing releases its lock.
    """
    mode = get_max_dispatch_mode()
    if mode == "disabled":
        logger.debug(
            "MAX: automatic dispatch disabled for database %s",
            db_engine.url.database,
        )
        return 0, 0

    token = get_max_token()
'''
)

change(
    '''                    ClientNotificationPreference.max_user_id,
                )''',
    '''                    ClientNotificationPreference.max_user_id,
                    Client.client_number,
                )'''
)

change(
    '''                    Client.client_number == TEST_CLIENT_NUMBER,
                    Client.deleted_at.is_(None),''',
    '''                    Client.deleted_at.is_(None),'''
)

change(
    '''            row = db.execute(statement).first()
            if row is None:''',
    '''            if mode == "test":
                statement = statement.where(
                    Client.client_number == TEST_CLIENT_NUMBER
                )

            row = db.execute(statement).first()
            if row is None:'''
)

change(
    "            notification, max_user_id = row",
    "            notification, max_user_id, client_number = row"
)

change(
    '''                    notification_id,
                    TEST_CLIENT_NUMBER,
''',
    '''                    notification_id,
                    client_number,
'''
)

ast.parse(source)

result = source.encode("utf-8")

if hashlib.sha256(result).hexdigest() != expected_result:
    raise SystemExit("STOP: Generated file did not pass integrity check")

destination.parent.mkdir(parents=True, exist_ok=True)

if destination.exists() and destination.read_bytes() != result:
    raise SystemExit("STOP: An existing different file would be overwritten")

destination.write_bytes(result)

print("OK: MAX PATCH FILE CREATED")
print("OK: SYNTAX VERIFIED")
print("OK: SHA256 VERIFIED")
print("FILE:", destination.resolve())
print("ORIGINAL SCHEDULER NOT MODIFIED")
print("DATABASES AND MAX NOT TOUCHED")
