
"""Планировщик и отправка уведомлений клиентам СТО.

Режим MAX по умолчанию выключен. Тестовый режим отправляет одному
выбранному клиенту, рабочий режим отправляет привязанным клиентам.
"""

import asyncio
import logging
import os
import re
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal as SessionFactory
from app.db.session import engine as db_engine
from app.models.appointment import Appointment
from app.models.client import Client
from app.models.notification import (
    ClientNotificationPreference,
    CustomerNotification,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
)
from app.models.work_order import WorkOrder, WorkOrderStatus
from app.services.max_bot_service import _api_request
from app.services.notification import (
    appointment_datetime,
    prepare_due_service_reminders,
)

logger = logging.getLogger("uvicorn.error")

CHECK_INTERVAL_SECONDS = 60
MAX_MESSAGES_PER_CHECK = 10
# MAX_RELIABILITY_PATCH_V1: explicit rate limits only; ambiguous sends never auto-retry.
MAX_RATE_LIMIT_RETRY_SECONDS = (60, 300, 900)

# MAX_PRODUCTION_SCOPE_V1: never send to all clients from the old test DB.
TEST_CLIENT_NUMBER = 3
LEGACY_DB_NAME = "cut_improvement_auto"
PRODUCTION_DB_NAME = "cut_improvement_auto_prod"
MAX_ALL_CLIENTS_ENV = "CUT_MAX_ALL_CLIENTS_ENABLED"


def get_max_dispatch_mode() -> str:
    settings = get_settings()
    database_name = db_engine.url.database
    if settings.max_dispatch_mode == "test" and (
        settings.max_test_client_number is not None or database_name == LEGACY_DB_NAME
    ):
        return "test"
    if (
        settings.max_dispatch_mode == "all"
        and settings.is_production
        and database_name != LEGACY_DB_NAME
    ):
        return "all"
    # Existing Windows installations use a different setting. Keep it scoped
    # to their production DB, and respect an explicitly configured mode.
    if (
        "max_dispatch_mode" not in settings.model_fields_set
        and database_name == PRODUCTION_DB_NAME
        and os.getenv(MAX_ALL_CLIENTS_ENV, "").strip() == "1"
    ):
        return "all"
    return "disabled"


_scheduler_task: asyncio.Task[None] | None = None


def process_due_service_reminders_once() -> int:
    """Подготовка сервисных напоминаний."""
    with SessionFactory() as db:
        return prepare_due_service_reminders(db)


def get_max_token() -> str | None:
    """Получение токена из настроек программы."""
    settings = get_settings()

    if settings.max_bot_token is None:
        return None

    token = (
        settings.max_bot_token
        .get_secret_value()
        .strip()
    )

    return token or None


def _rate_limit_error(error: Exception) -> bool:
    """Return True only if MAX explicitly rejected the request with HTTP 429."""
    code = getattr(error, "code", None)
    if code is None:
        response = getattr(error, "response", None)
        code = getattr(response, "status_code", None)

    try:
        if int(code) == 429:
            return True
    except (TypeError, ValueError):
        pass

    detail = str(error)
    return bool(
        re.search(
            r"(?:\bHTTP\s*429\b|\b429\s+Too\s+Many\s+Requests\b|\bstatus\s*(?:code\s*)?[:=]?\s*429\b)",
            detail,
            flags=re.IGNORECASE,
        )
    )


def _rate_limit_retry_attempt(notification: CustomerNotification) -> int:
    match = re.match(r"^MAX_RETRY:(\d+):", notification.error_message or "")
    return int(match.group(1)) if match else 0


def _handle_max_delivery_error(
    notification: CustomerNotification,
    error: Exception,
) -> None:
    """Keep rejected 429 requests for later; do not retry ambiguous errors.

    We intentionally keep the original DB schema: scheduled_for becomes the
    next attempt time for an explicit rate-limit rejection. Other failures
    require staff review, because POST /messages has no documented
    idempotency key and a lost response may mean the message was delivered.
    """
    if _rate_limit_error(error):
        previous = _rate_limit_retry_attempt(notification)
        if previous < len(MAX_RATE_LIMIT_RETRY_SECONDS):
            next_attempt = previous + 1
            delay = MAX_RATE_LIMIT_RETRY_SECONDS[previous]
            notification.status = NotificationStatus.PREPARED
            notification.scheduled_for = (
                datetime.now(UTC) + timedelta(seconds=delay)
            )
            notification.error_message = (
                f"MAX_RETRY:{next_attempt}: HTTP 429; "
                f"retry in {delay}s; {str(error)}"
            )[:2000]
            logger.warning(
                "MAX: rate limit; notification %s retry %s/%s in %ss",
                notification.id,
                next_attempt,
                len(MAX_RATE_LIMIT_RETRY_SECONDS),
                delay,
            )
            return

    notification.status = NotificationStatus.FAILED
    notification.error_message = (
        "MAX: delivery status requires manual review; " + str(error)
    )[:2000]


