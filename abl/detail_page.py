"""Invoice detail pages of the Borrowing Base Certificate: the audit log behind page 1.

Two compact columns per page. Each debtor is a small table whose invoice rows are
self-describing, so no column header needs repeating.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import groupby
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    FrameBreak,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .activity import SettledLine
from .models import CollateralReport, InvoiceLine, LineStatus, ReportPalette

COLUMN_WIDTHS = (
    36,
    44,
    62,
    62,
    52,
    52,
    46,
)  # id, issued, due, face, gross, elig, status
COLUMN_WIDTH = sum(COLUMN_WIDTHS)
COLUMN_GAP = 12
HEADER_HEIGHT = 68
FOOTER_HEIGHT = 30
FIRST_TEMPLATE = "detail_first"
NEXT_TEMPLATE = "detail_cont"

type FooterPainter = Callable[[Canvas, BaseDocTemplate], None]


@dataclass(frozen=True, slots=True)
class _Styles:
    """Paragraph styles shared by the detail tables."""

    cell: ParagraphStyle
    right: ParagraphStyle
    bold: ParagraphStyle
    bold_right: ParagraphStyle
    note: ParagraphStyle
    title: ParagraphStyle
    status: dict[LineStatus, ParagraphStyle]


def _styles(theme: ReportPalette, fonts: tuple[str, str, str]) -> _Styles:
    """Builds the compact styles; status colour is keyed by `LineStatus`."""
    regular, bold, oblique = fonts
    cell = ParagraphStyle(
        "DetailCell",
        fontName=regular,
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor(theme.primary),
    )
    bold_cell = ParagraphStyle("DetailBold", parent=cell, fontName=bold)
    return _Styles(
        cell=cell,
        right=ParagraphStyle("DetailRight", parent=cell, alignment=2),
        bold=bold_cell,
        bold_right=ParagraphStyle("DetailBoldRight", parent=bold_cell, alignment=2),
        note=ParagraphStyle(
            "DetailNote",
            parent=cell,
            fontName=oblique,
            fontSize=5.8,
            leading=7,
            textColor=colors.HexColor(theme.muted),
        ),
        title=ParagraphStyle(
            "DetailTitle", parent=bold_cell, fontSize=9, leading=11, spaceAfter=2
        ),
        status={
            LineStatus.ELIGIBLE: ParagraphStyle(
                "StatusEligible",
                parent=cell,
                textColor=colors.HexColor(theme.muted),
            ),
            LineStatus.HAIRCUT: ParagraphStyle(
                "StatusHaircut",
                parent=bold_cell,
                textColor=colors.HexColor(theme.accent),
            ),
            LineStatus.INELIGIBLE: ParagraphStyle(
                "StatusIneligible",
                parent=bold_cell,
                textColor=colors.HexColor(theme.alert),
            ),
        },
    )


def _money(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _status_text(line: InvoiceLine) -> str:
    """Short status: Eligible, Haircut n%, or Ineligible."""
    match line.status:
        case LineStatus.ELIGIBLE:
            return "Eligible"
        case LineStatus.HAIRCUT:
            return f"Haircut {line.haircut_pct:.0%}"
        case LineStatus.INELIGIBLE:
            return "Ineligible"


def _invoice_row(line: InvoiceLine, st: _Styles) -> list[Paragraph]:
    """Renders an invoice's cells; each one names itself, so there is no header row."""
    issued = f"{line.invoice_date:%Y-%m-%d}" + ("*" if line.is_new else "")
    late = f" +{line.days_past_due}d" if line.days_past_due else ""
    return [
        Paragraph(escape(line.invoice_id or "-"), st.cell),
        Paragraph(issued, st.cell),
        Paragraph(f"{line.due_date:%Y-%m-%d}{late}", st.cell),
        Paragraph(f"{escape(line.currency)} {_money(line.face_amount)}", st.right),
        Paragraph(_money(line.gross_cad), st.right),
        Paragraph(_money(line.eligible_cad), st.right),
        Paragraph(_status_text(line), st.status[line.status]),
    ]


def _debtor_row(client: str, lines: Sequence[InvoiceLine], st: _Styles) -> list[object]:
    """Renders the debtor's name with its gross and eligible totals."""
    gross = sum((ln.gross_cad for ln in lines), Decimal("0.00"))
    eligible = sum((ln.eligible_cad for ln in lines), Decimal("0.00"))
    return [
        Paragraph(escape(client), st.bold),
        "",
        "",
        "",
        Paragraph(_money(gross), st.bold_right),
        Paragraph(_money(eligible), st.bold_right),
        Paragraph(f"{len(lines)} inv", st.right),
    ]


