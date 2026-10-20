import io
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from abl import ReportSettings, generate_all_reports, generate_report_pack
from abl.models import CertificateParams
from abl.report import _compact_currency, _customer_pivot

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHART_FILES = {
    "collateral": "collateral_chart.png",
    "dso": "dso_chart.png",
    "top_n": "top_n_chart.png",
    "customer_ar": "customer_ar_chart.png",
    "stream": "customer_ar_stream_chart.png",
}
CSV_HEADER = (
    "Client Name,Invoice Date,Due Date,Invoice Amount,Currency,Invoice Paid Date"
)
# ReportLab stamps a per-run document ID and timestamps into every PDF.
PDF_RUN_METADATA = re.compile(
    rb"/(?:Creation|Mod)Date \([^)]*\)|\[<[0-9a-f]+><[0-9a-f]+>\]"
)


def _sample_csv(customers: int = 8, weeks: int = 14) -> str:
    """Builds weekly invoices for several customers, a third of them paid."""
    start = date(2026, 1, 5)
    rows = [CSV_HEADER]
    for week in range(weeks):
        for customer in range(customers):
            issued = start + timedelta(weeks=week)
            paid = (issued + timedelta(days=20)).isoformat() if week % 3 == 0 else ""
            currency = "USD" if customer % 2 else "CAD"
            amount = 5000 * (customer + 1)
            rows.append(
                f"Client {customer},{issued},{issued + timedelta(days=30)},"
                f"{amount}.00,{currency},{paid}"
            )
    return "\n".join(rows)


@pytest.fixture(scope="module")
def pack_outputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Runs the whole pack once; the charts are slow enough to share."""
    settings = ReportSettings(
        input_source=io.StringIO(_sample_csv()),
        output_dir=tmp_path_factory.mktemp("pack"),
    )
    return generate_report_pack(settings)


@pytest.mark.parametrize("name", CHART_FILES)
def test_pack_writes_valid_png_chart(pack_outputs: dict[str, Path], name: str) -> None:
    path = pack_outputs[name]
    assert path.name == CHART_FILES[name]
    assert path.read_bytes()[:8] == PNG_SIGNATURE
    assert path.stat().st_size > 10_000


def test_pack_writes_certificate_pdf(pack_outputs: dict[str, Path]) -> None:
    assert pack_outputs["certificate"].read_bytes()[:5] == b"%PDF-"


def test_pack_returns_certificate_and_every_chart(
    pack_outputs: dict[str, Path],
) -> None:
    assert set(pack_outputs) == {"certificate", *CHART_FILES}


def test_generate_all_reports_empty_invoices_writes_nothing(tmp_path: Path) -> None:
    settings = ReportSettings(output_dir=tmp_path)
    assert generate_all_reports(settings, invoices=[]) == {}
    assert list(tmp_path.iterdir()) == []


def test_certificate_is_reproducible_with_fixed_generated_at(tmp_path: Path) -> None:
    fixed = CertificateParams(generated_at=datetime(2026, 10, 20, 12, 0, 0))
    paths = [
        generate_report_pack(
            ReportSettings(
                input_source=io.StringIO(_sample_csv(customers=2, weeks=3)),
                output_dir=tmp_path / name,
                certificate=fixed,
            )
        )["certificate"]
        for name in ("first", "second")
    ]
    first, second = (PDF_RUN_METADATA.sub(b"", p.read_bytes()) for p in paths)
    assert first == second


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(0, "$0"), (350_000, "$350K"), (1_200_000, "$1.2M")],
)
def test_compact_currency(amount: float, expected: str) -> None:
    assert _compact_currency(amount) == expected


def _balances(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "as_of_date": [date(2026, 1, 5)] * len(rows),
            "client_name": [name for name, _ in rows],
            "balance": [balance for _, balance in rows],
            "is_top_n": [False] * len(rows),
        }
    )


def test_customer_pivot_folds_smallest_into_other_bucket() -> None:
    df = _balances([("A", 100.0), ("B", 400.0), ("C", 300.0), ("D", 200.0)])
    pivot = _customer_pivot(df, max_named=2)
    assert pivot is not None
    assert list(pivot.columns) == ["B", "C", "Other customers"]
    assert pivot["Other customers"].tolist() == [300.0]


def test_customer_pivot_has_no_other_bucket_when_all_customers_fit() -> None:
    pivot = _customer_pivot(_balances([("A", 1.0), ("B", 2.0)]), max_named=5)
    assert pivot is not None
    assert list(pivot.columns) == ["B", "A"]


def test_customer_pivot_ignores_placeholder_rows() -> None:
    assert _customer_pivot(_balances([("NONE", 0.0)]), max_named=5) is None
