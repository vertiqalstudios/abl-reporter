"""Builds the design docs: Graphviz draws the diagrams, pandoc and xelatex make PDFs.

Run `uv run python design/render.py`. Needs `dot` (Graphviz), `pandoc` and `xelatex` on
the PATH; the Python side is standard library only. The SVGs in `design/diagrams/` are
committed so GitHub shows them; the PDFs land in `design/build/`, which is not.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

DESIGN_DIR = Path(__file__).resolve().parent
DIAGRAM_DIR = DESIGN_DIR / "diagrams"
BUILD_DIR = DESIGN_DIR / "build"
DOCUMENTS = ("system_flow", "certificate_design")
TOOLS = ("dot", "pandoc", "xelatex")
SVG_LINK = re.compile(r"\]\(diagrams/([\w-]+)\.svg\)")
# One dimension only, so the diagram keeps its aspect ratio; tall ones size by height.
PRINT_SIZE = {
    "inputs_outputs": "width=100%",
    "chart_branch": "width=100%",
    "certificate_branch": "width=75%",
    "run_sequence": "height=78%",
    "engine": "height=78%",
}
PANDOC_OPTIONS = (
    "--from=markdown-implicit_figures",
    "--pdf-engine=xelatex",
    "--variable=geometry:margin=0.8in",
    "--variable=fontsize=10pt",
    "--variable=colorlinks=true",
    "--variable=monofont=DejaVu Sans Mono",
    "--variable=monofontoptions=Scale=0.78",
)


def missing_tools(tools: tuple[str, ...] = TOOLS) -> list[str]:
    """Returns the required programs that aren't on the PATH."""
    return [tool for tool in tools if shutil.which(tool) is None]


def draw(source: Path, target: Path) -> None:
    """Draws one Graphviz source to the format named by the target's suffix."""
    target.parent.mkdir(parents=True, exist_ok=True)
    kind = target.suffix.lstrip(".")
    subprocess.run(["dot", f"-T{kind}", str(source), "-o", str(target)], check=True)


def render_diagrams() -> list[Path]:
    """Writes each diagram as a committed SVG and as a build-only PDF for print."""
    written: list[Path] = []
    for source in sorted(DIAGRAM_DIR.glob("*.dot")):
        svg = source.with_suffix(".svg")
        draw(source, svg)
        draw(source, BUILD_DIR / "diagrams" / f"{source.stem}.pdf")
        written.append(svg)
    return written


def for_print(markdown: str) -> str:
    """Points diagram links at the vector PDFs and sizes them to fit the page."""

    def link(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"](build/diagrams/{name}.pdf){{{PRINT_SIZE.get(name, 'width=100%')}}}"

    return SVG_LINK.sub(link, markdown)


def render_document(name: str) -> Path:
    """Builds `design/<name>.md` into `design/build/<name>.pdf`."""
    source = DESIGN_DIR / f"{name}.md"
    target = BUILD_DIR / f"{name}.pdf"
    subprocess.run(
        [
            "pandoc",
            *PANDOC_OPTIONS,
            f"--resource-path={DESIGN_DIR}",
            "-o",
            str(target),
        ],
        input=for_print(source.read_text(encoding="utf-8")),
        text=True,
        check=True,
    )
    return target


def main() -> int:
    """Renders every diagram and document, returning a process exit code."""
    missing = missing_tools()
    if missing:
        print(f"Missing required program(s): {', '.join(missing)}", file=sys.stderr)
        return 1
    BUILD_DIR.mkdir(exist_ok=True)
    for path in render_diagrams():
        print(f"diagram: {path.relative_to(DESIGN_DIR)}")
    for name in DOCUMENTS:
        print(f"document: {render_document(name).relative_to(DESIGN_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
