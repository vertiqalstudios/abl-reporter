"""Borrowing Base Certificate PDF report generation using ReportLab."""

import warnings
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .bbc import generate_bbc
from .fx import FXProvider, convert_to_cad, make_fixed_fx_provider
from .importer import load_invoices_from_csv
from .models import (
    CertificateParams,
    CollateralReport,
    CurrencyBreakdown,
    DebtorConcentration,
    IneligibilityReason,
    Invoice,
    LendingCriteria,
    ReportPalette,
    ReportSettings,
    ReportStatus,
)
from .rules import is_invoice_open

# Silence runpy's double-import warning for `python -m abl.certificate`.
warnings.filterwarnings(
    "ignore", category=RuntimeWarning, message=".*'abl.certificate'.*"
)

_FONT_DIRS = (
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path("~/Library/Fonts").expanduser(),
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path("~/.fonts").expanduser(),
)

# (registered name, file glob stem, core-font fallback)
_FONT_VARIANTS = (
    ("HelveticaNeue", "*HelveticaNeue*", "Helvetica"),
    ("HelveticaNeue-Bold", "*HelveticaNeue*Bold*", "Helvetica-Bold"),
    ("HelveticaNeue-Italic", "*HelveticaNeue*Italic*", "Helvetica-Oblique"),
)

_CURRENCY_SORT_ORDER = {"CAD": 0, "USD": 1}
_CURRENCY_LABELS = {"CAD": "Domestic", "USD": "US Dollar"}


def _find_font_file(pattern: str) -> Path | None:
    """Returns the first TTF/OTF matching pattern across system font directories."""
    for font_dir in _FONT_DIRS:
        if not font_dir.exists():
            continue
        for ext in (".ttf", ".otf"):
            match = next(font_dir.rglob(f"{pattern}{ext}"), None)
            if match is not None:
                return match
    return None


def _register_font(name: str, pattern: str, fallback: str) -> str:
    """Registers a system font under name, returning fallback if unavailable."""
    if name in pdfmetrics.getRegisteredFontNames():
        return name
    font_file = _find_font_file(pattern)
    if font_file is None:
        return fallback
    try:
        pdfmetrics.registerFont(TTFont(name, str(font_file)))
    except Exception:  # ReportLab raises assorted errors on unreadable fonts
        return fallback
    return name


def _resolve_fonts() -> tuple[str, str, str]:
    """Resolves Helvetica Neue font family with fallback to core Helvetica."""
    regular, bold, oblique = (_register_font(*v) for v in _FONT_VARIANTS)
    return regular, bold, oblique


def _cad_rate(currency: str, as_of: date, fx: FXProvider) -> Decimal:
    """Returns the CAD FX rate for a currency, 1.0000 for CAD itself."""
    if currency == "CAD":
        return Decimal("1.0000")
    return fx(currency, "CAD", as_of)


