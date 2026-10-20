from datetime import date

import pandas as pd
import pytest

from abl.report import get_chart_year_label


@pytest.mark.parametrize(
    "name,dates,expected",
    [
        ("Single year, multiple points", ["2024-01-01", "2024-01-08"], "2024"),
        ("Single year, one point (fallback)", ["2024-01-01"], "2024"),
        (
            "Two non-stub years",
            ["2024-12-15", "2024-12-22", "2025-01-05", "2025-01-12"],
            "2024–2025",
        ),
        ("First year is stub", ["2024-12-31", "2025-01-07", "2025-01-14"], "2025"),
        ("Last year is stub", ["2024-12-17", "2024-12-24", "2025-01-02"], "2024"),
        (
            "Three years, edges stub",
            ["2023-12-31", "2024-01-07", "2024-12-24", "2025-01-02"],
            "2024",
        ),
        (
            "Three non-stub years",
            [
                "2023-12-24",
                "2023-12-31",
                "2024-06-01",
                "2024-06-08",
                "2025-01-07",
                "2025-01-14",
            ],
            "2023–2025",
        ),
    ],
)
def test_year_label_logic(name: str, dates: list[str], expected: str) -> None:
    series = pd.to_datetime(pd.Series(dates))
    assert get_chart_year_label(series) == expected


def test_empty_series() -> None:
    series = pd.Series([], dtype="datetime64[ns]")
    expected = str(date.today().year)
    assert get_chart_year_label(series) == expected
