"""Borrowing Base Certificate PDF report generation using ReportLab."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import os
from pathlib import Path
import warnings

warnings.filterwarnings(
    "ignore", category=RuntimeWarning, message=".*'abl.certificate'.*"
)

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
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
    Invoice,
    LendingCriteria,
    ReportPalette,
    ReportSettings,
    ReportStatus,
)
from .rules import is_invoice_open


def _resolve_fonts() -> tuple[str, str, str]:
    """Resolves Helvetica Neue font family with fallback to core Helvetica."""
    regular_name = "Helvetica"
    bold_name = "Helvetica-Bold"
    oblique_name = "Helvetica-Oblique"

    candidate_dirs = [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path("~/Library/Fonts").expanduser(),
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path("~/.fonts").expanduser(),
    ]

    for d in candidate_dirs:
        if not d.exists():
            continue
        # Check for Helvetica Neue variations
        reg_candidates = list(d.rglob("*HelveticaNeue*.ttf")) + list(
            d.rglob("*HelveticaNeue*.otf")
        )
        bold_candidates = list(d.rglob("*HelveticaNeue*Bold*.ttf")) + list(
            d.rglob("*HelveticaNeue*Bold*.otf")
        )
        oblique_candidates = list(d.rglob("*HelveticaNeue*Italic*.ttf")) + list(
            d.rglob("*HelveticaNeue*Italic*.otf")
        )

        if reg_candidates:
            try:
                if "HelveticaNeue" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(
                        TTFont("HelveticaNeue", str(reg_candidates[0]))
                    )
                regular_name = "HelveticaNeue"
            except Exception:
                pass

        if bold_candidates:
            try:
                if "HelveticaNeue-Bold" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(
                        TTFont("HelveticaNeue-Bold", str(bold_candidates[0]))
                    )
                bold_name = "HelveticaNeue-Bold"
            except Exception:
                pass

        if oblique_candidates:
            try:
                if "HelveticaNeue-Italic" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(
                        TTFont("HelveticaNeue-Italic", str(oblique_candidates[0]))
                    )
                oblique_name = "HelveticaNeue-Italic"
            except Exception:
                pass

        if regular_name.startswith("HelveticaNeue"):
            break

    return regular_name, bold_name, oblique_name


def calculate_currency_breakdown(
    invoices: Sequence[Invoice],
    as_of: date,
    fx: FXProvider,
) -> list[CurrencyBreakdown]:
    """Aggregates uncollected invoice balances by transaction currency and computes CAD equivalent share."""
    open_invoices = [inv for inv in invoices if is_invoice_open(inv, as_of)]
    if not open_invoices:
        return []

    currency_totals: dict[str, Decimal] = defaultdict(Decimal)
    for inv in open_invoices:
        currency_totals[inv.currency.upper()] += inv.invoice_amount

    breakdowns: list[CurrencyBreakdown] = []
    total_cad = Decimal("0.00")
    temp_results: list[tuple[str, Decimal, Decimal, Decimal]] = []

    for curr, face in currency_totals.items():
        if curr == "CAD":
            rate = Decimal("1.0000")
            cad_amt = face
        else:
            rate = fx(curr, "CAD", as_of)
            cad_amt = (face * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total_cad += cad_amt
        temp_results.append((curr, face, rate, cad_amt))

    for curr, face, rate, cad_amt in temp_results:
        share = (
            (cad_amt / total_cad).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if total_cad > Decimal("0.00")
            else Decimal("0.0000")
        )
        breakdowns.append(
            CurrencyBreakdown(
                currency=curr,
                face_amount=face,
                fx_rate=rate,
                cad_amount=cad_amt,
                share_pct=share,
            )
        )

    def sort_key(b: CurrencyBreakdown) -> tuple[int, str]:
        if b.currency == "CAD":
            return (0, b.currency)
        if b.currency == "USD":
            return (1, b.currency)
        return (2, b.currency)

    breakdowns.sort(key=sort_key)
    return breakdowns


def calculate_debtor_concentrations(
    invoices: Sequence[Invoice],
    as_of: date,
    fx: FXProvider,
    cap_pct: Decimal = Decimal("0.30"),
) -> list[DebtorConcentration]:
    """Calculates open AR balance and portfolio share per debtor, identifying concentration threshold breaches."""
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

    concentrations: list[DebtorConcentration] = []
    for client_name, balance in sorted_clients:
        share = (balance / gross_total).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        is_capped = share > cap_pct
        concentrations.append(
            DebtorConcentration(
                client_name=client_name,
                balance_cad=balance,
                share_pct=share,
                is_capped=is_capped,
            )
        )
    return concentrations


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
    """Builds a single-page landscape PDF certificate complying with Tufte financial layout standards."""

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
    start_str = invoice_start.isoformat() if invoice_start is not None else "Historical"
    end_str = (
        invoice_end.isoformat() if invoice_end is not None else params.as_of.isoformat()
    )
    gen_time_str = params.generated_at.strftime("%Y-%m-%d %H:%M UTC")

    header_data = [
        [
            Paragraph(
                "<b>VERTIQAL STUDIOS</b><br/><font size='13'><b>Borrowing Base Certificate</b></font>",
                s_title,
            ),
            Paragraph(
                f"<b>As of Date:</b> {params.as_of.isoformat()} &bull; <b>Run Date:</b> {gen_time_str}<br/>"
                f"<b>Credit Facility Limit:</b> ${params.facility_limit:,.2f} {params.currency} &bull; <b>Base Currency:</b> {params.currency}",
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
                f"<font color='{theme.muted}'>INVOICE WINDOW</font><br/><b>{start_str} &ndash; {end_str}</b>",
                s_param_val,
            ),
            Paragraph(
                f"<font color='{theme.muted}'>ADVANCE / DILUTION RATE</font><br/><b>{adv_pct:.1f}% &minus; {dil_pct:.1f}% = {eff_pct:.1f}% Eff.</b>",
                s_param_val,
            ),
            Paragraph(
                f"<font color='{theme.muted}'>DEBTOR CONCENTRATION CAP</font><br/><b>{conc_cap_pct:.1f}% of Gross Eligible</b>",
                s_param_val,
            ),
            Paragraph(
                f"<font color='{theme.muted}'>OVERDUE & TAINT COVENANTS</font><br/><b>Max {criteria.max_overdue_days}d &bull; Taint >{criteria.taint_past_due_days}d ({criteria.taint_threshold_pct * 100:.0f}%)</b>",
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
            Paragraph(f"${report.ineligible_summary.contra:,.2f}", s_right),
        ],
        [
            Paragraph("&nbsp;&nbsp;&bull;&nbsp;Aged Past Due Over Limit", s_body),
            Paragraph(f"${report.ineligible_summary.aged_overdue:,.2f}", s_right),
        ],
        [
            Paragraph("&nbsp;&nbsp;&bull;&nbsp;Cross-Default Tainted Debtors", s_body),
            Paragraph(f"${report.ineligible_summary.cross_default:,.2f}", s_right),
        ],
        [
            Paragraph("&nbsp;&nbsp;&bull;&nbsp;Debtor Concentration Excess", s_body),
            Paragraph(
                (
                    f"(${report.ineligible_summary.concentration_excess:,.2f})"
                    if report.ineligible_summary.concentration_excess > Decimal("0.00")
                    else "$0.00"
                ),
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
                (
                    f"<b>(${report.ineligible_amount_cad:,.2f})</b>"
                    if report.ineligible_amount_cad > Decimal("0.00")
                    else "<b>$0.00</b>"
                ),
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
    av_draw = Drawing(370, 70)
    av_draw.add(
        String(
            0,
            58,
            "CAPITAL AVAILABILITY WATERFALL",
            fontName=font_bold,
            fontSize=7,
            fillColor=c_primary,
        )
    )

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

    y_av = 42
    for title, val, txt, col in av_steps:
        w = max(2, int((val / max_scale) * 215)) if val > 0 else 2
        av_draw.add(
            String(
                0,
                y_av + 1,
                title,
                fontName=font_reg,
                fontSize=6.5,
                fillColor=c_primary,
            )
        )
        av_draw.add(Rect(115, y_av, w, 6.5, fillColor=col, strokeColor=None))
        av_draw.add(
            String(
                115 + w + 5,
                y_av + 1,
                txt,
                fontName=font_bold,
                fontSize=6.5,
                fillColor=col,
            )
        )
        y_av -= 13

    # 4. COLUMN 2:
    # 4A. ORIGINAL CURRENCY BREAKDOWN (USD / CAD)
    curr_breakdowns = list(currency_breakdowns) if currency_breakdowns else []
    if not curr_breakdowns:
        # Synthesize from Gross AR if no explicit breakdown passed
        curr_breakdowns = [
            CurrencyBreakdown(
                currency=params.currency,
                face_amount=report.gross_amount_cad,
                fx_rate=Decimal("1.0000"),
                cad_amount=report.gross_amount_cad,
                share_pct=Decimal("1.0000"),
            )
        ]

    curr_rows = [
        [
            Paragraph("<b>Currency</b>", s_bold),
            Paragraph("<b>Face Amount</b>", s_right_bold),
            Paragraph("<b>FX Rate</b>", s_right_bold),
            Paragraph("<b>Collateral (CAD)</b>", s_right_bold),
            Paragraph("<b>Share</b>", s_right_bold),
        ]
    ]

    total_face_cad = Decimal("0.00")
    for cb in curr_breakdowns:
        curr_label = (
            f"<b>{cb.currency}</b> (Domestic)"
            if cb.currency == "CAD"
            else (
                f"<b>{cb.currency}</b> (US Dollar)"
                if cb.currency == "USD"
                else f"<b>{cb.currency}</b>"
            )
        )
        rate_str = "1.0000" if cb.currency == "CAD" else f"{cb.fx_rate:.4f}"
        curr_rows.append(
            [
                Paragraph(curr_label, s_body),
                Paragraph(f"${cb.face_amount:,.2f}", s_right),
                Paragraph(rate_str, s_right),
                Paragraph(f"${cb.cad_amount:,.2f}", s_right),
                Paragraph(f"{cb.share_pct * 100:.1f}%", s_right),
            ]
        )
        total_face_cad += cb.cad_amount

    curr_rows.append(
        [
            Paragraph("<b>Total Portfolio AR</b>", s_bold),
            Paragraph("-", s_right),
            Paragraph("Base CAD", s_right_bold),
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
            Paragraph("<b>Share</b>", s_right_bold),
            Paragraph("<b>Status</b>", s_right_bold),
        ]
    ]

    top_debtors = list(concentrations[:5])
    other_debtors = list(concentrations[5:])
    if other_debtors:
        other_balance = sum((d.balance_cad for d in other_debtors), Decimal("0.00"))
        gross_c = report.gross_amount_cad
        other_share = (
            (other_balance / gross_c).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            if gross_c > Decimal("0.00")
            else Decimal("0.00")
        )
    else:
        other_balance = Decimal("0.00")
        other_share = Decimal("0.00")

    display_list = list(top_debtors)
    if other_debtors:
        display_list.append(
            DebtorConcentration(
                client_name=f"Other Accounts ({len(other_debtors)})",
                balance_cad=other_balance,
                share_pct=other_share,
                is_capped=False,
            )
        )

    for item in display_list:
        status_html = (
            f"<font color='{theme.alert}'><b>EXCESS</b></font>"
            if item.is_capped
            else f"<font color='{theme.accent}'>Eligible</font>"
        )
        conc_rows.append(
            [
                Paragraph(f"&bull;&nbsp;{item.client_name}", s_body),
                Paragraph(f"${item.balance_cad:,.0f}", s_right),
                Paragraph(
                    f"{item.share_pct * 100:.1f}%",
                    s_right_alert if item.is_capped else s_right,
                ),
                Paragraph(status_html, s_right),
            ]
        )

    conc_rows.append(
        [
            Paragraph("<b>Total Pledged Portfolio</b>", s_bold),
            Paragraph(f"<b>${report.gross_amount_cad:,.0f}</b>", s_right_bold),
            Paragraph("<b>100.0%</b>", s_right_bold),
            Paragraph(f"<b>{conc_cap_pct:.0f}% Cap</b>", s_right_bold),
        ]
    )

    conc_table = Table(conc_rows, colWidths=[130, 80, 60, 70])
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
    conc_bar_draw = Drawing(340, 40)
    conc_bar_draw.add(
        String(
            0,
            30,
            "DEBTOR CONCENTRATION DISTRIBUTION (100% STACKED)",
            fontName=font_bold,
            fontSize=6.5,
            fillColor=c_primary,
        )
    )

    bar_w_total = 310
    bar_x = 0
    bar_y = 12
    bar_h = 11

    curr_x = bar_x
    for i, item in enumerate(display_list):
        pct = float(item.share_pct)
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
                    fontName=font_bold,
                    fontSize=6,
                    fillColor=colors.white,
                )
            )
        curr_x += w_seg

    # Cap Threshold Vertical Guideline
    cap_ratio = float(criteria.max_client_concentration_pct)
    cap_x = bar_x + int(cap_ratio * bar_w_total)
    conc_bar_draw.add(
        Line(
            cap_x,
            bar_y - 3,
            cap_x,
            bar_y + bar_h + 3,
            strokeColor=c_alert,
            strokeWidth=1.5,
        )
    )
    conc_bar_draw.add(
        String(
            cap_x - 14,
            bar_y - 10,
            f"{cap_ratio*100:.0f}% Cap",
            fontName=font_bold,
            fontSize=6,
            fillColor=c_alert,
        )
    )
    conc_bar_draw.add(
        String(
            bar_w_total - 60,
            bar_y - 10,
            "100% Total AR",
            fontName=font_reg,
            fontSize=6,
            fillColor=c_muted,
        )
    )

    # Ineligibility Breakdown Bars
    inelig_draw = Drawing(340, 58)
    inelig_draw.add(
        String(
            0,
            48,
            "INELIGIBILITY BREAKDOWN BY REASON",
            fontName=font_bold,
            fontSize=7,
            fillColor=c_primary,
        )
    )

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
    y_in = 34
    for name, val, bar_color in inelig_items:
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
                fontName=font_reg,
                fontSize=6.5,
                fillColor=c_primary,
            )
        )
        inelig_draw.add(
            Rect(
                135,
                y_in,
                110,
                6,
                fillColor=c_bg_subtle,
                strokeColor=c_border,
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
                fontName=font_bold if val > 0 else font_reg,
                fontSize=6,
                fillColor=bar_color if val > 0 else c_muted,
            )
        )
        y_in -= 11

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
                    "<b>BORROWER COMPLIANCE CERTIFICATION:</b> The undersigned Authorized Officer of the Borrower hereby certifies that "
                    "the collateral valuations, ineligibility calculations, and borrowing base computations set forth herein are true, correct, "
                    "and calculated in accordance with the definitions, advance rates, and covenant ratios of the Credit Facility Agreement.",
                    s_cert,
                ),
            ],
            [
                Paragraph(
                    "<b>Authorized Officer:</b> ___________________________&nbsp;&nbsp;&nbsp;&nbsp;"
                    "<b>Title:</b> Chief Financial Officer / Treasurer&nbsp;&nbsp;&nbsp;&nbsp;"
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
                f"<b>STATUS: {params.status.value.replace('_', '-')}</b> &mdash; {status_sub}.",
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
    doc_elements.append(footer_table)

    doc.build(doc_elements)
    return path


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

    as_of = cfg.certificate.as_of or max(inv.invoice_date for inv in invoices)
    fx = cfg.fx if cfg.fx is not None else make_fixed_fx_provider()

    params = (
        cfg.certificate
        if cfg.certificate.as_of is not None
        else CertificateParams(
            as_of=as_of,
            status=cfg.certificate.status,
            existing_borrow=cfg.certificate.existing_borrow,
            facility_limit=cfg.certificate.facility_limit,
            currency=cfg.certificate.currency,
            disclaimer=cfg.certificate.disclaimer,
            generated_at=cfg.certificate.generated_at,
        )
    )

    report = generate_bbc(invoices, as_of, cfg.criteria, fx)
    concentrations = calculate_debtor_concentrations(
        invoices, as_of, fx, cap_pct=cfg.criteria.max_client_concentration_pct
    )
    currency_breakdowns = calculate_currency_breakdown(invoices, as_of, fx)

    invoice_start = min(inv.invoice_date for inv in invoices)
    invoice_end = max(inv.invoice_date for inv in invoices)

    output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "bbc_certificate.pdf"

    return generate_certificate_pdf(
        report=report,
        criteria=cfg.criteria,
        params=params,
        concentrations=concentrations,
        currency_breakdowns=currency_breakdowns,
        invoice_start=invoice_start,
        invoice_end=invoice_end,
        palette=cfg.palette,
        output_path=output_file,
    )


def main() -> None:
    """Executes Borrowing Base Certificate generation using default ReportSettings."""
    pdf_out = generate_certificate()
    print(f"Borrowing Base Certificate generated: {pdf_out}")


if __name__ == "__main__":
    main()
