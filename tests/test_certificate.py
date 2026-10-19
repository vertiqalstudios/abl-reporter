from datetime import date
from decimal import Decimal
import io
from pathlib import Path
import pytest

from abl.bbc import generate_bbc
from abl.certificate import (
    calculate_currency_breakdown,
    calculate_debtor_concentrations,
    generate_certificate,
    generate_certificate_pdf,
)
from abl.fx import make_fixed_fx_provider
from abl.models import (
    CertificateParams,
    CurrencyBreakdown,
    Invoice,
    LendingCriteria,
    ReportPalette,
    ReportSettings,
    ReportStatus,
)


@pytest.fixture
def fixed_fx():
    return make_fixed_fx_provider(usd_cad_rate=Decimal("1.40"))


@pytest.fixture
def default_criteria():
    return LendingCriteria(
        advance_rate=Decimal("0.85"),
        dilution_reserve_pct=Decimal("0.05"),
        max_client_concentration_pct=Decimal("0.30"),
        max_overdue_days=180,
        taint_past_due_days=90,
        taint_threshold_pct=Decimal("0.50"),
        contra_clients=frozenset({"self"}),
    )


@pytest.fixture
def sample_invoices():
    return [
        Invoice("Alpha Corp", date(2026, 1, 10), date(2026, 2, 10), Decimal("100000.00"), "CAD"),
        Invoice("Beta Ltd", date(2026, 1, 15), date(2026, 2, 15), Decimal("50000.00"), "CAD"),
        Invoice("Gamma LLC", date(2026, 1, 20), date(2026, 2, 20), Decimal("25000.00"), "CAD"),
        Invoice("Delta Inc", date(2026, 1, 25), date(2026, 2, 25), Decimal("25000.00"), "CAD"),
    ]


@pytest.fixture
def multi_currency_invoices():
    return [
        Invoice("Alpha Corp", date(2026, 1, 10), date(2026, 2, 10), Decimal("100000.00"), "CAD"),
        Invoice("US Client Inc", date(2026, 1, 15), date(2026, 2, 15), Decimal("50000.00"), "USD"),
        Invoice("Beta Ltd", date(2026, 1, 18), date(2026, 2, 18), Decimal("25000.00"), "CAD"),
        Invoice("US Tech LLC", date(2026, 1, 20), date(2026, 2, 20), Decimal("20000.00"), "USD"),
    ]


def test_calculate_debtor_concentrations_empty(fixed_fx) -> None:
    concentrations = calculate_debtor_concentrations([], date(2026, 2, 1), fixed_fx)
    assert concentrations == []


def test_calculate_debtor_concentrations_sorting_and_capping(sample_invoices, fixed_fx) -> None:
    concentrations = calculate_debtor_concentrations(
        sample_invoices, date(2026, 2, 1), fixed_fx, cap_pct=Decimal("0.30")
    )
    assert len(concentrations) == 4
    assert concentrations[0].client_name == "Alpha Corp"
    assert concentrations[0].balance_cad == Decimal("100000.00")
    assert concentrations[0].share_pct == Decimal("0.5000")
    assert concentrations[0].is_capped is True

    assert concentrations[1].client_name == "Beta Ltd"
    assert concentrations[1].share_pct == Decimal("0.2500")
    assert concentrations[1].is_capped is False


def test_calculate_currency_breakdown_empty(fixed_fx) -> None:
    breakdowns = calculate_currency_breakdown([], date(2026, 2, 1), fixed_fx)
    assert breakdowns == []


def test_calculate_currency_breakdown_multi_currency(multi_currency_invoices, fixed_fx) -> None:
    # CAD: 100k + 25k = 125k face = 125k CAD
    # USD: 50k + 20k = 70k face * 1.40 = 98k CAD
    # Total Gross CAD = 223,000 CAD
    as_of = date(2026, 2, 1)
    breakdowns = calculate_currency_breakdown(multi_currency_invoices, as_of, fixed_fx)
    assert len(breakdowns) == 2

    cad_entry = next(b for b in breakdowns if b.currency == "CAD")
    usd_entry = next(b for b in breakdowns if b.currency == "USD")

    assert cad_entry.face_amount == Decimal("125000.00")
    assert cad_entry.cad_amount == Decimal("125000.00")
    assert cad_entry.fx_rate == Decimal("1.0000")
    assert cad_entry.share_pct == Decimal("0.5605")

    assert usd_entry.face_amount == Decimal("70000.00")
    assert usd_entry.cad_amount == Decimal("98000.00")
    assert usd_entry.fx_rate == Decimal("1.4000")
    assert usd_entry.share_pct == Decimal("0.4395")


