import io
from datetime import date
from pathlib import Path

import pytest
from reportlab.platypus import Paragraph

from abl import certificate, detail_page
from abl.models import CertificateParams, ReportSettings
from abl.pack import generate_report_pack

AS_OF = date(2026, 3, 13)
CSV_HEADER = (
    "Client Name,Invoice Date,Due Date,Invoice Amount,Currency,Invoice Paid Date"
)


class _RecordingParagraph(Paragraph):
    """A Paragraph that remembers the markup it was given."""

    seen: list[str] = []

    def __init__(self, text: str, *args: object, **kwargs: object) -> None:
        _RecordingParagraph.seen.append(text)
        super().__init__(text, *args, **kwargs)


def _csv(client: str, paid: str = "") -> io.StringIO:
    row = f'"{client}",2026-03-02,2026-04-01,100.00,CAD,{paid}'
    return io.StringIO(f"{CSV_HEADER}\n{row}")


def _pack(tmp_path: Path, client: str, disclaimer: str = "ok") -> None:
    generate_report_pack(
        ReportSettings(
            input_source=_csv(client),
            output_dir=tmp_path,
            as_of=AS_OF,
            certificate=CertificateParams(disclaimer=disclaimer),
        )
    )


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    _RecordingParagraph.seen = []
    monkeypatch.setattr(certificate, "Paragraph", _RecordingParagraph)
    monkeypatch.setattr(detail_page, "Paragraph", _RecordingParagraph)
    return _RecordingParagraph.seen


def test_debtor_names_are_escaped_on_every_page(
    tmp_path: Path, seen: list[str]
) -> None:
    _pack(tmp_path, "Smith & Sons <Ltd>")
    names = [text for text in seen if "Smith" in text]
    assert len(names) >= 2  # page 1 concentration table and the detail page
    assert all("Smith &amp; Sons &lt;Ltd&gt;" in text for text in names)


def test_disclaimer_is_escaped(tmp_path: Path, seen: list[str]) -> None:
    _pack(tmp_path, "Acme", disclaimer="Confidential & <private>")
    assert any("CONFIDENTIAL &amp; &lt;PRIVATE&gt;" in text for text in seen)


def test_a_name_with_an_unclosed_tag_no_longer_breaks_the_run(tmp_path: Path) -> None:
    _pack(tmp_path, "Acme <b>Bold & Co")
    assert (tmp_path / "bbc_certificate.pdf").exists()
