import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from abl import ReportSettings, generate_all_reports, generate_report_pack
from abl.fx import make_fixed_fx_provider
from abl.fxdata import FXDataError
from abl.importer import load_invoices_from_csv
from abl.models import CertificateParams, CustomerPalette
from abl.report import (
    _OTHER_LABEL,
    _chart_start,
    _compact_currency,
    _customer_colors,
    _customer_pivot,
    _row_major,
    _stream_bounds,
    _week_edges,
    generate_dso_chart,
)
from abl.timeseries import compute_weekly_dso

AS_OF = date(2026, 5, 1)
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
        as_of=AS_OF,
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


def test_generate_all_reports_rejects_as_of_before_first_invoice(
    tmp_path: Path,
) -> None:
    settings = ReportSettings(
        input_source=io.StringIO(_sample_csv(customers=2, weeks=3)),
        output_dir=tmp_path,
        as_of=date(2025, 12, 31),
    )
    with pytest.raises(ValueError, match="before the first invoice"):
        generate_all_reports(settings)


def test_certificate_is_reproducible_with_fixed_generated_at(tmp_path: Path) -> None:
    fixed = CertificateParams(generated_at=datetime(2026, 10, 20, 12, 0, 0))
    paths = [
        generate_report_pack(
            ReportSettings(
                input_source=io.StringIO(_sample_csv(customers=2, weeks=3)),
                output_dir=tmp_path / name,
                certificate=fixed,
                as_of=AS_OF,
            )
        )["certificate"]
        for name in ("first", "second")
    ]
    first, second = (PDF_RUN_METADATA.sub(b"", p.read_bytes()) for p in paths)
    assert first == second


FUTURE_AS_OF = date(2026, 2, 6)
KNOWN_ROWS = (
    "Alpha,2026-01-05,2026-02-04,10000.00,CAD,",
    "Beta,2026-01-12,2026-02-11,20000.00,USD,",
)
# Same history plus data that did not exist yet at FUTURE_AS_OF.
HINDSIGHT_ROWS = (
    "Alpha,2026-01-05,2026-02-04,10000.00,CAD,2026-02-20",
    "Beta,2026-01-12,2026-02-11,20000.00,USD,",
    "Gamma,2026-02-13,2026-03-15,90000.00,CAD,",
)


@pytest.fixture(scope="module")
def known_and_hindsight_packs(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[dict[str, Path], dict[str, Path]]:
    """Runs the pack on history as known at as_of, and with later data added."""
    fixed = CertificateParams(generated_at=datetime(2026, 10, 20, 12, 0, 0))
    packs = [
        generate_report_pack(
            ReportSettings(
                input_source=io.StringIO("\n".join((CSV_HEADER, *rows))),
                output_dir=tmp_path_factory.mktemp(name),
                certificate=fixed,
                as_of=FUTURE_AS_OF,
            )
        )
        for name, rows in (("known", KNOWN_ROWS), ("hindsight", HINDSIGHT_ROWS))
    ]
    return packs[0], packs[1]


@pytest.mark.parametrize("name", ["certificate", *CHART_FILES])
def test_data_after_as_of_does_not_change_outputs(
    known_and_hindsight_packs: tuple[dict[str, Path], dict[str, Path]], name: str
) -> None:
    known, hindsight = known_and_hindsight_packs
    assert PDF_RUN_METADATA.sub(b"", known[name].read_bytes()) == PDF_RUN_METADATA.sub(
        b"", hindsight[name].read_bytes()
    )


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


@pytest.mark.parametrize(
    ("first_invoice", "as_of", "weeks", "expected"),
    [
        # long history: the window reaches back `weeks` from a Friday as-of
        (date(2024, 1, 3), date(2026, 10, 23), 78, date(2025, 4, 25)),
        # short history: start at the first snapshot Friday on or after the data
        (date(2025, 1, 1), date(2025, 6, 6), 78, date(2025, 1, 3)),
        (date(2025, 1, 3), date(2025, 6, 6), 78, date(2025, 1, 3)),
        (date(2025, 1, 4), date(2025, 6, 6), 78, date(2025, 1, 10)),
    ],
)
def test_chart_start_snaps_window_to_snapshot_friday(
    first_invoice: date, as_of: date, weeks: int, expected: date
) -> None:
    assert _chart_start(first_invoice, as_of, weeks) == expected


@pytest.mark.parametrize(
    ("count", "columns", "expected"),
    [
        (2, 2, [0, 1]),
        (4, 4, [0, 1, 2, 3]),
        # matplotlib fills columns of heights [2, 2, 1, 1] top to bottom
        (6, 4, [0, 4, 1, 5, 2, 3]),
        (5, 4, [0, 4, 1, 2, 3]),
    ],
)
def test_row_major_makes_column_filled_legend_read_across(
    count: int, columns: int, expected: list[int]
) -> None:
    assert _row_major(list(range(count)), columns) == expected


def test_customer_colors_follow_rank_and_keep_other_grey() -> None:
    slots = CustomerPalette(named=("#111111", "#222222", "#333333"), other="#eeeeee")
    colors = _customer_colors(slots, ["Big", "Small", _OTHER_LABEL])
    assert colors == {"Big": "#111111", "Small": "#222222", _OTHER_LABEL: "#eeeeee"}


def test_stream_bounds_stack_symmetrically_about_zero() -> None:
    layers = [pd.Series([1.0, 4.0]), pd.Series([3.0, 0.0])]
    (low_a, high_a), (low_b, high_b) = _stream_bounds(layers)
    np.testing.assert_allclose(low_a, [-2.0, -2.0])
    np.testing.assert_allclose(high_a, low_b)
    np.testing.assert_allclose(high_b, [2.0, 2.0])


def test_week_edges_give_each_snapshot_a_whole_week() -> None:
    dates = pd.to_datetime(pd.Series(["2026-01-02", "2026-01-09", "2026-01-16"]))
    edges = _week_edges(dates)
    np.testing.assert_allclose(np.diff(edges), [7.0, 7.0, 7.0])
    assert edges[0] == pytest.approx(edges[1] - 7.0)


def test_chart_style_does_not_leak_into_global_matplotlib(tmp_path: Path) -> None:
    invoices = load_invoices_from_csv(io.StringIO(_sample_csv(customers=2, weeks=3)))
    series = compute_weekly_dso(
        tuple(invoices),
        date(2026, 1, 9),
        date(2026, 1, 30),
        fx=make_fixed_fx_provider(),
    )
    before = dict(plt.rcParams)
    generate_dso_chart(series, output_path=tmp_path / "dso.png")
    assert dict(plt.rcParams) == before


def test_pack_survives_a_failed_rate_fetch(tmp_path: Path) -> None:
    def broken(start: date, end: date) -> dict[date, Decimal]:
        raise FXDataError("offline")

    outputs = generate_report_pack(
        ReportSettings(
            input_source=io.StringIO(_sample_csv(customers=2, weeks=3)),
            output_dir=tmp_path,
            as_of=AS_OF,
            fx_fetcher=broken,
        )
    )
    assert outputs["certificate"].exists()
