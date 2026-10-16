from datetime import date
from decimal import Decimal
import pytest

from abl.fx import convert_to_cad, make_fixed_fx_provider
from abl.models import Invoice


@pytest.mark.parametrize(
    ("currency", "amount", "rate", "expected_amount", "expected_curr"),
    [
        ("USD", Decimal("1000.00"), Decimal("1.44"), Decimal("1440.00"), "CAD"),
        ("CAD", Decimal("500.00"), Decimal("1.44"), Decimal("500.00"), "CAD"),
        ("USD", Decimal("0.00"), Decimal("1.44"), Decimal("0.00"), "CAD"),
    ],
)
def test_convert_to_cad(
    currency: str,
    amount: Decimal,
    rate: Decimal,
    expected_amount: Decimal,
    expected_curr: str,
) -> None:
    fx = make_fixed_fx_provider(rate)
    inv = Invoice("Test Corp", date(2025, 1, 1), date(2025, 1, 31), amount, currency)
    cad_inv = convert_to_cad(inv, date(2025, 1, 15), fx)

    assert cad_inv.currency == expected_curr
    assert cad_inv.invoice_amount == expected_amount


def test_unsupported_currency_raises() -> None:
    fx = make_fixed_fx_provider()
    inv = Invoice("Gamma Inc", date(2025, 1, 1), date(2025, 1, 31), Decimal("100.00"), "EUR")
    with pytest.raises(ValueError, match="Unsupported currency"):
        convert_to_cad(inv, date(2025, 1, 15), fx)