def _debtor_table(
    client: str, lines: Sequence[InvoiceLine], st: _Styles, theme: ReportPalette
) -> Table:
    """One debtor: a name row, then each invoice, with its reason beneath if any."""
    rows: list[list[object]] = [_debtor_row(client, lines, st)]
    spans = [("SPAN", (0, 0), (3, 0))]
    for line in lines:
        rows.append(_invoice_row(line, st))
        if line.detail:
            spans.append(("SPAN", (0, len(rows)), (-1, len(rows))))
            note = Paragraph(f"&nbsp;&nbsp;{escape(line.detail)}", st.note)
            rows.append([note, *[""] * (len(COLUMN_WIDTHS) - 1)])
    table = Table(rows, colWidths=COLUMN_WIDTHS, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.bg_subtle)),
                ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor(theme.border)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (6, 0), (6, -1), 7),
                *spans,
            ]
        )
    )
    return table


def _total_table(
    lines: Sequence[InvoiceLine], st: _Styles, theme: ReportPalette
) -> Table:
    """Totals the open collateral, so the log can be ticked back to page 1."""
    gross = sum((ln.gross_cad for ln in lines), Decimal("0.00"))
    eligible = sum((ln.eligible_cad for ln in lines), Decimal("0.00"))
    row = [
        Paragraph("TOTAL OPEN COLLATERAL (CAD)", st.bold),
        "",
        "",
        "",
        Paragraph(_money(gross), st.bold_right),
        Paragraph(_money(eligible), st.bold_right),
        Paragraph(f"{len(lines)} inv", st.right),
    ]
    table = Table([row], colWidths=COLUMN_WIDTHS)
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (3, 0)),
                ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.HexColor(theme.primary)),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _settled_table(
    settled: Sequence[SettledLine], st: _Styles, theme: ReportPalette
) -> Table:
    """Lists invoices paid this week, valued at their settlement-date rate."""
    head = [("Debtor", st.bold), ("Invoice", st.bold), ("Paid", st.bold)]
    head += [(name, st.bold_right) for name in ("Face", "Rate", "CAD")]
    rows: list[list[object]] = [[Paragraph(text, style) for text, style in head]]
    rows.extend(
        [
            Paragraph(escape(s.client_name), st.cell),
            Paragraph(escape(s.invoice_id or "-"), st.cell),
            Paragraph(f"{s.paid_date:%Y-%m-%d}", st.cell),
            Paragraph(f"{escape(s.currency)} {_money(s.face_amount)}", st.right),
            Paragraph(f"{s.fx_rate:.4f}", st.right),
            Paragraph(_money(s.cad_amount), st.right),
        ]
        for s in settled
    )
    widths = (112, 36, 52, 70, 38, 46)
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor(theme.primary)),
                ("TOPPADDING", (0, 0), (-1, -1), 1.2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _strip_cell(label: str, value: str, st: _Styles, theme: ReportPalette) -> Paragraph:
    return Paragraph(f"<font color='{theme.muted}'>{label}</font><br/>{value}", st.bold)


def _summary_strip(
    report: CollateralReport,
    settled: Sequence[SettledLine],
    st: _Styles,
    theme: ReportPalette,
) -> Table:
    """Four figures: open, issued this week, settled this week, reconciliation."""
    new = [ln for ln in report.lines if ln.is_new]
    new_total = sum((ln.gross_cad for ln in new), Decimal("0.00"))
    settled_total = sum((s.cad_amount for s in settled), Decimal("0.00"))
    cells = [
        _strip_cell(
            "OPEN INVOICES (GROSS COLLATERAL)",
            f"{len(report.lines)} &bull; ${_money(report.gross_amount_cad)}",
            st,
            theme,
        ),
        _strip_cell(
            "ISSUED THIS WEEK (STILL OPEN)*",
            f"{len(new)} &bull; ${_money(new_total)}",
            st,
            theme,
        ),
        _strip_cell(
            "SETTLED THIS WEEK (ACTIVITY)",
            f"{len(settled)} &bull; ${_money(settled_total)}",
            st,
            theme,
        ),
        _strip_cell(
            "GROSS &minus; INELIGIBLE = ELIGIBLE",
            f"{_money(report.gross_amount_cad)} &minus; "
            f"{_money(report.ineligible_amount_cad)} = "
            f"{_money(report.eligible_amount_cad)}",
            st,
            theme,
        ),
    ]
    table = Table([cells], colWidths=[180] * 4)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(theme.bg_subtle)),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.border)),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.border)),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _groups(lines: Sequence[InvoiceLine]) -> list[tuple[str, list[InvoiceLine]]]:
    """Groups the (already sorted) lines by debtor."""
    return [
        (client, list(group))
        for client, group in groupby(lines, key=lambda ln: ln.client_name)
    ]


