from types import SimpleNamespace

from app.models.notification import CustomerNotification, NotificationStatus
from app.services import notification_scheduler


class DummySession:
    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False


class DummySessionFactory:
    def __call__(self):
        return DummySession()


def test_scheduler_interval_is_one_minute() -> None:
    assert notification_scheduler.CHECK_INTERVAL_SECONDS == 60


def test_scheduler_processes_due_reminders(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        notification_scheduler,
        "SessionFactory",
        DummySessionFactory(),
    )

    calls = []

    def fake_prepare(db):
        calls.append(db)
        return 3

    monkeypatch.setattr(
        notification_scheduler,
        "prepare_due_service_reminders",
        fake_prepare,
    )

    result = notification_scheduler.process_due_service_reminders_once()

    assert result == 3
    assert len(calls) == 1
    assert isinstance(
        calls[0],
        DummySession,
    )


def test_explicit_rate_limit_retries_are_bounded() -> None:
    notice = CustomerNotification()
    error = RuntimeError("MAX API: HTTP 429")

    for attempt in range(len(notification_scheduler.MAX_RATE_LIMIT_RETRY_SECONDS)):
        notification_scheduler._handle_max_delivery_error(notice, error)
        assert notice.status == NotificationStatus.PREPARED
        assert notice.error_message.startswith(f"MAX_RETRY:{attempt + 1}:")

    notification_scheduler._handle_max_delivery_error(notice, error)
    assert notice.status == NotificationStatus.FAILED


def test_ambiguous_send_failure_needs_review() -> None:
    notice = CustomerNotification()
    notification_scheduler._handle_max_delivery_error(notice, TimeoutError())
    assert notice.status == NotificationStatus.FAILED


def test_dispatch_mode_scopes_test_and_requires_production_for_all(monkeypatch) -> None:
    config = SimpleNamespace(
        max_dispatch_mode="disabled",
        max_test_client_number=None,
        is_production=False,
        model_fields_set={"max_dispatch_mode"},
    )
    engine = SimpleNamespace(url=SimpleNamespace(database="new_sto_db"))
    monkeypatch.setattr(notification_scheduler, "get_settings", lambda: config)
    monkeypatch.setattr(notification_scheduler, "db_engine", engine)

    assert notification_scheduler.get_max_dispatch_mode() == "disabled"
    config.max_dispatch_mode = "test"
    assert notification_scheduler.get_max_dispatch_mode() == "disabled"
    config.max_test_client_number = 42
    assert notification_scheduler.get_max_dispatch_mode() == "test"
    config.max_dispatch_mode = "all"
    assert notification_scheduler.get_max_dispatch_mode() == "disabled"
    config.is_production = True
    assert notification_scheduler.get_max_dispatch_mode() == "all"
    engine.url.database = notification_scheduler.LEGACY_DB_NAME
    assert notification_scheduler.get_max_dispatch_mode() == "disabled"
