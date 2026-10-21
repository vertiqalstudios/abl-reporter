from datetime import date
from decimal import Decimal

import pytest

from abl.models import (
    CustomerPalette,
    LendingCriteria,
    ReportPalette,
    ReportSettings,
    end_of_week,
)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 19), date(2026, 10, 23)),  # Monday
        (date(2026, 10, 20), date(2026, 10, 23)),  # Tuesday
        (date(2026, 10, 23), date(2026, 10, 23)),  # Friday
        (date(2026, 10, 24), date(2026, 10, 23)),  # Saturday
        (date(2026, 10, 25), date(2026, 10, 23)),  # Sunday
    ],
)
def test_end_of_week_is_friday_of_the_same_week(day: date, expected: date) -> None:
    assert end_of_week(day) == expected


def test_resolve_as_of_defaults_to_end_of_current_week() -> None:
    assert ReportSettings().resolve_as_of(today=date(2026, 10, 20)) == date(
        2026, 10, 23
    )


def test_resolve_as_of_prefers_explicit_date() -> None:
    settings = ReportSettings(as_of=date(2026, 2, 1))
    assert settings.resolve_as_of(today=date(2026, 10, 20)) == date(2026, 2, 1)


def test_customer_palette_shares_no_colour_with_report_palette() -> None:
    report = ReportPalette()
    customers = CustomerPalette()
    report_colours = {
        report.primary,
        report.secondary,
        report.muted,
        report.subtle,
        report.accent,
        report.alert,
        report.border,
        report.bg_subtle,
        report.grid,
        *report.debtor_colors,
    }
    customer_colours = {*customers.named, customers.other}
    assert {c.lower() for c in report_colours}.isdisjoint(
        c.lower() for c in customer_colours
    )


def test_top_n_customers_defaults_to_ten() -> None:
    assert ReportSettings().top_n_customers == 10


def test_customer_palette_has_seven_distinct_named_hues() -> None:
    named = [c.lower() for c in CustomerPalette().named]
    assert len(set(named)) == len(named) == 7


def test_effective_advance_rate_nets_both_reserves() -> None:
    criteria = LendingCriteria(
        advance_rate=Decimal("0.85"),
        dilution_reserve_pct=Decimal("0.025"),
        fx_reserve_pct=Decimal("0.025"),
    )
    assert criteria.effective_advance_rate == Decimal("0.800")


def test_defaults_match_the_working_settings() -> None:
    settings = ReportSettings()
    assert settings.criteria.max_overdue_days == 90
    assert settings.criteria.dilution_reserve_pct == Decimal("0.025")
    assert settings.criteria.effective_advance_rate == Decimal("0.800")
    assert settings.chart_weeks == 52