def notification_is_current(
    db,
    notification: CustomerNotification,
) -> bool:
    """Проверяем актуальность события перед отправкой."""

    if notification.kind == NotificationKind.WORK_ORDER_READY:
        if notification.work_order_id is None:
            return False
        order = db.get(WorkOrder, notification.work_order_id)
        return (
            order is not None
            and order.deleted_at is None
            and order.status == WorkOrderStatus.READY
            and order.client_id == notification.client_id
            and order.vehicle_id == notification.vehicle_id
        )

    if notification.appointment_id is None:
        return notification.kind == NotificationKind.SERVICE_REMINDER

    appointment = db.get(
        Appointment,
        notification.appointment_id,
    )

    if appointment is None:
        return False

    if appointment.deleted_at is not None:
        return False

    appointment_status = getattr(
        appointment.status,
        "value",
        appointment.status,
    )

    if notification.kind == NotificationKind.APPOINTMENT_CANCELLATION:
        return appointment_status == "cancelled"

    if appointment_status != "scheduled":
        return False

    return appointment_datetime(appointment) > datetime.now(UTC)


def dispatch_due_max_once() -> tuple[int, int]:
    """Send MAX messages with durable per-message claim and status commits.

    MAX_CRASH_SAFE_CLAIM_V1: before POST, commit a FAILED/manual-review
    claim. After a crash, no scheduler auto-resends an ambiguous message.
    Each loop iteration locks just ONE due row; committing releases its lock.
    """
    mode = get_max_dispatch_mode()
    if mode == "disabled":
        logger.debug(
            "MAX: automatic dispatch disabled for database %s",
            db_engine.url.database,
        )
        return 0, 0

    token = get_max_token()
    if not token:
        logger.warning("MAX: токен отсутствует, отправка пропущена")
        return 0, 0

    sent_count = 0
    failed_count = 0
    checked_availability = False
    test_client_number = get_settings().max_test_client_number or TEST_CLIENT_NUMBER

    for _ in range(MAX_MESSAGES_PER_CHECK):
        with SessionFactory() as db:
            statement = (
                select(
                    CustomerNotification,
                    ClientNotificationPreference.max_user_id,
                    Client.client_number,
                )
                .join(
                    Client,
                    CustomerNotification.client_id == Client.id,
                )
                .join(
                    ClientNotificationPreference,
                    ClientNotificationPreference.client_id == Client.id,
                )
                .where(
                    Client.deleted_at.is_(None),
                    Client.archived_at.is_(None),
                    ClientNotificationPreference.deleted_at.is_(None),
                    ClientNotificationPreference.max_enabled.is_(True),
                    ClientNotificationPreference.max_user_id.is_not(None),
                    CustomerNotification.deleted_at.is_(None),
                    CustomerNotification.channel == NotificationChannel.MAX,
                    CustomerNotification.status == NotificationStatus.PREPARED,
                    CustomerNotification.scheduled_for
                    <= datetime.now(UTC),
                )
                .order_by(
                    CustomerNotification.scheduled_for.asc(),
                    CustomerNotification.created_at.asc(),
                )
                .limit(1)
                .with_for_update(
                    of=CustomerNotification,
                    skip_locked=True,
                )
            )

            if mode == "test":
                statement = statement.where(
                    Client.client_number == test_client_number
                )

            row = db.execute(statement).first()
            if row is None:
                break

            notification, max_user_id, client_number = row

            # One side-effect-free availability probe per dispatch run.
            if not checked_availability:
                try:
                    _api_request(token, "GET", "/me")
                except Exception as error:
                    logger.warning(
                        "MAX: недоступен; очередь сохранена, "
                        "новая проверка через %s с: %s",
                        CHECK_INTERVAL_SECONDS,
                        error,
                    )
                    return sent_count, failed_count
                checked_availability = True

            if not notification_is_current(db, notification):
                notification.status = NotificationStatus.CANCELLED
                notification.error_message = None
                db.commit()
                logger.info("MAX: устаревшее уведомление отменено")
                continue

            try:
                recipient_id = int(max_user_id)
                if recipient_id <= 0:
                    raise ValueError("Некорректный MAX ID")
            except (ValueError, TypeError) as error:
                notification.status = NotificationStatus.FAILED
                notification.error_message = (
                    "MAX: некорректный получатель: " + str(error)
                )[:2000]
                db.commit()
                failed_count += 1
                continue

            # Save all information needed for the API call before the claim.
            notification_id = notification.id
            message_text = notification.message_text
            previous_error = notification.error_message

            # Commit a durable non-retryable claim BEFORE calling MAX.
            # Crash after this point -> manual review, never a blind resend.
            notification.status = NotificationStatus.FAILED
            notification.error_message = (
                "MAX_INFLIGHT: отправка начата; если сервер завершился, "
                "проверить доставку вручную"
            )
            db.commit()

            try:
                result = _api_request(
                    token,
                    "POST",
                    "/messages",
                    params={"user_id": recipient_id},
                    payload={"text": message_text},
                )

                if not isinstance(result, dict):
                    raise ValueError("MAX returned an invalid response")
                message = result.get("message") or {}
                if not isinstance(message, dict):
                    raise ValueError("MAX returned an invalid message body")
                body = message.get("body") or {}
                if not isinstance(body, dict):
                    raise ValueError("MAX returned an invalid body")
                message_id = body.get("mid")
                if not message_id:
                    raise ValueError("MAX returned no message ID; inspect delivery")

                notification.status = NotificationStatus.SENT
                notification.sent_at = datetime.now(UTC)
                notification.provider_message_id = str(message_id)
                notification.error_message = None
                db.commit()  # Persist this ONE send before processing another.

                sent_count += 1
                logger.info(
                    "MAX: уведомление %s отправлено клиенту №%s",
                    notification_id,
                    client_number,
                )

            except Exception as error:
                # Restore the retry count that existed BEFORE the claim.
                # Otherwise 429 would restart at attempt 1 forever.
                notification.error_message = previous_error
                _handle_max_delivery_error(notification, error)
                db.commit()  # 429 schedules retry; ambiguity stays FAILED.
                failed_count += 1
                logger.exception(
                    "MAX: ошибка отправки уведомления %s",
                    notification_id,
                )

            # Preserve existing MAX rate limiting.
            time.sleep(0.6)

    return sent_count, failed_count


