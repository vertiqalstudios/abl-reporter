from datetime import date
from decimal import Decimal
import io
from pathlib import Path
import pytest

from abl.bbc import generate_bbc
from abl.certificate import calculate_debtor_concentrations, generate_certificate_pdf
from abl.fx import make_fixed_fx_provider
from abl.models import (
    CertificateParams,
    Invoice,
    LendingCriteria,
    ReportPalette,
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


def test_calculate_debtor_concentrations_empty(fixed_fx) -> None:
    concentrations = calculate_debtor_concentrations([], date(2026, 2, 1), fixed_fx)
    assert concentrations == []


def test_calculate_debtor_concentrations_sorting_and_capping(sample_invoices, fixed_fx) -> None:
    # Total = 100k + 50k + 25k + 25k = 200k.
    # Alpha = 50% (>30% cap) -> is_capped is True
    # Beta = 25% (<=30% cap) -> is_capped is False
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


@pytest.mark.parametrize("status", [ReportStatus.PRO_FORMA, ReportStatus.DRAFT, ReportStatus.CERTIFIED])
def test_generate_certificate_pdf_status_variants(
    tmp_path: Path, sample_invoices, default_criteria, fixed_fx, status: ReportStatus
) -> None:
    as_of = date(2026, 2, 1)
    report = generate_bbc(sample_invoices, as_of, default_criteria, fixed_fx)
    concentrations = calculate_debtor_concentrations(sample_invoices, as_of, fixed_fx)

    params = CertificateParams(
        as_of=as_of,
        status=status,
        existing_borrow=Decimal("20000.00"),
        disclaimer="Confidential Test Disclaimer",
    )

    pdf_out = tmp_path / f"test_cert_{status.value}.pdf"
    result_path = generate_certificate_pdf(
        report=report,
        criteria=default_criteria,
        params=params,
        concentrations=concentrations,
        output_path=pdf_out,
    )

    assert result_path.exists()
    assert result_path.stat().st_size > 1000  # Valid non-empty PDF file


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
