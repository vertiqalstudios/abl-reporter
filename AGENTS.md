# AGENTS.md

Guidance for AI coding agents (and humans) working in `abl-reporter`.

## Ground rules

- Target **Python 3.14+ only**. No compatibility shims, no `sys.version_info` checks.
- **Pre-commit hooks are mandatory.** Every commit must pass them; never bypass with `--no-verify`.
- Prefer small, flat, obviously-correct functions over clever ones.
- Match the style of surrounding code before inventing new patterns.

## Setup

```bash
uv venv --python 3.14
uv pip install -r requirements-dev.txt
uv run pre-commit install
```

## Before every commit

```bash
uv run pre-commit run --all-files
uv run pytest
```

If a hook rewrites files (black, isort, whitespace), re-stage and commit again. If a hook fails, fix the
cause — do not disable the hook, add blanket `# noqa`, or edit `.pre-commit-config.yaml` to make it pass.
A targeted `# noqa: <CODE>` is acceptable only with a short reason on the same line.

## Python 3.14 syntax

Use modern syntax; treat legacy forms as errors.

| Use | Not |
| --- | --- |
| `list[int]`, `dict[str, Decimal]`, `tuple[int, ...]` | `List`, `Dict`, `Tuple` from `typing` |
| `X \| None`, `A \| B` | `Optional[X]`, `Union[A, B]` |
| `type Rows = list[Invoice]` (PEP 695) | `Rows: TypeAlias = ...` |
| `def first[T](xs: Sequence[T]) -> T:` | `T = TypeVar("T")` |
| `class Box[T]:` | `class Box(Generic[T]):` |
| `collections.abc.Sequence`, `Mapping`, `Iterable`, `Callable` | `typing` equivalents |
| `typing.Self`, `typing.override` | returning the class name as a string; unmarked overrides |
| Plain annotations (deferred by default, PEP 649) | `from __future__ import annotations`, quoted forward refs |
| `match` / `case` for structural dispatch | long `if/elif` chains on type or shape |
| f-strings | `%` formatting, `.format()` |
| `pathlib.Path` | `os.path` |
| `@dataclass(frozen=True, slots=True)` / `enum.StrEnum` | ad-hoc dicts or string constants |

## Typing

- Annotate **every** function signature, including return type (`-> None` too).
- Be specific: `Decimal` for money, `date` for dates, `frozenset[str]` for immutable lookups,
  `Sequence[Invoice]` for read-only inputs, concrete types for return values.
- Avoid `Any`, `object` as a catch-all, and bare `dict`/`list`. If unavoidable, explain why in a comment.
- Prefer `Protocol` over inheritance for structural contracts; `TypedDict` for dict-shaped external data.
- Use `Literal[...]` or `StrEnum` for closed sets of values.
- Annotate empty containers on creation: `totals: dict[str, Decimal] = defaultdict(Decimal)`.
- Never use `float` for currency.

## Cyclomatic complexity

Aim for **complexity ≤ 5 per function**. The flake8 hook enforces a hard ceiling of 8
(`--max-complexity=8`, rule `C901`); never raise it or suppress `C901` — refactor instead.

- Guard clauses and early returns instead of nested `if`s.
- One function, one job. Extract predicates (`is_invoice_open`, `check_contra`) and compose them.
- Replace branch ladders with lookup tables (`dict[Key, Callable]`) or `match`.
- Keep nesting depth ≤ 2. Hoist loop bodies into named functions.
- Prefer comprehensions, `sum()`, `any()`, `all()` over accumulator loops with conditionals.
- No flag arguments that switch behaviour — write two functions.

```python
# Good: flat, single-purpose, each branch is a guard
def is_invoice_open(inv: Invoice, as_of: date) -> bool:
    """True if the invoice is issued and unpaid at `as_of`."""
    if inv.invoice_date > as_of:
        return False
    return inv.paid_date is None or inv.paid_date > as_of
```

## Docstrings and comments

- Every public module, class, and function gets a docstring. One line is the default.
- Docstrings say **what** and **why**, not **how**. Imperative or declarative, ≤ 1 line where possible.
- Add `Args:` / `Returns:` / `Raises:` sections only when types and names don't make it obvious.
- Comments are terse and rare: explain intent, domain rules, or non-obvious constraints.
  Don't narrate the code (`# increment i`), don't leave commented-out code, don't sign or date comments.
- Domain terms (eligibility, contra, taint, borrowing base) should match the names used in `abl/models.py`.

## Readability

- Descriptive names over abbreviations; booleans read as predicates (`is_`, `has_`).
- Line length 88 (black). Imports ordered by isort.
- Pure functions by default; isolate I/O (CSV import, chart and PDF output) at the edges.
- Immutable data where practical (`frozen=True`, `tuple`, `frozenset`).
- Raise specific exceptions with useful messages; never `except Exception: pass`.

## Tests

- Pytest, under `tests/`, mirroring module names (`abl/rules.py` → `tests/test_rules.py`).
- New behaviour ships with tests; bug fixes ship with a regression test.
- Tests follow the same typing and complexity rules — no logic branches inside tests; use
  `pytest.mark.parametrize` instead.

## Done means

1. `uv run pre-commit run --all-files` passes.
2. `uv run pytest` passes.
3. New code is fully typed, documented, and within the complexity budget.
