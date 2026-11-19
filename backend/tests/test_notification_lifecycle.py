from datetime import time, timedelta

import app.db.notification_hooks  # noqa: F401
import pytest
from app.db import audit_hooks
from app.db.base import Base
from app.models.appointment import Appointment, AppointmentStatus
from app.models.client import Client, ClientSource
from app.models.notification import (
    ClientNotificationPreference,
    CustomerNotification,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
)
from app.models.vehicle import Vehicle
from app.models.work_order import WorkOrder, WorkOrderStatus
from app.services.appointment import cancel_appointment
from app.services.notification import local_now
from app.services.notification_scheduler import notification_is_current
from app.services.work_order import change_work_order_status
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def customer_and_vehicle(monkeypatch):
    # SQLite does not generate the PostgreSQL identity on audit logs.
    monkeypatch.setattr(audit_hooks, "record_audit_event", lambda *_args, **_kwargs: None)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        customer = Client(
            client_number=1,
            full_name="Тестовый клиент",
            phone_primary="00000000000",
            source=ClientSource.OTHER,
        )
        vehicle = Vehicle(vehicle_number=1, license_plate="TEST", brand="Test", model="Test")
        db.add_all([customer, vehicle])
        db.commit()
        yield db, customer, vehicle
    engine.dispose()


def link_max(db, customer):
    db.add(
        ClientNotificationPreference(client_id=customer.id, max_enabled=True, max_user_id="1")
    )
    db.commit()


@pytest.mark.parametrize("max_connected", [False, True])
def test_cancel_appointment_is_idempotent(customer_and_vehicle, max_connected) -> None:
    db, customer, vehicle = customer_and_vehicle
    if max_connected:
        link_max(db, customer)
    appointment = Appointment(
        appointment_number=1,
        client_id=customer.id,
        vehicle_id=vehicle.id,
        appointment_date=local_now().date() + timedelta(days=2),
        appointment_time=time(12),
        reason="Тестовая запись",
    )
    db.add(appointment)
    db.commit()

    assert cancel_appointment(db, appointment=appointment).status == AppointmentStatus.CANCELLED
    assert cancel_appointment(db, appointment=appointment).status == AppointmentStatus.CANCELLED
    notifications = db.scalars(select(CustomerNotification)).all()

    if max_connected:
        assert {(item.kind, item.status) for item in notifications} == {
            (NotificationKind.APPOINTMENT_CONFIRMATION, NotificationStatus.CANCELLED),
            (NotificationKind.APPOINTMENT_REMINDER, NotificationStatus.CANCELLED),
            (NotificationKind.APPOINTMENT_CANCELLATION, NotificationStatus.PREPARED),
        }
        assert len(notifications) == 3
    else:
        assert notifications == []


def test_ready_notification_is_queued_once_per_ready_transition(customer_and_vehicle) -> None:
    db, customer, vehicle = customer_and_vehicle
    link_max(db, customer)
    order = WorkOrder(
        work_order_number=1,
        client_id=customer.id,
        vehicle_id=vehicle.id,
        status=WorkOrderStatus.IN_PROGRESS,
        reason="Тестовый заказ-наряд",
    )
    db.add(order)
    db.commit()

    change_work_order_status(db, order=order, new_status=WorkOrderStatus.READY)
    change_work_order_status(db, order=order, new_status=WorkOrderStatus.READY)
    (first_notice,) = db.scalars(select(CustomerNotification)).all()
    assert first_notice.work_order_id == order.id
    assert first_notice.kind == NotificationKind.WORK_ORDER_READY
    assert first_notice.channel == NotificationChannel.MAX
    assert first_notice.status == NotificationStatus.PREPARED
    assert notification_is_current(db, first_notice)

    change_work_order_status(db, order=order, new_status=WorkOrderStatus.IN_PROGRESS)
    assert first_notice.status == NotificationStatus.CANCELLED
    assert not notification_is_current(db, first_notice)

    change_work_order_status(db, order=order, new_status=WorkOrderStatus.READY)
    notifications = db.scalars(select(CustomerNotification)).all()
    assert len(notifications) == 2
    assert sum(item.status == NotificationStatus.PREPARED for item in notifications) == 1