async def _notification_scheduler_loop() -> None:
    """Постоянный цикл проверки очереди уведомлений."""

    while True:
        try:
            prepared_count = await asyncio.to_thread(
                process_due_service_reminders_once
            )

            if prepared_count:
                logger.info(
                    "Подготовлено сервисных уведомлений: %s",
                    prepared_count,
                )

            sent_count, failed_count = (
                await asyncio.to_thread(
                    dispatch_due_max_once
                )
            )

            if sent_count or failed_count:
                logger.info(
                    "MAX: отправлено %s, ошибок %s",
                    sent_count,
                    failed_count,
                )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Ошибка автоматической обработки уведомлений"
            )

        await asyncio.sleep(
            CHECK_INTERVAL_SECONDS
        )


async def start_notification_scheduler() -> None:
    """Запускается вместе с FastAPI."""

    global _scheduler_task

    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    if (
        _scheduler_task is not None
        and not _scheduler_task.done()
    ):
        return

    _scheduler_task = asyncio.create_task(
        _notification_scheduler_loop(),
        name="customer-notification-scheduler",
    )

    logger.info(
        "Планировщик уведомлений запущен"
    )


async def stop_notification_scheduler() -> None:
    """Останавливается вместе с FastAPI."""

    global _scheduler_task

    if _scheduler_task is None:
        return

    _scheduler_task.cancel()

    with suppress(asyncio.CancelledError):
        await _scheduler_task

    _scheduler_task = None

    logger.info(
        "Планировщик уведомлений остановлен"
    )