def detail_flowables(
    report: CollateralReport,
    settled: Sequence[SettledLine],
    fx_note: str,
    theme: ReportPalette,
    fonts: tuple[str, str, str],
) -> list[Flowable]:
    """Builds the detail pages: a header block, then debtors in two columns."""
    st = _styles(theme, fonts)
    as_of = report.as_of_date
    flow: list[Flowable] = [
        Paragraph(
            f"<b>INVOICE DETAIL &mdash; GROSS COLLATERAL AS OF {as_of:%Y-%m-%d}</b>",
            st.title,
        ),
        _summary_strip(report, settled, st, theme),
        Spacer(1, 3),
        Paragraph(
            "Columns: invoice id, issued (* = this week), due date (+days past "
            "due), face as booked, gross CAD, eligible CAD, status. An invoice shows "
            "the first covenant it fails (contra, then aged, then taint), so a "
            "tainted debtor's aged invoices read as aged. " + escape(fx_note),
            st.note,
        ),
        FrameBreak(),
    ]
    flow.extend(
        KeepTogether([_debtor_table(client, group, st, theme), Spacer(1, 3)])
        for client, group in _groups(report.lines)
    )
    flow.append(_total_table(report.lines, st, theme))
    if settled:
        flow.append(Spacer(1, 8))
        flow.append(
            Paragraph("<b>SETTLED THIS WEEK (CAD AT SETTLEMENT RATE)</b>", st.title)
        )
        flow.append(_settled_table(settled, st, theme))
    return flow


def make_footer_painter(
    status_text: str,
    disclaimer: str,
    total_pages: int,
    theme: ReportPalette,
    fonts: tuple[str, str, str],
) -> FooterPainter:
    """Returns a painter for the footer that detail pages draw on the canvas."""
    _, bold, _ = fonts

    def paint(canvas: Canvas, doc: BaseDocTemplate) -> None:
        left = doc.leftMargin
        right = left + doc.width
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor(theme.border))
        canvas.line(left, 28, right, 28)
        canvas.setFont(bold, 6.5)
        canvas.setFillColor(colors.HexColor(theme.muted))
        canvas.drawString(left, 20, status_text)
        canvas.drawRightString(
            right,
            20,
            f"Vertiqal Studios ABL Reporter • Page {doc.page} of {total_pages}",
        )
        canvas.setFillColor(colors.HexColor(theme.alert))
        canvas.drawCentredString((left + right) / 2, 10, disclaimer.upper())
        canvas.restoreState()

    return paint


def detail_page_templates(
    doc: BaseDocTemplate, painter: FooterPainter
) -> list[PageTemplate]:
    """Returns the first detail page (header block plus two columns) and the rest."""
    left = doc.leftMargin
    column_top = doc.bottomMargin + doc.height
    body_bottom = doc.bottomMargin + FOOTER_HEIGHT

    def columns(top: float) -> list[Frame]:
        return [
            Frame(
                left + i * (COLUMN_WIDTH + COLUMN_GAP),
                body_bottom,
                COLUMN_WIDTH,
                top - body_bottom,
                id=f"col{i}",
                leftPadding=0,
                rightPadding=0,
                topPadding=0,
                bottomPadding=0,
            )
            for i in range(2)
        ]

    header = Frame(
        left,
        column_top - HEADER_HEIGHT,
        doc.width,
        HEADER_HEIGHT,
        id="header",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    return [
        PageTemplate(
            id=FIRST_TEMPLATE,
            frames=[header, *columns(column_top - HEADER_HEIGHT)],
            onPage=painter,
            autoNextPageTemplate=NEXT_TEMPLATE,
        ),
        PageTemplate(id=NEXT_TEMPLATE, frames=columns(column_top), onPage=painter),
    ]
