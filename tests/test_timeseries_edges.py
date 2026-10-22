from datetime import date
from decimal import Decimal

import pytest

from abl.models import Invoice
from abl.timeseries import (
    AgingBucket,
    _classify_aging_bucket,
    compute_weekly_snapshot,
    customer_ar_balances,
)

AS_OF = date(2026, 3, 13)


def _inv(amount: str, paid: date | None = None) -> Invoice:
    return Invoice(
        "Acme", date(2026, 2, 1), date(2026, 3, 1), Decimal(amount), "CAD", paid
    )


@pytest.mark.parametrize(
    ("days_past_due", "bucket"),
    [
        (0, AgingBucket.CURRENT),
        (1, AgingBucket.DAYS_1_30),
        (30, AgingBucket.DAYS_1_30),
        (31, AgingBucket.DAYS_31_60),
        (60, AgingBucket.DAYS_31_60),
        (61, AgingBucket.DAYS_61_90),
        (90, AgingBucket.DAYS_61_90),
        (91, AgingBucket.DAYS_90_PLUS),
        (4000, AgingBucket.DAYS_90_PLUS),
    ],
)
def test_aging_bucket_boundaries(days_past_due: int, bucket: AgingBucket) -> None:
    assert _classify_aging_bucket(days_past_due) is bucket


def test_snapshot_of_only_credit_notes_has_no_dso() -> None:
    snap = compute_weekly_snapshot((_inv("-250.00"),), AS_OF, "CAD")
    assert snap.total_ar == Decimal("-250.00")
    assert snap.dso_equal_weight is None
    assert snap.bucket_1_30 == Decimal("0.00")


def test_week_with_no_open_ar_gives_one_placeholder_row() -> None:
    settled = _inv("100.00", paid=date(2026, 3, 2))
    df = customer_ar_balances((settled,), date(2026, 3, 6), AS_OF, top_n=3)
    assert df["client_name"].tolist() == ["NONE", "NONE"]
    assert df["balance"].tolist() == [0.0, 0.0]