@pytest.mark.parametrize("status", [ReportStatus.PRO_FORMA, ReportStatus.DRAFT, ReportStatus.CERTIFIED])
def test_generate_certificate_pdf_status_variants(
    tmp_path: Path, multi_currency_invoices, default_criteria, fixed_fx, status: ReportStatus
) -> None:
    as_of = date(2026, 2, 1)
    report = generate_bbc(multi_currency_invoices, as_of, default_criteria, fixed_fx)
    concentrations = calculate_debtor_concentrations(multi_currency_invoices, as_of, fixed_fx)
    currency_breakdowns = calculate_currency_breakdown(multi_currency_invoices, as_of, fixed_fx)

    params = CertificateParams(
        as_of=as_of,
        status=status,
        existing_borrow=Decimal("20000.00"),
        disclaimer="Confidential Bank Workpaper",
    )

    pdf_out = tmp_path / f"test_cert_{status.value}.pdf"
    result_path = generate_certificate_pdf(
        report=report,
        criteria=default_criteria,
        params=params,
        concentrations=concentrations,
        currency_breakdowns=currency_breakdowns,
        output_path=pdf_out,
    )

    assert result_path.exists()
    assert result_path.stat().st_size > 1000


def test_generate_certificate_pdf_debtor_rollup(
    tmp_path: Path, default_criteria, fixed_fx
) -> None:
    # 8 distinct debtors - Top 5 should be preserved, rest rolled into Other Accounts
    invoices = [
        Invoice(f"Client {i}", date(2026, 1, 10), date(2026, 2, 10), Decimal(f"{10000 * i}.00"), "CAD")
        for i in range(1, 9)
    ]
    as_of = date(2026, 2, 1)
    report = generate_bbc(invoices, as_of, default_criteria, fixed_fx)
    concentrations = calculate_debtor_concentrations(invoices, as_of, fixed_fx)
    currency_breakdowns = calculate_currency_breakdown(invoices, as_of, fixed_fx)

    params = CertificateParams(as_of=as_of, status=ReportStatus.CERTIFIED)
    pdf_out = tmp_path / "test_cert_rollup.pdf"

    result_path = generate_certificate_pdf(
        report=report,
        criteria=default_criteria,
        params=params,
        concentrations=concentrations,
        currency_breakdowns=currency_breakdowns,
        output_path=pdf_out,
    )

    assert result_path.exists()
    assert result_path.stat().st_size > 1000


def test_generate_certificate_pdf_custom_palette(
    tmp_path: Path, sample_invoices, default_criteria, fixed_fx
) -> None:
    as_of = date(2026, 2, 1)
    report = generate_bbc(sample_invoices, as_of, default_criteria, fixed_fx)
    concentrations = calculate_debtor_concentrations(sample_invoices, as_of, fixed_fx)

    custom_palette = ReportPalette(
        primary="#1E293B",
        accent="#2563EB",
        alert="#DC2626",
    )

    params = CertificateParams(as_of=as_of, status=ReportStatus.CERTIFIED)
    pdf_out = tmp_path / "custom_palette_cert.pdf"

    result_path = generate_certificate_pdf(
        report=report,
        criteria=default_criteria,
        params=params,
        concentrations=concentrations,
        palette=custom_palette,
        output_path=pdf_out,
    )

    assert result_path.exists()
    assert result_path.stat().st_size > 1000


def test_report_settings_defaults() -> None:
    settings = ReportSettings()
    assert settings.criteria.advance_rate == Decimal("0.85")
    assert settings.palette.font_family == "Helvetica Neue"
    assert settings.certificate.status == ReportStatus.PRO_FORMA
    assert settings.certificate.currency == "CAD"
    assert settings.input_source == Path("invoices.csv")
    assert settings.output_dir == Path("output")


def test_generate_certificate_from_settings_with_textio(
    tmp_path: Path, default_criteria, fixed_fx
) -> None:
    csv_stream = io.StringIO(
        "Client Name,Invoice Date,Due Date,Invoice Amount,Currency,Invoice Paid Date\n"
        "Alpha Corp,2026-01-10,2026-02-10,100000.00,CAD,\n"
        "US Client Inc,2026-01-15,2026-02-15,50000.00,USD,\n"
    )
    settings = ReportSettings(
        criteria=default_criteria,
        fx=fixed_fx,
        certificate=CertificateParams(
            as_of=date(2026, 2, 1),
            status=ReportStatus.CERTIFIED,
        ),
        input_source=csv_stream,
        output_dir=tmp_path,
    )

    pdf_out = generate_certificate(settings)
    assert pdf_out.exists()
    assert pdf_out.parent == tmp_path
    assert pdf_out.name == "bbc_certificate.pdf"
    assert pdf_out.stat().st_size > 1000


def test_generate_certificate_from_settings_with_invoices(
    tmp_path: Path, sample_invoices, default_criteria, fixed_fx
) -> None:
    settings = ReportSettings(
        criteria=default_criteria,
        fx=fixed_fx,
        certificate=CertificateParams(
            as_of=date(2026, 2, 1),
            status=ReportStatus.DRAFT,
        ),
        output_dir=tmp_path,
    )

    pdf_out = generate_certificate(settings, invoices=sample_invoices)
    assert pdf_out.exists()
    assert pdf_out.stat().st_size > 1000

