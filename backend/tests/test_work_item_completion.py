from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from unittest.mock import Mock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api.v1 import reports
from app.models.employee import Employee
from app.models.user import User
from app.models.work_item import WorkItem, WorkItemAssignment
from app.models.work_order import WorkOrder, WorkOrderStatus
from app.schemas.work_item import WorkItemUpdate
from app.services import work_item as service
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_completing_open_work_is_idempotent_and_locks_edits(monkeypatch) -> None:
    db = Mock(spec=Session)
    order = WorkOrder(
        id=uuid4(),
        work_order_number=1,
        status=WorkOrderStatus.IN_PROGRESS,
    )
    work = WorkItem(
        id=uuid4(),
        work_order_id=order.id,
        work_item_number=2,
        name="Тестовая работа",
        price=Decimal("100.03"),
    )
    employees = [Employee(employee_number=n, full_name=f"Мастер {n}") for n in (1, 2)]
    assignments = [
        (WorkItemAssignment(share_percent=Decimal("50")), employee)
        for employee in employees
    ]
    lock_order = Mock(return_value=order)
    monkeypatch.setattr(service, "require_work_order", lock_order)
    monkeypatch.setattr(service, "get_work_item_by_number", lambda *_: work)
    monkeypatch.setattr(service, "list_assignments_for_work", lambda *_: assignments)

    first = service.complete_work_item(db, work_order_number=1, work_item_number=2)
    second = service.complete_work_item(db, work_order_number=1, work_item_number=2)

    assert first.completed_at is not None
    assert first.completed_at.tzinfo == UTC
    assert first.completed_at == second.completed_at
    assert order.status == WorkOrderStatus.IN_PROGRESS
    assert [entry.employee_number for entry in first.assignments] == [1, 2]
    db.commit.assert_called_once()
    lock_order.assert_called_with(db, 1, for_update=True)

    payload = WorkItemUpdate(
        name="Изменённая работа",
        price=Decimal("100.03"),
        assignments=[{"employee_number": 1, "share_percent": Decimal("100")}],
    )
    with pytest.raises(HTTPException) as error:
        service.update_work_item(
            db,
            work_order_number=1,
            work_item_number=2,
            payload=payload,
        )
    assert error.value.status_code == 409
    assert work.name == "Тестовая работа"


def test_edit_keeps_existing_assignment_snapshot(monkeypatch) -> None:
    db = Mock(spec=Session)
    assignment = WorkItemAssignment(
        share_percent=Decimal("100"),
        rate_percent_snapshot=Decimal("25"),
    )
    latest_rate = Mock()
    monkeypatch.setattr(service, "get_current_employee_rate", latest_rate)

    service.save_assignments(
        db,
        work_item=WorkItem(),
        payload=WorkItemUpdate(
            name="Уточнённое название",
            price=Decimal("100.03"),
            assignments=[{"employee_number": 1, "share_percent": Decimal("100")}],
        ),
        existing_assignments={1: assignment},
    )

    assert assignment.rate_percent_snapshot == Decimal("25")
    latest_rate.assert_not_called()
    db.add.assert_not_called()
    db.delete.assert_not_called()


def test_my_report_uses_account_binding(monkeypatch) -> None:
    db = Mock(spec=Session)
    employee_id = uuid4()
    report = Mock()
    monkeypatch.setattr(reports, "get_employee_accrual_report", report)

    with pytest.raises(HTTPException) as error:
        reports.my_employee_accrual_report(db, User(employee_id=None))
    assert error.value.status_code == 403

    result = reports.my_employee_accrual_report(db, User(employee_id=employee_id))
    assert result is report.return_value
    report.assert_called_once_with(db, date_from=None, date_to=None, employee_id=employee_id)


def test_migration_backfills_only_issued_orders(monkeypatch) -> None:
    migration = import_module(
        "app.db.migrations.versions.63dcab1e74f2_track_work_item_completion"
    )
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE work_orders (id INTEGER, status TEXT, issued_at DATETIME, "
            "deleted_at DATETIME)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE work_items (id INTEGER, work_order_id INTEGER, deleted_at DATETIME)"
        )
        issued_at = datetime(2026, 11, 19, 8, tzinfo=UTC).isoformat()
        conn.exec_driver_sql(
            "INSERT INTO work_orders VALUES (1, 'issued', ?, NULL), "
            "(2, 'planned', NULL, NULL), (3, 'ready', NULL, NULL)",
            (issued_at,),
        )
        conn.exec_driver_sql(
            "INSERT INTO work_items VALUES (1, 1, NULL), (2, 2, NULL), (3, 3, NULL)"
        )
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))

        migration.upgrade()

        rows = conn.exec_driver_sql("SELECT completed_at FROM work_items ORDER BY id")
        assert rows.scalars().all() == [issued_at, None, None]
