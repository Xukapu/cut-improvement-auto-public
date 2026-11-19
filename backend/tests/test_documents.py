from decimal import Decimal
from unittest.mock import Mock

import pytest
from app.models.work_order import WorkOrder, WorkOrderStatus
from app.schemas.document import (
    DocumentClient,
    DocumentPayment,
    DocumentVehicle,
    WorkOrderDocumentData,
)
from app.services.document import (
    build_completion_act_pdf,
    build_work_order_pdf,
    format_date,
    format_money,
    get_completion_act_data,
)
from fastapi import HTTPException


def sample_data() -> WorkOrderDocumentData:
    return WorkOrderDocumentData(
        work_order_number=2,
        document_date="30.09.2026",
        status="Запланирован",
        reason="Тестовый ремонт",
        client=DocumentClient(
            client_number=2,
            full_name="Тестовый Клиент",
            phone_primary="+79990000000",
            phone_secondary=None,
        ),
        vehicle=DocumentVehicle(
            vehicle_number=1,
            brand="Volkswagen",
            model="Polo",
            license_plate="А123ВС77",
            vin="XW8ZZZ61ZKG000001",
            year=2020,
            mileage=55000,
        ),
        works=[],
        parts=[],
        recommendations=[],
        payment=DocumentPayment(
            works_total=Decimal("10000"),
            sto_parts_total=Decimal("6000"),
            repair_total=Decimal("16000"),
            paid_amount=Decimal("16000"),
            debt_amount=Decimal("0"),
        ),
    )


def test_date_format() -> None:
    from datetime import date

    assert format_date(date(2026, 10, 1)) == "01.10.2026"


def test_money_format() -> None:
    assert format_money(Decimal("16000")) == "16 000,00 ₽"


def test_print_schema_has_no_internal_fields() -> None:
    payload = str(sample_data().model_dump())

    assert "internal_mark" not in payload
    assert "share_percent" not in payload
    assert "rate_percent_snapshot" not in payload
    assert "earning_amount" not in payload
    assert "actor_login" not in payload


def test_work_order_pdf() -> None:
    content = build_work_order_pdf(sample_data())

    assert content.startswith(b"%PDF-")

    assert len(content) > 1000


def test_completion_act_pdf() -> None:
    content = build_completion_act_pdf(sample_data())

    assert content.startswith(b"%PDF-")

    assert len(content) > 1000



@pytest.mark.parametrize("order_status", [WorkOrderStatus.PLANNED, WorkOrderStatus.IN_PROGRESS])
def test_rejects_act_before_repair_ready(monkeypatch, order_status: WorkOrderStatus) -> None:
    order = WorkOrder(status=order_status)
    monkeypatch.setattr("app.services.document.require_work_order", lambda *args: order)

    with pytest.raises(HTTPException) as error:
        get_completion_act_data(Mock(), 2)

    assert error.value.status_code == 409


def test_returns_issued_act_from_snapshot(monkeypatch) -> None:
    data = sample_data()
    order = WorkOrder(
        status=WorkOrderStatus.ISSUED,
        completion_act_data=data.model_dump(mode="json"),
    )
    monkeypatch.setattr("app.services.document.require_work_order", lambda *args: order)
    db = Mock()

    stored, preliminary = get_completion_act_data(db, data.work_order_number)

    assert stored == data
    assert not preliminary
    assert not db.mock_calls
