"""Single entry point: `python -m abl` writes the certificate and all charts."""

from .models import ReportSettings
from .pack import generate_report_pack


def main(settings: ReportSettings | None = None) -> None:
    """Runs the report pack and prints where each output was written."""
    try:
        outputs = generate_report_pack(settings)
    except FileNotFoundError as err:
        raise SystemExit(str(err)) from err
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
