# ABL Collateral Monitoring Engine

Asset-Backed Lending (ABL) collateral evaluation and borrowing base engine.

## Requirements
Written for Python 3.14. May be backwards compatible, but honestly - doesn't need to be.

## License
Copyright (c) 2026 Vertiqal Studios Corporation & Columbia Westminster Pte Ltd. All Rights Reserved.

## Generating the report
Install once, then run from the directory that holds your invoice CSV:

```bash
uv venv --python 3.14
uv pip install -e .
uv run abl-report        # or: uv run python -m abl
```

This reads `invoices.csv` and writes the Borrowing Base Certificate PDF
(`bbc_certificate.pdf`) and all five charts to `output/`, printing each path. The CSV
needs the columns `Client Name, Invoice Date, Due Date, Invoice Amount, Currency,
Invoice Paid Date`. There are no command-line flags; a missing CSV exits with a "CSV file not found" message.

### As-of date
One as-of date drives the certificate and every chart. It defaults to the **Friday of
the current week** and is set with `ReportSettings(as_of=...)`. Anything after it is
ignored: invoices dated later are left out, and a payment dated later still counts as
unpaid. Charts run weekly (Fridays) from the first invoice up to the as-of date.

### Custom settings
Configure by building a `ReportSettings` object in your own script:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from abl import CertificateParams, ReportSettings, ReportStatus, generate_report_pack

settings = ReportSettings(
    input_source=Path("data/invoices.csv"),
    output_dir=Path("reports/2026-10-23"),
    as_of=date(2026, 10, 23),
    certificate=CertificateParams(
        status=ReportStatus.CERTIFIED,
        existing_borrow=Decimal("250000.00"),
        facility_limit=Decimal("2000000.00"),
    ),
)
outputs = generate_report_pack(settings)  # {"certificate": ..., "collateral": ..., ...}
```

`ReportSettings` also takes `criteria` (`LendingCriteria`: advance rate, concentration
cap, taint rules), `palette` and `fx` (defaults to a fixed 1 USD = 1.44 CAD). To print
the paths as the command does, pass the same object to `abl.__main__.main(settings)`.

## Running Tests
```bash
uv run pytest
```

## Authorship
v0.02+ - Maintained by @fadesibert
v0.01 - Created by Gemini / Architect @fadesibert
