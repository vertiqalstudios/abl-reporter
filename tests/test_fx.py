from datetime import date
from decimal import Decimal

import pytest

from abl.fx import (
    FXConversionError,
    convert_to_cad,
    make_fixed_fx_provider,
    require_supported_currencies,
)
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
    inv = Invoice(
        "Gamma Inc", date(2025, 1, 1), date(2025, 1, 31), Decimal("100.00"), "EUR"
    )
    with pytest.raises(FXConversionError, match="denominated in EUR"):
        convert_to_cad(inv, date(2025, 1, 15), fx)


@pytest.mark.parametrize(
    ("invoice_id", "expected"),
    [
        (
            "INV-42",
            "unsupported currency - invoice INV-42 is denominated in EUR "
            "which is not supported for collateral processing",
        ),
        (
            "",
            "unsupported currency - invoice for Gamma Inc dated 2025-01-01 is "
            "denominated in EUR which is not supported for collateral processing",
        ),
    ],
)
def test_unsupported_currency_message_names_the_invoice(
    invoice_id: str, expected: str
) -> None:
    inv = Invoice(
        "Gamma Inc",
        date(2025, 1, 1),
        date(2025, 1, 31),
        Decimal("100.00"),
        "EUR",
        None,
        invoice_id,
    )
    with pytest.raises(FXConversionError) as caught:
        require_supported_currencies([inv])
    assert str(caught.value) == expected


def test_supported_currencies_pass() -> None:
    invoices = [
        Invoice("A", date(2025, 1, 1), date(2025, 1, 31), Decimal("1.00"), code)
        for code in ("CAD", "USD", "usd")
    ]
    require_supported_currencies(invoices)


@pytest.mark.parametrize(
    ("from_curr", "to_curr", "expected"),
    [
        ("USD", "CAD", Decimal("1.44")),
        ("CAD", "USD", Decimal("0.69444444")),
        ("usd", "usd", Decimal("1.00")),
    ],
)
def test_fixed_provider_rates(from_curr: str, to_curr: str, expected: Decimal) -> None:
    fx = make_fixed_fx_provider(Decimal("1.44"))
    assert fx(from_curr, to_curr, date(2025, 1, 1)) == expected


def test_fixed_provider_rejects_unknown_pair() -> None:
    fx = make_fixed_fx_provider()
    with pytest.raises(ValueError, match="Unsupported currency pair: EUR to CAD"):
        fx("EUR", "CAD", date(2025, 1, 1))


def test_fixed_provider_is_valid_on_any_date() -> None:
    assert make_fixed_fx_provider().rate_date(date(2025, 6, 1)) == date(2025, 6, 1)
