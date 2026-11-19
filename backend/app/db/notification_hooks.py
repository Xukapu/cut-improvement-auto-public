
"""Уведомления о создании и переносе записей СТО."""

from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.notification import (
    CustomerNotification,
    NotificationKind,
)

from app.services.notification import (
    cancel_prepared_appointment_notifications,
    prepare_appointment_notifications,
)


_WATCHED_FIELDS = {
    "client_id",
    "vehicle_id",
    "appointment_date",
    "appointment_time",
    "status",
    "deleted_at",
}


@dataclass
class _AppointmentChange:
    appointment: Appointment
    old_date: date | None = None
    old_time: time | None = None
    is_rescheduled: bool = False


def _appointment_changed(
    appointment: Appointment,
) -> bool:
    state = inspect(appointment)

    return any(
        state.attrs[field].history.has_changes()
        for field in _WATCHED_FIELDS
    )


def _capture_change(
    appointment: Appointment,
) -> _AppointmentChange:
    """Запоминает дату и время до переноса."""

    state = inspect(appointment)

    date_history = (
        state.attrs.appointment_date.history
    )

    time_history = (
        state.attrs.appointment_time.history
    )

    date_changed = date_history.has_changes()
    time_changed = time_history.has_changes()

    client_changed = (
        state.attrs.client_id.history.has_changes()
    )

    old_date = (
        date_history.deleted[0]
        if date_changed and date_history.deleted
        else appointment.appointment_date
        if not date_changed
        else None
    )

    old_time = (
        time_history.deleted[0]
        if time_changed and time_history.deleted
        else appointment.appointment_time
        if not time_changed
        else None
    )

    return _AppointmentChange(
        appointment=appointment,
        old_date=old_date,
        old_time=old_time,
        is_rescheduled=(
            date_changed or time_changed
        ) and not client_changed,
    )


def _reschedule_text(
    change: _AppointmentChange,
) -> str:
    """Текст сообщения о переносе."""

    appointment = change.appointment

    assert change.old_date is not None
    assert change.old_time is not None

    return (
        "🚘 ЦУТ Improvement Auto\n\n"
        "Ваша запись перенесена.\n\n"
        f"Было: {change.old_date:%d.%m.%Y} "
        f"в {change.old_time:%H:%M}\n"
        f"Стало: {appointment.appointment_date:%d.%m.%Y} "
        f"в {appointment.appointment_time:%H:%M}\n\n"
        "Ждём вас в назначенное время!"
    )


@event.listens_for(
    Session,
    "before_flush",
)
def collect_appointment_notification_changes(
    session: Session,
    _flush_context,
    _instances,
) -> None:

    changes: list[_AppointmentChange] = (
        session.info.setdefault(
            "_appointment_notification_changes",
            [],
        )
    )

    known = {
        id(entry.appointment)
        for entry in changes
    }

    # Новые записи
    for item in session.new:
        if (
            isinstance(item, Appointment)
            and id(item) not in known
        ):
            changes.append(
                _AppointmentChange(
                    appointment=item,
                )
            )

            known.add(id(item))

    # Изменённые записи
    for item in session.dirty:
        if (
            isinstance(item, Appointment)
            and id(item) not in known
            and _appointment_changed(item)
        ):
            changes.append(
                _capture_change(item)
            )

            known.add(id(item))

    # Удалённые записи
    for item in session.deleted:
        if (
            isinstance(item, Appointment)
            and id(item) not in known
        ):
            changes.append(
                _AppointmentChange(
                    appointment=item,
                )
            )

            known.add(id(item))


@event.listens_for(
    Session,
    "after_flush_postexec",
)
def apply_appointment_notification_changes(
    session: Session,
    _flush_context,
) -> None:

    changes: list[_AppointmentChange] = (
        session.info.pop(
            "_appointment_notification_changes",
            [],
        )
    )

    for change in changes:
        appointment = change.appointment

        if appointment.id is None:
            continue

        # При удалении отменяем ожидающие сообщения.
        if (
            appointment in session.deleted
            or appointment.deleted_at is not None
        ):
            cancel_prepared_appointment_notifications(
                session,
                appointment.id,
            )
            continue

        # Существующая логика:
        # отмена старых ожидающих уведомлений
        # и создание подтверждения и напоминания.
        prepare_appointment_notifications(
            session,
            appointment,
        )

        # Если дата и время не менялись,
        # оставляем обычное подтверждение.
        if (
            not change.is_rescheduled
            or change.old_date is None
            or change.old_time is None
        ):
            continue

        if (
            change.old_date == appointment.appointment_date
            and change.old_time == appointment.appointment_time
        ):
            continue

        # Заменяем текст нового подтверждения
        # на сообщение о переносе.
        # Новых таблиц или типов уведомлений не нужно.
        message = _reschedule_text(change)

        for item in session.new:
            if (
                isinstance(item, CustomerNotification)
                and item.appointment_id == appointment.id
                and item.kind
                == NotificationKind.APPOINTMENT_CONFIRMATION
            ):
                item.message_text = message
