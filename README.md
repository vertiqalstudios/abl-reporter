# ABL Collateral Monitoring Engine

Asset-Backed Lending (ABL) collateral evaluation and borrowing base engine.

## Requirements
Written for Python 3.14. May be backwards compatible, but honestly - doesn't need to be.

## License
Copyright (c) 2026 Vertiqal Studios Corporation & Columbia Westminster Pte Ltd. All Rights Reserved.

## Generating the report
One call writes the Borrowing Base Certificate PDF and all five charts. Settings are
Python-only (there is no command-line interface); the input CSV needs the columns
`Client Name, Invoice Date, Due Date, Invoice Amount, Currency, Invoice Paid Date`.

```python
from pathlib import Path

from abl import ReportSettings, generate_report_pack

settings = ReportSettings(input_source=Path("invoices.csv"), output_dir=Path("output"))
outputs = generate_report_pack(settings)  # {"certificate": ..., "collateral": ..., ...}
```

Defaults: `input_source="invoices.csv"` (relative to the working directory) and
`output_dir="output/"`. The certificate's as-of date defaults to the latest invoice date;
set `CertificateParams(as_of=...)` to override it.

## Running Tests
```bash
uv run pytest
```

## Authorship
v0.02+ - Maintained by @fadesibert
v0.01 - Created by Gemini / Architect @fadesibert
