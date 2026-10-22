import io
import re
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from abl.certificate import generate_certificate_pdf
from abl.detail_page import (
    _groups,
    _status_text,
    detail_flowables,
    make_footer_painter,
)
from abl.engine import evaluate_snapshot
from abl.fx import make_fixed_fx_provider
from abl.models import (
    CertificateParams,
    CollateralReport,
    Invoice,
    InvoiceLine,
    LendingCriteria,
    LineStatus,
    ReportPalette,
    ReportSettings,
)
from abl.pack import generate_report_pack

AS_OF = date(2026, 3, 13)
FX = make_fixed_fx_provider(Decimal("1.40"))
FONTS = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")
PAGE_OBJECT = re.compile(rb"/Type\s*/Page\b")
CSV_HEADER = (
    "Client Name,Invoice Date,Due Date,Invoice Amount,Currency,Invoice Paid Date"
)


def _invoices(count: int) -> list[Invoice]:
    return [
        Invoice(
            f"Client {n:03d}",
            AS_OF - timedelta(days=20),
            AS_OF + timedelta(days=30),
            Decimal("1000"),
            "CAD",
        )
        for n in range(count)
    ]


def _report(count: int) -> CollateralReport:
    return evaluate_snapshot(_invoices(count), AS_OF, LendingCriteria(), FX)


def _render(report: CollateralReport, folder: Path) -> bytes:
    """Writes the certificate for `report` and returns the PDF bytes."""
    target = folder / "cert.pdf"
    generate_certificate_pdf(
        report=report,
        criteria=LendingCriteria(),
        params=CertificateParams(),
        concentrations=[],
        output_path=target,
    )
    return target.read_bytes()


def _pages(report: CollateralReport, folder: Path) -> int:
    return len(PAGE_OBJECT.findall(_render(report, folder)))


def test_a_report_without_lines_stays_one_page(tmp_path: Path) -> None:
    bare = CollateralReport(
        week=_report(1).week,
        eligible_amount_cad=Decimal("0.00"),
        ineligible_amount_cad=Decimal("0.00"),
        net_available_borrow_cad=Decimal("0.00"),
    )
    assert _pages(bare, tmp_path) == 1


def test_a_report_with_lines_adds_a_detail_page(tmp_path: Path) -> None:
    assert _pages(_report(10), tmp_path) == 2


def test_a_long_log_flows_onto_further_pages(tmp_path: Path) -> None:
    assert _pages(_report(400), tmp_path) >= 3


def test_pack_writes_the_detail_page(tmp_path: Path) -> None:
    rows = [CSV_HEADER] + [
        f"Client {n},2026-03-02,2026-04-01,100.00,CAD," for n in range(5)
    ]
    outputs = generate_report_pack(
        ReportSettings(
            input_source=io.StringIO("\n".join(rows)),
            output_dir=tmp_path,
            as_of=AS_OF,
        )
    )
    assert len(PAGE_OBJECT.findall(outputs["certificate"].read_bytes())) == 2


@pytest.mark.parametrize(
    ("status", "ineligible", "expected"),
    [
        (LineStatus.ELIGIBLE, "0.00", "Eligible"),
        (LineStatus.INELIGIBLE, "100.00", "Ineligible"),
        (LineStatus.HAIRCUT, "27.00", "Haircut 27%"),
        (LineStatus.CREDIT, "0.00", "Credit note"),
    ],
)
def test_status_text(status: LineStatus, ineligible: str, expected: str) -> None:
    line = InvoiceLine(
        client_name="A",
        invoice_id="",
        invoice_date=AS_OF,
        due_date=AS_OF,
        days_past_due=0,
        currency="CAD",
        face_amount=Decimal("100"),
        fx_rate=Decimal("1"),
        gross_cad=Decimal("100.00"),
        eligible_cad=Decimal("100.00") - Decimal(ineligible),
        ineligible_cad=Decimal(ineligible),
        status=status,
    )
    assert _status_text(line) == expected


def test_lines_group_by_debtor_in_order() -> None:
    invoices = [*_invoices(2), *_invoices(2)]
    lines = evaluate_snapshot(invoices, AS_OF, LendingCriteria(), FX).lines
    groups = _groups(lines)
    assert [(name, len(rows)) for name, rows in groups] == [
        ("Client 000", 2),
        ("Client 001", 2),
    ]


def test_detail_flowables_has_header_frame_break_then_a_table_per_debtor() -> None:
    report = _report(3)
    flow = detail_flowables(report, (), "", ReportPalette(), FONTS)
    names = [type(item).__name__ for item in flow]
    assert names.count("_FrameBreak") == 1
    assert names.count("KeepTogether") == 3
    assert names.index("_FrameBreak") < names.index("KeepTogether")


class _Canvas:
    """Records what the footer painter draws."""

    def __init__(self) -> None:
        self.text: list[str] = []

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *args, **kwargs: None

    def drawString(self, _x: float, _y: float, text: str) -> None:
        self.text.append(text)

    drawRightString = drawCentredString = drawString


class _Doc:
    leftMargin = 36
    width = 720
    page = 2


def test_footer_says_page_n_of_total_with_status_and_disclaimer() -> None:
    canvas = _Canvas()
    painter = make_footer_painter(
        "STATUS: PRO-FORMA", "confidential", 3, ReportPalette(), FONTS
    )
    painter(canvas, _Doc())
    assert any("Page 2 of 3" in text for text in canvas.text)
    assert "STATUS: PRO-FORMA" in canvas.text
    assert "CONFIDENTIAL" in canvas.text
