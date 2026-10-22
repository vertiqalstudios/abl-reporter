from pathlib import Path

import pytest

from abl.__main__ import main
from abl.models import ReportSettings


def test_main_exits_with_message_when_input_csv_is_missing(tmp_path: Path) -> None:
    settings = ReportSettings(input_source=tmp_path / "nope.csv", output_dir=tmp_path)
    with pytest.raises(SystemExit, match="CSV file not found"):
        main(settings)


def test_main_writes_a_troubleshooting_log(tmp_path: Path) -> None:
    settings = ReportSettings(input_source=tmp_path / "nope.csv", output_dir=tmp_path)
    with pytest.raises(SystemExit):
        main(settings)
    assert (tmp_path / "abl.log").exists()


def test_main_reports_an_unsupported_currency_usably(tmp_path: Path) -> None:
    csv_path = tmp_path / "eur.csv"
    csv_path.write_text(
        "Client Name,Invoice Date,Due Date,Invoice Amount,Currency,"
        "Invoice Paid Date,Invoice Key\n"
        "Acme,2026-03-01,2026-04-01,100.00,EUR,,E1\n"
    )
    settings = ReportSettings(input_source=csv_path, output_dir=tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(settings)
    assert str(caught.value) == (
        "FXConversionError: unsupported currency - invoice E1 is denominated "
        "in EUR which is not supported for collateral processing"
    )
