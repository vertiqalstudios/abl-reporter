from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .models import Invoice

type FXProvider = Callable[[str, str, date], Decimal]


def make_fixed_fx_provider(usd_cad_rate: Decimal = Decimal("1.44")) -> FXProvider:
    """Returns a deterministic FX provider fixed at 1 USD = 1.44 CAD."""

    def get_rate(from_curr: str, to_curr: str, as_of: date) -> Decimal:
        from_c = from_curr.upper()
        to_c = to_curr.upper()
        if from_c == to_c:
            return Decimal("1.00")
        if from_c == "USD" and to_c == "CAD":
            return usd_cad_rate
        if from_c == "CAD" and to_c == "USD":
            return (Decimal("1.00") / usd_cad_rate).quantize(
                Decimal("0.00000001"), rounding=ROUND_HALF_UP
            )
        raise ValueError(f"Unsupported currency pair: {from_curr} to {to_curr}")

    return get_rate


def convert_to_cad(inv: Invoice, as_of: date, fx: FXProvider) -> Invoice:
    """Normalizes an invoice to CAD base currency using the supplied FX provider."""
    rate = fx(inv.currency, "CAD", as_of)
    cad_amount = (inv.invoice_amount * rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return Invoice(
        client_name=inv.client_name,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        invoice_amount=cad_amount,
        currency="CAD",
        paid_date=inv.paid_date,
        invoice_id=inv.invoice_id,
    )
