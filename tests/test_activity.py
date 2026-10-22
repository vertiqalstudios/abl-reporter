from datetime import date, timedelta
from decimal import Decimal

import pytest

from abl.activity import SettledLine, is_settled_in_week, settled_in_week
from abl.fxdata import SeriesFXProvider
from abl.models import Invoice

AS_OF = date(2026, 3, 13)
WEEK_AGO = AS_OF - timedelta(days=7)
FX = SeriesFXProvider(
    {
        date(2026, 3, 9): Decimal("1.30"),
        date(2026, 3, 11): Decimal("1.35"),
        AS_OF: Decimal("1.40"),
    }
)


def _inv(
    paid: date | None, issued: date = date(2026, 2, 1), client: str = "Acme"
) -> Invoice:
    return Invoice(client, issued, date(2026, 4, 1), Decimal("1000"), "USD", paid, "K1")


@pytest.mark.parametrize(
    ("paid", "expected"),
    [
        (None, False),  # still open
        (AS_OF + timedelta(days=1), False),  # paid after the report date
        (AS_OF, True),
        (WEEK_AGO + timedelta(days=1), True),
        (WEEK_AGO, False),  # last week's report date: last week's activity
    ],
)
def test_is_settled_in_week(paid: date | None, expected: bool) -> None:
    assert is_settled_in_week(_inv(paid), AS_OF) is expected


def test_issued_and_paid_in_the_same_week_is_activity() -> None:
    invoice = _inv(date(2026, 3, 11), issued=date(2026, 3, 9))
    (line,) = settled_in_week([invoice], AS_OF, FX)
    assert line.invoice_date == date(2026, 3, 9)


def test_settled_line_is_priced_at_the_settlement_date_rate() -> None:
    (line,) = settled_in_week([_inv(date(2026, 3, 11))], AS_OF, FX)
    assert line == SettledLine(
        client_name="Acme",
        invoice_id="K1",
        invoice_date=date(2026, 2, 1),
        paid_date=date(2026, 3, 11),
        currency="USD",
        face_amount=Decimal("1000"),
        fx_rate=Decimal("1.35"),
        cad_amount=Decimal("1350.00"),
    )


def test_settled_lines_sort_by_debtor_case_blind_then_settlement() -> None:
    invoices = [
        _inv(date(2026, 3, 11), client="zed"),
        _inv(date(2026, 3, 11), client="Alpha"),
        _inv(date(2026, 3, 9), client="alpha"),
    ]
    lines = settled_in_week(invoices, AS_OF, FX)
    assert [(ln.client_name, ln.paid_date.day) for ln in lines] == [
        ("alpha", 9),
        ("Alpha", 11),
        ("zed", 11),
    ]
