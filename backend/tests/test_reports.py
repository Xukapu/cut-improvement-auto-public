from datetime import date
from decimal import Decimal

import pytest
from app.services.report import (
    calculate_employee_amount,
    normalize_period,
)
from app.services.work_item import calculate_earning
from fastapi import HTTPException


def test_employee_amount() -> None:
    share_base, earning = calculate_employee_amount(
        work_price=Decimal("10000"),
        share_percent=Decimal("70"),
        rate_percent=Decimal("30"),
    )

    assert share_base == Decimal("7000.00")
    assert earning == Decimal("2100.00")


def test_second_employee_amount() -> None:
    share_base, earning = calculate_employee_amount(
        work_price=Decimal("10000"),
        share_percent=Decimal("30"),
        rate_percent=Decimal("40"),
    )

    assert share_base == Decimal("3000.00")
    assert earning == Decimal("1200.00")


def test_employee_amount_matches_order_at_fractional_cent() -> None:
    price = Decimal("100.03")
    share = Decimal("50")
    rate = Decimal("25")
    share_base, earning = calculate_employee_amount(
        work_price=price,
        share_percent=share,
        rate_percent=rate,
    )

    assert share_base == Decimal("50.02")
    assert earning == calculate_earning(price=price, share_percent=share, rate_percent=rate)
    assert earning == Decimal("12.50")


def test_report_period() -> None:
    start_date, end_date = normalize_period(
        date(2026, 9, 1),
        date(2026, 9, 30),
    )

    assert start_date == date(2026, 9, 1)
    assert end_date == date(2026, 9, 30)


def test_single_report_date() -> None:
    start_date, end_date = normalize_period(
        date(2026, 9, 30),
        None,
    )

    assert start_date == date(2026, 9, 30)
    assert end_date == date(2026, 9, 30)


def test_invalid_report_period() -> None:
    with pytest.raises(HTTPException):
        normalize_period(
            date(2026, 10, 1),
            date(2026, 9, 30),
        )
