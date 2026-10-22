from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .models import FXProvider, Invoice

__all__ = [
    "FXProvider",
    "FXConversionError",
    "FixedFXProvider",
    "convert_to_cad",
    "make_fixed_fx_provider",
    "require_supported_currencies",
    "valuation_date",
]

DEFAULT_USD_CAD = Decimal("1.44")
SUPPORTED_CURRENCIES = frozenset({"CAD", "USD"})


class FXConversionError(ValueError):
    """Raised when an invoice can't be converted for collateral processing."""


@dataclass(frozen=True, slots=True)
class FixedFXProvider:
    """Deterministic FX at one USD/CAD rate, whatever the date."""

    usd_cad_rate: Decimal
    source: str

    def __call__(self, from_curr: str, to_curr: str, as_of: date) -> Decimal:
        from_c = from_curr.upper()
        to_c = to_curr.upper()
        if from_c == to_c:
            return Decimal("1.00")
        if from_c == "USD" and to_c == "CAD":
            return self.usd_cad_rate
        if from_c == "CAD" and to_c == "USD":
            return (Decimal("1.00") / self.usd_cad_rate).quantize(
                Decimal("0.00000001"), rounding=ROUND_HALF_UP
            )
        raise ValueError(f"Unsupported currency pair: {from_curr} to {to_curr}")

    def rate_date(self, as_of: date) -> date:
        """A fixed rate is valid on any date, so it is as of the date asked."""
        return as_of


def make_fixed_fx_provider(
    usd_cad_rate: Decimal = DEFAULT_USD_CAD, source: str | None = None
) -> FixedFXProvider:
    """Returns a deterministic FX provider fixed at 1 USD = `usd_cad_rate` CAD."""
    label = source if source is not None else f"Pinned {usd_cad_rate:.4f}"
    return FixedFXProvider(usd_cad_rate, label)


def valuation_date(inv: Invoice, as_of: date) -> date:
    """Returns the pricing date: settlement if paid by as_of, otherwise as_of.

    Open receivables are valued at spot on the report date; settled ones at the
    rate they actually realised.
    """
    if inv.paid_date is not None and inv.paid_date <= as_of:
        return inv.paid_date
    return as_of


def require_supported_currency(inv: Invoice) -> None:
    """Raises FXConversionError if the invoice isn't in a supported currency."""
    if inv.currency.upper() in SUPPORTED_CURRENCIES:
        return
    label = inv.invoice_id or f"for {inv.client_name} dated {inv.invoice_date}"
    raise FXConversionError(
        f"unsupported currency - invoice {label} is denominated in "
        f"{inv.currency.upper()} which is not supported for collateral processing"
    )


def require_supported_currencies(invoices: Iterable[Invoice]) -> None:
    """Fails fast, before any pricing, on the first unsupported invoice."""
    for inv in invoices:
        require_supported_currency(inv)


def convert_to_cad(inv: Invoice, as_of: date, fx: FXProvider) -> Invoice:
    """Normalizes an invoice to CAD at its valuation-date rate."""
    require_supported_currency(inv)
    rate = fx(inv.currency, "CAD", valuation_date(inv, as_of))
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