def _totals_by_currency(
    open_invoices: Sequence[Invoice], as_of: date, fx: FXProvider
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Sums face and CAD amounts per currency, rounding per invoice like the engine."""
    face_totals: dict[str, Decimal] = defaultdict(Decimal)
    cad_totals: dict[str, Decimal] = defaultdict(Decimal)
    for inv in open_invoices:
        currency = inv.currency.upper()
        face_totals[currency] += inv.invoice_amount
        cad_totals[currency] += convert_to_cad(inv, as_of, fx).invoice_amount
    return face_totals, cad_totals


def _share_of(part: Decimal, total: Decimal) -> Decimal:
    """Returns part / total to four places, or zero for a non-positive total."""
    if total <= Decimal("0.00"):
        return Decimal("0.0000")
    return (part / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _currency_sort_key(breakdown: CurrencyBreakdown) -> tuple[int, str]:
    """Orders CAD first, USD second, then remaining currencies alphabetically."""
    return _CURRENCY_SORT_ORDER.get(breakdown.currency, 2), breakdown.currency


def _currency_label(code: str) -> str:
    """Returns the bolded currency code with a descriptive suffix where known."""
    suffix = _CURRENCY_LABELS.get(code)
    return f"<b>{code}</b> ({suffix})" if suffix else f"<b>{code}</b>"


def _single_currency_breakdown(
    report: CollateralReport, params: CertificateParams
) -> list[CurrencyBreakdown]:
    """Synthesizes a one-row breakdown from gross AR in the base currency."""
    return [
        CurrencyBreakdown(
            currency=params.currency,
            face_amount=report.gross_amount_cad,
            fx_rate=Decimal("1.0000"),
            cad_amount=report.gross_amount_cad,
            share_pct=Decimal("1.0000"),
        )
    ]


def _display_concentrations(
    concentrations: Sequence[DebtorConcentration], gross: Decimal, top_n: int = 5
) -> list[DebtorConcentration]:
    """Returns the top debtors, rolling the remainder into one "Other" row."""
    top, others = list(concentrations[:top_n]), concentrations[top_n:]
    if not others:
        return top
    other_balance = sum((d.balance_cad for d in others), Decimal("0.00"))
    other_row = DebtorConcentration(
        client_name=f"Other Accounts ({len(others)})",
        balance_cad=other_balance,
        share_pct=_share_of(other_balance, gross),
        is_capped=False,
        eligible_share_pct=sum((d.eligible_share_pct for d in others), Decimal("0")),
    )
    return [*top, other_row]


def calculate_currency_breakdown(
    invoices: Sequence[Invoice],
    as_of: date,
    fx: FXProvider,
) -> list[CurrencyBreakdown]:
    """Aggregates open balances by currency with CAD equivalents and shares."""
    open_invoices = [inv for inv in invoices if is_invoice_open(inv, as_of)]
    if not open_invoices:
        return []

    face_totals, cad_totals = _totals_by_currency(open_invoices, as_of, fx)
    total_cad = sum(cad_totals.values(), Decimal("0.00"))
    breakdowns = [
        CurrencyBreakdown(
            currency=curr,
            face_amount=face,
            fx_rate=_cad_rate(curr, as_of, fx),
            cad_amount=cad_totals[curr],
            share_pct=_share_of(cad_totals[curr], total_cad),
        )
        for curr, face in face_totals.items()
    ]
    return sorted(breakdowns, key=_currency_sort_key)


def format_deduction(amount: Decimal) -> str:
    """Formats a ledger deduction in accounting style: `($1,234.00)`, or `$0.00`."""
    if amount <= Decimal("0.00"):
        return "$0.00"
    return f"(${amount:,.2f})"


def pre_cap_eligible_by_client(report: CollateralReport) -> dict[str, Decimal]:
    """Returns each debtor's eligible balance before the concentration cap trimmed it.

    This is the base the cap is measured against: kept balance plus cap excess.
    """
    balances: dict[str, Decimal] = defaultdict(Decimal)
    for inv in report.eligible_invoices:
        balances[inv.client_name] += inv.invoice_amount
    for rec in report.ineligible_invoices:
        if rec.reason == IneligibilityReason.CONCENTRATION_EXCESS:
            balances[rec.client_name] += rec.ineligible_balance
    return dict(balances)


def _eligible_shares(balances: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Returns each debtor's share of the pre-cap eligible pool."""
    total = sum(balances.values(), Decimal("0.00"))
    return {client: _share_of(amount, total) for client, amount in balances.items()}


def capped_client_names(report: CollateralReport) -> frozenset[str]:
    """Returns debtors the engine trimmed for breaching the concentration cap."""
    return frozenset(
        rec.client_name
        for rec in report.ineligible_invoices
        if rec.reason == IneligibilityReason.CONCENTRATION_EXCESS
    )


def calculate_debtor_concentrations(
    invoices: Sequence[Invoice],
    as_of: date,
    fx: FXProvider,
    capped_clients: frozenset[str] = frozenset(),
    eligible_balances: Mapping[str, Decimal] | None = None,
) -> list[DebtorConcentration]:
    """Returns open CAD balance and share per debtor, flagging capped debtors.

    The cap applies to gross eligible collateral, not gross AR, so callers pass
    the engine's capped debtors (`capped_client_names`) rather than a share test,
    and `pre_cap_eligible_by_client` to report each debtor's share of that pool.
    """
    open_invoices = [
        convert_to_cad(inv, as_of, fx)
        for inv in invoices
        if is_invoice_open(inv, as_of)
    ]
    if not open_invoices:
        return []

    client_totals: dict[str, Decimal] = defaultdict(Decimal)
    for inv in open_invoices:
        client_totals[inv.client_name] += inv.invoice_amount

    gross_total = sum(client_totals.values(), Decimal("0.00"))
    if gross_total <= Decimal("0.00"):
        return []

    sorted_clients = sorted(client_totals.items(), key=lambda kv: kv[1], reverse=True)
    eligible_shares = _eligible_shares(eligible_balances or {})

    concentrations: list[DebtorConcentration] = []
    for client_name, balance in sorted_clients:
        share = (balance / gross_total).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        concentrations.append(
            DebtorConcentration(
                client_name=client_name,
                balance_cad=balance,
                share_pct=share,
                is_capped=client_name in capped_clients,
                eligible_share_pct=eligible_shares.get(client_name, Decimal("0.0000")),
            )
        )
    return concentrations


@dataclass(frozen=True, slots=True)
class _Ink:
    """Resolved fonts and colours shared by the certificate's vector drawings."""

    font_reg: str
    font_bold: str
    primary: Color
    muted: Color
    alert: Color
    border: Color
    bg_subtle: Color


def _waterfall_drawing(
    steps: Sequence[tuple[str, float, str, Color]], max_scale: float, ink: _Ink
) -> Drawing:
    """Draws the capital availability step bars, scaled to max_scale."""
    av_draw = Drawing(370, 70)
    av_draw.add(
        String(
            0,
            58,
            "CAPITAL AVAILABILITY WATERFALL",
            fontName=ink.font_bold,
            fontSize=7,
            fillColor=ink.primary,
        )
    )

    y_av = 42
    for title, val, txt, col in steps:
        w = max(2, int((val / max_scale) * 215)) if val > 0 else 2
        av_draw.add(
            String(
                0,
                y_av + 1,
                title,
                fontName=ink.font_reg,
                fontSize=6.5,
                fillColor=ink.primary,
            )
        )
        av_draw.add(Rect(115, y_av, w, 6.5, fillColor=col, strokeColor=None))
        av_draw.add(
            String(
                115 + w + 5,
                y_av + 1,
                txt,
                fontName=ink.font_bold,
                fontSize=6.5,
                fillColor=col,
            )
        )
        y_av -= 13
    return av_draw


def _readable_label_color(fill: Color, ink: _Ink) -> Color:
    """Returns white text on dark fills and the primary ink on light ones."""
    luminance = 0.299 * fill.red + 0.587 * fill.green + 0.114 * fill.blue
    return ink.primary if luminance > 0.6 else colors.white


_BAR_WIDTH = 310
_BAR_Y = 12
_BAR_HEIGHT = 11


def _concentration_bar_drawing(
    display_list: Sequence[DebtorConcentration],
    share_of: Callable[[DebtorConcentration], Decimal],
    title: str,
    total_label: str,
    debtor_hex_colors: Sequence[Color],
    ink: _Ink,
) -> Drawing:
    """Draws a 100% stacked debtor bar, sized by `share_of` on its own base."""
    conc_bar_draw = Drawing(340, 40)
    conc_bar_draw.add(
        String(
            0,
            30,
            title,
            fontName=ink.font_bold,
            fontSize=6.5,
            fillColor=ink.primary,
        )
    )

    bar_w_total = _BAR_WIDTH
    bar_x = 0
    bar_y = _BAR_Y
    bar_h = _BAR_HEIGHT

    curr_x = bar_x
    for i, item in enumerate(display_list):
        pct = float(share_of(item))
        w_seg = max(1, int(pct * bar_w_total))
        color_idx = min(i, len(debtor_hex_colors) - 1)
        seg_color = debtor_hex_colors[color_idx]
        conc_bar_draw.add(
            Rect(
                curr_x,
                bar_y,
                w_seg,
                bar_h,
                fillColor=seg_color,
                strokeColor=colors.white,
                strokeWidth=0.5,
            )
        )
        if pct >= 0.12 and w_seg > 24:
            conc_bar_draw.add(
                String(
                    curr_x + (w_seg / 2) - 6,
                    bar_y + 3,
                    f"{pct*100:.0f}%",
                    fontName=ink.font_bold,
                    fontSize=6,
                    fillColor=_readable_label_color(seg_color, ink),
                )
            )
        curr_x += w_seg

    conc_bar_draw.add(
        String(
            bar_w_total - 60,
            bar_y - 10,
            total_label,
            fontName=ink.font_reg,
            fontSize=6,
            fillColor=ink.muted,
        )
    )
    return conc_bar_draw


def _definition_paragraphs(style: ParagraphStyle) -> list[Paragraph]:
    """Returns the footnotes defining the concentration table's share columns."""
    return [
        Paragraph(
            "<b>Definitions.</b> "
            "<super>1</super> <b>% AR</b>: the debtor's open balance as a share of "
            "gross accounts receivable, before any exclusions.",
            style,
        ),
        Paragraph(
            "<super>2</super> <b>% Elig.</b>: the debtor's balance as a share of "
            "gross eligible collateral, i.e. after contra, aged past due and "
            "cross-default exclusions but before the concentration cap is applied.",
            style,
        ),
    ]


def _ineligibility_drawing(
    items: Sequence[tuple[str, float, Color]],
    max_inelig: float,
    gross_val: float,
    ink: _Ink,
) -> Drawing:
    """Draws one bar per ineligibility reason, labelled with its share of gross AR."""
    inelig_draw = Drawing(340, 58)
    inelig_draw.add(
        String(
            0,
            48,
            "INELIGIBILITY BREAKDOWN BY REASON",
            fontName=ink.font_bold,
            fontSize=7,
            fillColor=ink.primary,
        )
    )

    y_in = 34
    for name, val, bar_color in items:
        w = max(2, int((val / max_inelig) * 110)) if val > 0 else 0
        pct_of_gross = (val / gross_val * 100) if gross_val > 0 else 0.0
        label_txt = (
            f"${val/1e3:,.1f}K ({pct_of_gross:.1f}%)" if val > 0 else "$0.00 (0.0%)"
        )

        inelig_draw.add(
            String(
                0,
                y_in + 1,
                name,
                fontName=ink.font_reg,
                fontSize=6.5,
                fillColor=ink.primary,
            )
        )
        inelig_draw.add(
            Rect(
                135,
                y_in,
                110,
                6,
                fillColor=ink.bg_subtle,
                strokeColor=ink.border,
                strokeWidth=0.5,
            )
        )
        if w > 0:
            inelig_draw.add(
                Rect(135, y_in, w, 6, fillColor=bar_color, strokeColor=None)
            )
        inelig_draw.add(
            String(
                135 + 110 + 5,
                y_in + 1,
                label_txt,
                fontName=ink.font_bold if val > 0 else ink.font_reg,
                fontSize=6,
                fillColor=bar_color if val > 0 else ink.muted,
            )
        )
        y_in -= 11
    return inelig_draw


def generate_certificate_pdf(
    report: CollateralReport,
    criteria: LendingCriteria,
    params: CertificateParams,
    concentrations: Sequence[DebtorConcentration],
    currency_breakdowns: Sequence[CurrencyBreakdown] | None = None,
    invoice_start: date | None = None,
    invoice_end: date | None = None,
    palette: ReportPalette | None = None,
    output_path: str | Path = "bbc_certificate.pdf",
) -> Path:
    """Builds the single-page landscape Borrowing Base Certificate PDF."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    theme = palette if palette is not None else ReportPalette()
    font_reg, font_bold, font_oblique = _resolve_fonts()

    c_primary = colors.HexColor(theme.primary)
    c_muted = colors.HexColor(theme.muted)
    c_subtle = colors.HexColor(theme.subtle)
    c_accent = colors.HexColor(theme.accent)
    c_alert = colors.HexColor(theme.alert)
    c_border = colors.HexColor(theme.border)
    c_bg_subtle = colors.HexColor(theme.bg_subtle)
    debtor_hex_colors = [colors.HexColor(c) for c in theme.debtor_colors]
    ink = _Ink(
        font_reg=font_reg,
        font_bold=font_bold,
        primary=c_primary,
        muted=c_muted,
        alert=c_alert,
        border=c_border,
        bg_subtle=c_bg_subtle,
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=20,
        bottomMargin=16,
    )

    styles = getSampleStyleSheet()

    s_title = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=17,
        leading=20,
        textColor=c_primary,
    )
    s_h2 = ParagraphStyle(
        "Heading2",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=8.5,
        leading=11,
        textColor=c_primary,
        spaceAfter=2,
    )
    s_body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName=font_reg,
        fontSize=8,
        leading=10.5,
        textColor=c_primary,
    )
    s_sub = ParagraphStyle(
        "DocSub",
        parent=styles["Normal"],
        fontName=font_oblique,
        fontSize=7,
        leading=9,
        textColor=c_muted,
    )
    s_bold = ParagraphStyle(
        "BodyBold",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=8,
        leading=10.5,
        textColor=c_primary,
    )
    s_right = ParagraphStyle("Right", parent=s_body, alignment=2)
    s_right_bold = ParagraphStyle("RightBold", parent=s_bold, alignment=2)
    s_right_accent = ParagraphStyle(
        "RightAccent", parent=s_bold, alignment=2, textColor=c_accent
    )
    s_right_alert = ParagraphStyle(
        "RightAlert", parent=s_bold, alignment=2, textColor=c_alert
    )
    s_param_val = ParagraphStyle(
        "ParamVal",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=7,
        leading=9.5,
        textColor=c_primary,
    )

    # Status configuration
    status_label = f"[ {params.status.value.replace('_', '-')} ]"
    status_sub = {
        ReportStatus.PRO_FORMA: "Sensitivity Modeling Run",
        ReportStatus.DRAFT: "Internal QA & Verification",
        ReportStatus.CERTIFIED: "Official Certified Borrowing Base",
    }.get(params.status, "Certificate Run")

    # 1. HEADER SECTION
    as_of = report.as_of_date
    start_str = invoice_start.isoformat() if invoice_start is not None else "Historical"
    end_str = invoice_end.isoformat() if invoice_end is not None else as_of.isoformat()
    gen_time_str = params.generated_at.strftime("%Y-%m-%d %H:%M UTC")

    header_data = [
        [
            Paragraph(
                "<b>VERTIQAL STUDIOS</b><br/>"
                "<font size='13'><b>Borrowing Base Certificate</b></font>",
                s_title,
            ),
            Paragraph(
                f"<b>As of Date:</b> {as_of.isoformat()} &bull; "
                f"<b>Run Date:</b> {gen_time_str}<br/>"
                f"<b>Credit Facility Limit:</b> ${params.facility_limit:,.2f} "
                f"{params.currency} &bull; <b>Base Currency:</b> {params.currency}",
                ParagraphStyle("HRight", parent=s_body, alignment=2),
            ),
            Paragraph(
                f"<font color='{theme.accent}'><b>{status_label}</b></font><br/>"
                f"<font size='6.5' color='{theme.muted}'>{status_sub}</font>",
                ParagraphStyle("Badge", parent=s_body, alignment=2),
            ),
        ]
    ]
    header_table = Table(header_data, colWidths=[330, 260, 130])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, -1), 1.0, c_primary),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    # 2. RUNTIME PARAMETERS STRIP (Quiet, compact 4-cell horizontal bar)
    adv_pct = criteria.advance_rate * Decimal("100")
    dil_pct = criteria.dilution_reserve_pct * Decimal("100")
    eff_pct = criteria.effective_advance_rate * Decimal("100")
    conc_cap_pct = criteria.max_client_concentration_pct * Decimal("100")

    param_strip_data = [
        [
            Paragraph(
                f"<font color='{theme.muted}'>INVOICE WINDOW</font><br/>"
                f"<b>{start_str} &ndash; {end_str}</b>",
                s_param_val,
            ),
            Paragraph(
                f"<font color='{theme.muted}'>ADVANCE / DILUTION RATE</font><br/>"
                f"<b>{adv_pct:.1f}% &minus; {dil_pct:.1f}% = {eff_pct:.1f}% Eff.</b>",
                s_param_val,
            ),
            Paragraph(
                f"<font color='{theme.muted}'>DEBTOR CONCENTRATION CAP</font><br/>"
                f"<b>{conc_cap_pct:.1f}% of Gross Eligible</b>",
                s_param_val,
            ),
            Paragraph(
                f"<font color='{theme.muted}'>OVERDUE & TAINT COVENANTS</font><br/>"
                f"<b>Max {criteria.max_overdue_days}d &bull; "
                f"Taint >{criteria.taint_past_due_days}d "
                f"({criteria.taint_threshold_pct * 100:.0f}%)</b>",
                s_param_val,
            ),
        ]
    ]
    param_strip = Table(param_strip_data, colWidths=[180, 180, 180, 180])
    param_strip.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), c_bg_subtle),
                ("BOX", (0, 0), (-1, -1), 0.5, c_border),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )

    # 3. COLUMN 1: COLLATERAL AVAILABILITY LEDGER
    net_excess_borrow = max(
        Decimal("0.00"), report.net_available_borrow_cad - params.existing_borrow
    )

    ledger_data = [
        [
            Paragraph("<b>Collateral Line Item</b>", s_bold),
            Paragraph(f"<b>Amount ({params.currency})</b>", s_right_bold),
        ],
        [
            Paragraph("<b>Gross Accounts Receivable Balance</b>", s_bold),
            Paragraph(f"<b>${report.gross_amount_cad:,.2f}</b>", s_right_bold),
        ],
        [
            Paragraph("<i>Less Ineligible Collateral Deductions:</i>", s_sub),
            Paragraph("", s_right),
        ],
        [
            Paragraph("&nbsp;&nbsp;&bull;&nbsp;Contra & Affiliate Accounts", s_body),
            Paragraph(format_deduction(report.ineligible_summary.contra), s_right),
        ],
        [
            Paragraph("&nbsp;&nbsp;&bull;&nbsp;Aged Past Due Over Limit", s_body),
            Paragraph(
                format_deduction(report.ineligible_summary.aged_overdue), s_right
            ),
        ],
        [
            Paragraph("&nbsp;&nbsp;&bull;&nbsp;Cross-Default Tainted Debtors", s_body),
            Paragraph(
                format_deduction(report.ineligible_summary.cross_default), s_right
            ),
        ],
        [
            Paragraph("&nbsp;&nbsp;&bull;&nbsp;Debtor Concentration Excess", s_body),
            Paragraph(
                format_deduction(report.ineligible_summary.concentration_excess),
                (
                    s_right_alert
                    if report.ineligible_summary.concentration_excess > Decimal("0.00")
                    else s_right
                ),
            ),
        ],
        [
            Paragraph("<b>Total Ineligible Collateral</b>", s_bold),
            Paragraph(
                f"<b>{format_deduction(report.ineligible_amount_cad)}</b>",
                (
                    s_right_alert
                    if report.ineligible_amount_cad > Decimal("0.00")
                    else s_right_bold
                ),
            ),
        ],
        [
            Paragraph("<b>Net Eligible Collateral</b>", s_bold),
            Paragraph(f"<b>${report.eligible_amount_cad:,.2f}</b>", s_right_accent),
        ],
        [
            Paragraph("&nbsp;&nbsp;&times;&nbsp;Effective Advance Rate", s_body),
            Paragraph(f"{eff_pct:.2f}%", s_right),
        ],
        [
            Paragraph("<b>Gross Borrowing Base</b>", s_bold),
            Paragraph(f"<b>${report.net_available_borrow_cad:,.2f}</b>", s_right_bold),
        ],
        [
            Paragraph("Less: Current Outstanding Loan Drawn", s_body),
            Paragraph(f"(${params.existing_borrow:,.2f})", s_right),
        ],
        [
            Paragraph("<font size='9'><b>NET EXCESS AVAILABILITY</b></font>", s_bold),
            Paragraph(
                f"<font size='9'><b>${net_excess_borrow:,.2f}</b></font>",
                s_right_accent,
            ),
        ],
    ]

    ledger_table = Table(ledger_data, colWidths=[240, 130])
    ledger_table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, c_primary),
                ("LINEBELOW", (0, 1), (-1, 1), 0.5, c_border),
                ("LINEBELOW", (0, 7), (-1, 7), 0.5, c_primary),
                ("LINEBELOW", (0, 8), (-1, 8), 0.5, c_primary),
                ("LINEBELOW", (0, 10), (-1, 10), 0.5, c_primary),
                ("LINEBELOW", (0, -1), (-1, -1), 1.5, c_accent),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    # Availability Waterfall / Step Bar directly UNDER Ledger
    gross_val = float(report.gross_amount_cad)
    elig_val = float(report.eligible_amount_cad)
    avail_val = float(report.net_available_borrow_cad)
    drawn_val = float(params.existing_borrow)
    max_scale = max(gross_val, float(params.facility_limit), 1.0)

    av_steps = [
        (
            "Gross A/R Balance",
            gross_val,
            f"${gross_val/1e3:,.1f}K",
            colors.HexColor("#334155"),
        ),
        (
            "Net Eligible Collateral",
            elig_val,
            f"${elig_val/1e3:,.1f}K",
            colors.HexColor("#475569"),
        ),
        (
            f"Net Availability ({eff_pct:.0f}%)",
            avail_val,
            f"${avail_val/1e3:,.1f}K",
            c_accent,
        ),
        ("Existing Drawn Loan", drawn_val, f"${drawn_val/1e3:,.1f}K", c_subtle),
    ]

    av_draw = _waterfall_drawing(av_steps, max_scale, ink)

    # 4. COLUMN 2:
    # 4A. ORIGINAL CURRENCY BREAKDOWN (USD / CAD)
    curr_breakdowns = list(
        currency_breakdowns or _single_currency_breakdown(report, params)
    )

    curr_rows = [
        [
            Paragraph("<b>Currency</b>", s_bold),
            Paragraph("<b>Face Amount</b>", s_right_bold),
            Paragraph("<b>FX Rate</b>", s_right_bold),
            Paragraph("<b>Collateral (CAD)</b>", s_right_bold),
            Paragraph("<b>Share</b>", s_right_bold),
        ]
    ]

    curr_rows.extend(
        [
            Paragraph(_currency_label(cb.currency), s_body),
            Paragraph(f"${cb.face_amount:,.2f}", s_right),
            Paragraph(
                "1.0000" if cb.currency == "CAD" else f"{cb.fx_rate:.4f}", s_right
            ),
            Paragraph(f"${cb.cad_amount:,.2f}", s_right),
            Paragraph(f"{cb.share_pct * 100:.1f}%", s_right),
        ]
        for cb in curr_breakdowns
    )
    total_face_cad = sum((cb.cad_amount for cb in curr_breakdowns), Decimal("0.00"))

    curr_rows.append(
        [
            Paragraph("<b>Total Portfolio AR</b>", s_bold),
            Paragraph("-", s_right),
            Paragraph("CAD", s_right_bold),
            Paragraph(f"<b>${total_face_cad:,.2f}</b>", s_right_bold),
            Paragraph("<b>100.0%</b>", s_right_bold),
        ]
    )

    curr_table = Table(curr_rows, colWidths=[95, 75, 45, 80, 45])
    curr_table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, c_primary),
                ("LINEBELOW", (0, 1), (-1, 1), 0.5, c_border),
                ("LINEBELOW", (0, -2), (-1, -2), 0.5, c_primary),
                ("LINEBELOW", (0, -1), (-1, -1), 1.0, c_primary),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("BACKGROUND", (0, 0), (-1, 0), c_bg_subtle),
            ]
        )
    )

    # 4B. DEBTOR CONCENTRATION TABLE & 100% STACKED BAR
    conc_rows = [
        [
            Paragraph("<b>Debtor Name</b>", s_bold),
            Paragraph("<b>Balance (CAD)</b>", s_right_bold),
            Paragraph("<b>% AR</b><super>1</super>", s_right_bold),
            Paragraph("<b>% Elig.</b><super>2</super>", s_right_bold),
            Paragraph("<b>Status</b>", s_right_bold),
        ]
    ]

    display_list = _display_concentrations(concentrations, report.gross_amount_cad)
    conc_rows.extend(
        [
            Paragraph(f"&bull;&nbsp;{item.client_name}", s_body),
            Paragraph(f"${item.balance_cad:,.0f}", s_right),
            Paragraph(f"{item.share_pct * 100:.1f}%", s_right),
            Paragraph(
                f"{item.eligible_share_pct * 100:.1f}%",
                s_right_alert if item.is_capped else s_right,
            ),
            Paragraph(
                (
                    f"<font color='{theme.alert}'><b>EXCESS</b></font>"
                    if item.is_capped
                    else f"<font color='{theme.accent}'>Eligible</font>"
                ),
                s_right,
            ),
        ]
        for item in display_list
    )

    conc_rows.append(
        [
            Paragraph("<b>Total Pledged Portfolio</b>", s_bold),
            Paragraph(f"<b>${report.gross_amount_cad:,.0f}</b>", s_right_bold),
            Paragraph("<b>100.0%</b>", s_right_bold),
            Paragraph("<b>100.0%</b>", s_right_bold),
            Paragraph(f"<b>{conc_cap_pct:.0f}% Cap</b>", s_right_bold),
        ]
    )

    conc_table = Table(conc_rows, colWidths=[110, 75, 50, 55, 50])
    conc_table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, c_primary),
                ("LINEBELOW", (0, 1), (-1, 1), 0.5, c_border),
                ("LINEBELOW", (0, -2), (-1, -2), 0.5, c_primary),
                ("LINEBELOW", (0, -1), (-1, -1), 1.0, c_primary),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )

    # Sorted Stacked 100% Horizontal Bar with Concentration Cap marker
    bar_list = _display_concentrations(
        sorted(
            (c for c in concentrations if c.eligible_share_pct > Decimal("0")),
            key=lambda c: c.eligible_share_pct,
            reverse=True,
        ),
        report.gross_amount_cad,
    )
    ar_bar_draw = _concentration_bar_drawing(
        display_list,
        lambda c: c.share_pct,
        "DEBTOR CONCENTRATION (% OF GROSS AR, 100% STACKED)",
        "100% Gross AR",
        debtor_hex_colors,
        ink,
    )
    conc_bar_draw = _concentration_bar_drawing(
        bar_list,
        lambda c: c.eligible_share_pct,
        "DEBTOR CONCENTRATION (% OF GROSS ELIGIBLE, 100% STACKED)",
        "100% Gross Eligible",
        debtor_hex_colors,
        ink,
    )
    # Ineligibility Breakdown Bars
    inelig_items = [
        (
            "Debtor Concentration Excess",
            float(report.ineligible_summary.concentration_excess),
            c_alert,
        ),
        (
            "Cross-Default Tainted Debtors",
            float(report.ineligible_summary.cross_default),
            c_subtle,
        ),
        (
            "Aged Past Due Over Limit",
            float(report.ineligible_summary.aged_overdue),
            c_subtle,
        ),
        (
            "Contra & Affiliate Accounts",
            float(report.ineligible_summary.contra),
            c_subtle,
        ),
    ]
    max_inelig = max(
        [val for _, val, _ in inelig_items] + [float(report.ineligible_amount_cad), 1.0]
    )
    inelig_draw = _ineligibility_drawing(inelig_items, max_inelig, gross_val, ink)

    # Assemble 2 Main Columns
    col_left_flow = [
        Paragraph("<b>1. COLLATERAL AVAILABILITY LEDGER</b>", s_h2),
        ledger_table,
        Spacer(1, 4),
        av_draw,
    ]
    col_right_flow = [
        Paragraph("<b>2. ORIGINAL CURRENCY BREAKDOWN (USD / CAD)</b>", s_h2),
        curr_table,
        Spacer(1, 4),
        Paragraph("<b>3. DEBTOR AR CONCENTRATION</b>", s_h2),
        conc_table,
        Spacer(1, 2),
        ar_bar_draw,
        conc_bar_draw,
        Spacer(1, 4),
        inelig_draw,
    ]

    main_grid = Table([[col_left_flow, col_right_flow]], colWidths=[375, 345])
    main_grid.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("RIGHTPADDING", (0, 0), (0, 0), 12),
                ("LEFTPADDING", (1, 0), (1, 0), 6),
            ]
        )
    )

    # 5. OFFICER CERTIFICATION & COMPLIANCE SIGN-OFF BLOCK
    sign_block = []
    if params.status == ReportStatus.CERTIFIED:
        s_cert = ParagraphStyle(
            "CertText",
            parent=s_body,
            fontSize=6.5,
            leading=8.5,
            textColor=c_primary,
        )
        cert_data = [
            [
                Paragraph(
                    "<b>BORROWER COMPLIANCE CERTIFICATION:</b> The undersigned "
                    "Authorized Officer of the Borrower hereby certifies that the "
                    "collateral valuations, ineligibility calculations, and borrowing "
                    "base computations set forth herein are true, correct, and "
                    "calculated in accordance with the definitions, advance rates, "
                    "and covenant ratios of the Credit Facility Agreement.",
                    s_cert,
                ),
            ],
            [
                Paragraph(
                    "<b>Authorized Officer:</b> ___________________________"
                    "&nbsp;&nbsp;&nbsp;&nbsp;"
                    "<b>Title:</b> Chief Financial Officer / Treasurer"
                    "&nbsp;&nbsp;&nbsp;&nbsp;"
                    "<b>Signature Date:</b> ___________________________",
                    s_cert,
                )
            ],
        ]
        cert_table = Table(cert_data, colWidths=[720])
        cert_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), c_bg_subtle),
                    ("BOX", (0, 0), (-1, -1), 0.5, c_border),
                    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        sign_block = [cert_table, Spacer(1, 4)]

    # 6. FOOTER SECTION WITH RED SMALL-CAPS DISCLAIMER
    s_disclaimer = ParagraphStyle(
        "Disclaimer",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=6.5,
        leading=8.5,
        alignment=1,
        textColor=c_alert,
    )

    footer_data = [
        [
            Paragraph(
                f"<b>STATUS: {params.status.value.replace('_', '-')}</b> "
                f"&mdash; {status_sub}.",
                ParagraphStyle("FootL", parent=s_body, fontSize=6.5, textColor=c_muted),
            ),
            Paragraph(
                "Vertiqal Studios ABL Reporter &bull; Page 1 of 1",
                ParagraphStyle(
                    "FootR", parent=s_body, fontSize=6.5, alignment=2, textColor=c_muted
                ),
            ),
        ],
        [
            Paragraph(params.disclaimer.upper(), s_disclaimer),
            Paragraph("", s_disclaimer),
        ],
    ]
    footer_table = Table(footer_data, colWidths=[520, 200])
    footer_table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, c_border),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("SPAN", (0, 1), (1, 1)),
            ]
        )
    )

    doc_elements = [
        header_table,
        Spacer(1, 4),
        param_strip,
        Spacer(1, 6),
        main_grid,
        Spacer(1, 4),
    ]
    doc_elements.extend(sign_block)
    s_note = ParagraphStyle(
        "Note", parent=s_body, fontSize=6, leading=7.5, textColor=c_muted
    )
    doc_elements.extend(_definition_paragraphs(s_note))
    doc_elements.append(footer_table)

    doc.build(doc_elements)
    return path


def _invoice_range(invoices: Sequence[Invoice], as_of: date) -> tuple[date, date]:
    """Returns the first and last invoice dates on or before `as_of`."""
    issued = [inv.invoice_date for inv in invoices if inv.invoice_date <= as_of]
    if not issued:
        raise ValueError(f"No invoices dated on or before as-of date {as_of}")
    return min(issued), max(issued)


def generate_certificate(
    settings: ReportSettings | None = None,
    invoices: Sequence[Invoice] | None = None,
) -> Path:
    """Evaluates collateral and generates the Borrowing Base Certificate PDF."""
    cfg = settings if settings is not None else ReportSettings()

    if invoices is None:
        invoices = load_invoices_from_csv(cfg.input_source)

    if not invoices:
        raise ValueError(
            f"No invoices available to generate certificate from: {cfg.input_source}"
        )

    as_of = cfg.resolve_as_of()
    fx = cfg.fx if cfg.fx is not None else make_fixed_fx_provider()

    report = generate_bbc(invoices, as_of, cfg.criteria, fx)
    concentrations = calculate_debtor_concentrations(
        invoices,
        as_of,
        fx,
        capped_clients=capped_client_names(report),
        eligible_balances=pre_cap_eligible_by_client(report),
    )
    currency_breakdowns = calculate_currency_breakdown(invoices, as_of, fx)

    invoice_start, invoice_end = _invoice_range(invoices, as_of)

    output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "bbc_certificate.pdf"

    return generate_certificate_pdf(
        report=report,
        criteria=cfg.criteria,
        params=cfg.certificate,
        concentrations=concentrations,
        currency_breakdowns=currency_breakdowns,
        invoice_start=invoice_start,
        invoice_end=invoice_end,
        palette=cfg.palette,
        output_path=output_file,
    )
