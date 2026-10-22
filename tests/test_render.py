import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest

RENDER_PATH = Path(__file__).resolve().parent.parent / "design" / "render.py"


def _load_render() -> ModuleType:
    spec = importlib.util.spec_from_file_location("design_render", RENDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = _load_render()


def test_for_print_points_svg_links_at_sized_pdfs() -> None:
    text = "![Flow](diagrams/engine.svg)\n"
    assert render.for_print(text) == (
        "![Flow](build/diagrams/engine.pdf){height=78%}\n"
    )


def test_for_print_gives_unlisted_diagrams_full_width() -> None:
    result = render.for_print("![x](diagrams/new_one.svg)")
    assert result == "![x](build/diagrams/new_one.pdf){width=100%}"


def test_for_print_leaves_other_links_alone() -> None:
    text = "[doc](certificate_design.md) ![p](photo.png)"
    assert render.for_print(text) == text


def test_missing_tools_names_what_is_absent() -> None:
    assert render.missing_tools(("definitely-not-a-program",)) == [
        "definitely-not-a-program"
    ]
    assert render.missing_tools(("python3",)) == []


def test_main_reports_missing_tools(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(render, "TOOLS", ("definitely-not-a-program",))
    monkeypatch.setattr(render, "missing_tools", lambda: ["definitely-not-a-program"])
    assert render.main() == 1
    assert "definitely-not-a-program" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("dot") is None, reason="Graphviz not installed")
def test_draw_writes_an_svg(tmp_path: Path) -> None:
    source = tmp_path / "tiny.dot"
    source.write_text("digraph g { a -> b }")
    target = tmp_path / "out" / "tiny.svg"
    render.draw(source, target)
    assert target.read_text().lstrip().startswith(("<?xml", "<svg"))
