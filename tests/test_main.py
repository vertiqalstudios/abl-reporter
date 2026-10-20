from pathlib import Path

import pytest

from abl.__main__ import main
from abl.models import ReportSettings


def test_main_exits_with_message_when_input_csv_is_missing(tmp_path: Path) -> None:
    settings = ReportSettings(input_source=tmp_path / "nope.csv", output_dir=tmp_path)
    with pytest.raises(SystemExit, match="CSV file not found"):
        main(settings)
