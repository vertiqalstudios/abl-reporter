import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from abl.fx import make_fixed_fx_provider
from abl.importer import load_invoices_from_csv
from abl.models import (
    SNAPSHOT_WEEKDAY,
    CustomerPalette,
    Invoice,
    ReportPalette,
    ReportSettings,
)
from abl.runner import run_collateral_report
from abl.timeseries import compute_weekly_dso, customer_ar_balances

_OTHER_LABEL = "Other customers"
_LEGEND_COLUMNS = 4
_LEGEND_ROW_MARGIN = 0.05
_WEEK_TICK_FORMAT = "%b %Y"
_GRID_WIDTH = 0.6
_STREAM_GAP_WIDTH = 0.75


def get_chart_year_label(dates: pd.Series) -> str:
    """
    Determines a unified year label, filtering out stub years (<2 data points).
    Returns YYYY for a single year or YYYY–YYYY (with en-dash) for spans.
    """
    if dates.empty:
        return str(date.today().year)

    dt_series = pd.to_datetime(dates)
    counts = dt_series.dt.year.value_counts()

    # A year must have at least 2 weeks of data to avoid being a stub
    significant_years = counts[counts > 1].index.tolist()

    if not significant_years:
        # Fallback if the dataset is very small or all years are stubs
        return str(dt_series.dt.year.iloc[-1])

    min_yr = min(significant_years)
    max_yr = max(significant_years)

    if min_yr == max_yr:
        return str(min_yr)
    return f"{min_yr}–{max_yr}"


def _compact_currency(x: float) -> str:
    """Formats a dollar amount as $1.2M, $350K or $0."""
    if x == 0:
        return "$0"
    if x >= 1e6:
        return f"${x/1e6:,.1f}M"
    return f"${x/1e3:,.0f}K"


def _currency_tick(x: float, pos: int) -> str:
    """Matplotlib tick formatter wrapping _compact_currency."""
    return _compact_currency(x)


def _style_rc(theme: ReportPalette) -> dict[str, str | float | bool]:
    """Matplotlib settings shared by every chart: a quiet frame and value grid."""
    return {
        "font.family": theme.font_family,
        "font.size": 12,
        "axes.titlesize": 12,
        "axes.labelsize": 12,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "legend.fontsize": 11,
        "legend.frameon": False,
        "text.color": theme.primary,
        "axes.labelcolor": theme.secondary,
        "xtick.color": theme.secondary,
        "ytick.color": theme.secondary,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": theme.subtle,
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.axisbelow": True,
        "grid.color": theme.grid,
        "grid.linewidth": _GRID_WIDTH,
        "xtick.bottom": False,
        "ytick.left": False,
        "lines.solid_capstyle": "round",
        "patch.edgecolor": "white",
        "patch.force_edgecolor": True,
        "patch.linewidth": 1.0,
    }


@contextmanager
def _chart_style(palette: ReportPalette | None) -> Iterator[ReportPalette]:
    """Resolves the palette and scopes the report style to one chart.

    Every public chart applies this, so a chart looks the same whichever entry
    point draws it and never leaks matplotlib settings into the caller.
    """
    theme = palette if palette is not None else ReportPalette()
    with plt.rc_context(_style_rc(theme)):
        yield theme


def _bar_width(dates: pd.Series) -> float:
    """Returns a bar width in days that leaves a gap between weekly bars."""
    diffs = dates.diff().dt.days.dropna()
    return max(1, diffs.min() * 0.8) if not diffs.empty else 5


def _set_title_and_xlabel(ax: Axes, title: str) -> None:
    """Applies the shared title and week-axis label styling."""
    ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel("Collateral Week", fontsize=11, labelpad=10)


def _format_week_axis(ax: Axes) -> None:
    """Formats the x axis as month and full year, angled so labels never collide."""
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter(_WEEK_TICK_FORMAT))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")


def _week_formatter_until(x_max: float) -> ticker.FuncFormatter:
    """Formats week ticks like `_format_week_axis`, blanking any past the data."""
    base = mdates.DateFormatter(_WEEK_TICK_FORMAT)

    def format_tick(x: float, pos: int | None) -> str:
        return base(x, pos) if x <= x_max else ""

    return ticker.FuncFormatter(format_tick)


def _row_major[T](items: Sequence[T], columns: int) -> list[T]:
    """Reorders items so matplotlib's column-filled legend reads left to right."""
    rows, wide_columns = divmod(len(items), columns)
    heights = [rows + 1] * wide_columns + [rows] * (columns - wide_columns)
    return [
        items[row * columns + col]
        for col, height in enumerate(heights)
        for row in range(height)
    ]


def _legend_below(fig: Figure, ax: Axes, order: Sequence[str] | None = None) -> float:
    """Places the legend under the plot and returns the bottom margin it needs.

    Entries read left to right in `order` (default: drawing order).
    """
    handles, labels = ax.get_legend_handles_labels()
    if order is not None:
        lookup = dict(zip(labels, handles))
        labels = [name for name in order if name in lookup]
        handles = [lookup[name] for name in labels]
    columns = min(len(labels), _LEGEND_COLUMNS)
    fig.legend(
        _row_major(handles, columns),
        _row_major(labels, columns),
        loc="lower center",
        ncol=columns,
    )
    return _LEGEND_ROW_MARGIN * math.ceil(len(labels) / columns)


def _save_figure(fig: Figure, output_path: Path | str, bottom_margin: float) -> Path:
    """Lays out the figure above the legend margin, saves it and closes it."""
    fig.tight_layout(rect=(0, bottom_margin, 1, 1))
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def _customer_pivot(df: pd.DataFrame, max_named: int) -> pd.DataFrame | None:
    """Pivots weekly balances by customer, folding the smallest into one column.

    Columns are ordered largest customer first with the "Other" bucket last.
    Returns None when there are no customers to plot.
    """
    df_filtered = df[df["client_name"] != "NONE"]
    if df_filtered.empty:
        return None

    pivot_df = df_filtered.pivot_table(
        index="as_of_date", columns="client_name", values="balance", aggfunc="sum"
    ).fillna(0)
    ranked = pivot_df.sum().sort_values(ascending=False).index
    named, rest = ranked[:max_named], ranked[max_named:]

    grouped = pivot_df[named].copy()
    if len(rest) > 0:
        grouped[_OTHER_LABEL] = pivot_df[rest].sum(axis=1)
    return grouped


def _customer_colors(
    customers: CustomerPalette, columns: Sequence[str]
) -> dict[str, str]:
    """Maps customers to palette slots by rank, with the bucket in its own grey."""
    named = [name for name in columns if name != _OTHER_LABEL]
    colors = dict(zip(named, customers.named))
    colors[_OTHER_LABEL] = customers.other
    return colors


def generate_collateral_chart(
    df: pd.DataFrame,
    output_path: Path | str = "collateral_chart.png",
    palette: ReportPalette | None = None,
) -> Path:
    """Charts weekly eligible vs ineligible collateral with the average eligible."""
    expected_schema = [
        "week",
        "eligible_collateral",
        "ineligible_collateral",
        "net_available_borrow",
    ]
    if list(df.columns) != expected_schema:
        raise ValueError(
            f"Schema mismatch. Expected {expected_schema}, got {list(df.columns)}"
        )

    date_col, elig_col, inelig_col, _ = expected_schema

    dates = pd.to_datetime(df[date_col])
    eligible = df[elig_col].astype(float)
    ineligible = df[inelig_col].astype(float)

    with _chart_style(palette) as theme:
        fig, ax = plt.subplots(figsize=(10, 6))
        width = _bar_width(dates)

        ax.bar(
            dates,
            eligible,
            width=width,
            label="Eligible Collateral",
            color=theme.accent,
        )
        ax.bar(
            dates,
            ineligible,
            bottom=eligible,
            width=width,
            label="Ineligible Collateral",
            color=theme.alert,
        )

        avg_eligible = eligible.mean()
        avg_label = f"Avg eligible: ${avg_eligible/1e3:,.0f}K"
        ax.axhline(
            avg_eligible,
            color="gray",
            linestyle="--",
            linewidth=1.5,
            label=avg_label,
        )

        _set_title_and_xlabel(
            ax, f"Vertiqal Collateral Eligibility {get_chart_year_label(dates)}"
        )
        ax.set_ylabel("Collateral Value CAD $", fontsize=11, labelpad=10)
        _format_week_axis(ax)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(_currency_tick))

        order = ["Eligible Collateral", "Ineligible Collateral", avg_label]
        return _save_figure(fig, output_path, _legend_below(fig, ax, order=order))


def generate_dso_chart(
    dso_series: tuple,
    output_path: Path | str = "dso_chart.png",
    palette: ReportPalette | None = None,
) -> Path | None:
    """Charts weekly equal- and dollar-weighted DSO with the equal-weight average."""
    df = pd.DataFrame(dso_series)
    if df.empty:
        print("No DSO data available to plot.")
        return None

    dates = pd.to_datetime(df["as_of_date"])
    eq_dso = df["dso_equal_weight"].astype(float)
    dol_dso = df["dso_dollar_weight"].astype(float)

    with _chart_style(palette) as theme:
        fig, ax = plt.subplots(figsize=(10, 6))

        ax.plot(
            dates,
            eq_dso,
            label="Equal Weight DSO",
            color=theme.primary,
            linewidth=2,
        )
        ax.plot(
            dates,
            dol_dso,
            label="Dollar Weight DSO",
            color=theme.accent,
            linewidth=2,
        )

        avg_eq_dso = eq_dso.mean()
        if pd.notna(avg_eq_dso):
            ax.axhline(
                avg_eq_dso,
                color="gray",
                linestyle="--",
                linewidth=1.5,
                label=f"Avg Eq DSO: {avg_eq_dso:.0f} days",
            )

        _set_title_and_xlabel(ax, f"Vertiqal DSO Trends {get_chart_year_label(dates)}")
        ax.set_ylabel("Days Sales Outstanding", fontsize=11, labelpad=10)
        _format_week_axis(ax)

        return _save_figure(fig, output_path, _legend_below(fig, ax))


def generate_top_n_chart(
    df: pd.DataFrame,
    top_n: int,
    output_path: Path | str = "top_n_chart.png",
    palette: ReportPalette | None = None,
) -> Path | None:
    """Charts weekly open AR split between the top N customers and the rest."""
    df_filtered = df[df["client_name"] != "NONE"].copy()
    if df_filtered.empty:
        print("No customer data available to plot Top N chart.")
        return None

    top_label = f"Top {top_n} Customers"
    other_label = "Other Customers"
    df_filtered["category"] = df_filtered["is_top_n"].map(
        {True: top_label, False: other_label}
    )

    pivot_df = df_filtered.pivot_table(
        index="as_of_date", columns="category", values="balance", aggfunc="sum"
    ).fillna(0)
    pivot_df = pivot_df.reindex(columns=[top_label, other_label], fill_value=0.0)

    flat_df = pivot_df.reset_index()
    dates = pd.to_datetime(flat_df["as_of_date"])
    width = _bar_width(dates)

    with _chart_style(palette) as theme:
        fig, ax = plt.subplots(figsize=(10, 6))

        ax.bar(
            dates,
            flat_df[other_label],
            width=width,
            label=other_label,
            color=theme.secondary,
        )
        ax.bar(
            dates,
            flat_df[top_label],
            bottom=flat_df[other_label],
            width=width,
            label=top_label,
            color=theme.accent,
        )

        _set_title_and_xlabel(
            ax,
            f"Vertiqal Open AR: Top {top_n} vs Other Customers "
            f"{get_chart_year_label(dates)}",
        )
        ax.set_ylabel("Open AR Balance CAD $", fontsize=11, labelpad=10)
        _format_week_axis(ax)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(_currency_tick))

        return _save_figure(fig, output_path, _legend_below(fig, ax))


def generate_customer_ar_chart(
    df: pd.DataFrame,
    output_path: Path | str = "customer_ar_chart.png",
    palette: ReportPalette | None = None,
    customers: CustomerPalette | None = None,
) -> Path | None:
    """Charts weekly open AR stacked by customer, smallest folded into 'Other'."""
    slots = customers if customers is not None else CustomerPalette()
    pivot_df = _customer_pivot(df, max_named=len(slots.named))
    if pivot_df is None:
        print("No customer data available to plot AR chart.")
        return None

    colors = _customer_colors(slots, list(pivot_df.columns))
    flat_df = pivot_df.reset_index()
    dates = pd.to_datetime(flat_df["as_of_date"])
    width = _bar_width(dates)

    with _chart_style(palette):
        fig, ax = plt.subplots(figsize=(10, 6))

        bottom = pd.Series(0.0, index=flat_df.index)
        for col in pivot_df.columns:
            ax.bar(
                dates,
                flat_df[col],
                bottom=bottom,
                width=width,
                color=colors[col],
                label=col,
            )
            bottom += flat_df[col]

        _set_title_and_xlabel(
            ax, f"Vertiqal Open AR by Customer {get_chart_year_label(dates)}"
        )
        ax.set_ylabel("Open AR Balance CAD $", fontsize=11, labelpad=10)
        _format_week_axis(ax)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(_currency_tick))

        return _save_figure(fig, output_path, _legend_below(fig, ax))


def _week_edges(dates: pd.Series) -> np.ndarray:
    """Returns the boundaries of the week centred on each snapshot date."""
    x_num = mdates.date2num(dates)
    step = np.diff(x_num).min() if len(x_num) > 1 else 7.0
    return np.append(x_num - step / 2, x_num[-1] + step / 2)


def _stream_bounds(
    layers: Sequence[pd.Series],
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Stacks layers symmetrically about zero, returning each (lower, upper)."""
    stacked = np.vstack([np.asarray(layer, dtype=float) for layer in layers])
    tops = np.cumsum(stacked, axis=0) - stacked.sum(axis=0) / 2
    return list(zip(tops - stacked, tops))


def _hold_last(values: np.ndarray) -> np.ndarray:
    """Repeats the final value so a post-step fill spans the last week too."""
    return np.append(values, values[-1])


def _center_out_order(keys: Sequence[str]) -> list[str]:
    """Orders keys (largest first) outward from the centre for stream aesthetics."""
    return [*reversed(keys[1::2]), *keys[0::2]]


def _scale_bar_value(max_vol: float) -> float:
    """Rounds max_vol down to a clean 1, 2 or 5 x 10^n scale-bar length."""
    magnitude = 10 ** np.floor(np.log10(max_vol if max_vol > 0 else 1))
    ratio = max_vol / magnitude
    if ratio >= 5:
        return 5 * magnitude
    if ratio >= 2:
        return 2 * magnitude
    return magnitude


def generate_customer_stream_chart(
    df: pd.DataFrame,
    output_path: Path | str = "customer_ar_stream_chart.png",
    palette: ReportPalette | None = None,
    customers: CustomerPalette | None = None,
) -> Path | None:
    """Draws open AR by customer as a weekly stream with a vertical scale bar."""
    slots = customers if customers is not None else CustomerPalette()
    pivot_df = _customer_pivot(df, max_named=len(slots.named))
    if pivot_df is None:
        return None

    ranked = list(pivot_df.columns)
    center_ordered = _center_out_order(ranked)
    colors = _customer_colors(slots, ranked)

    flat_df = pivot_df.reset_index()
    edges = _week_edges(pd.to_datetime(flat_df["as_of_date"]))
    bounds = _stream_bounds([flat_df[col] for col in center_ordered])

    with _chart_style(palette):
        fig, ax = plt.subplots(figsize=(10, 6))

        for name, (lower, upper) in zip(center_ordered, bounds):
            ax.fill_between(
                edges,
                _hold_last(lower),
                _hold_last(upper),
                step="post",
                color=colors[name],
                label=name,
                edgecolor="white",
                linewidth=_STREAM_GAP_WIDTH,
            )

        max_vol = float(pivot_df.sum(axis=1).max())
        scale_val = _scale_bar_value(max_vol)

        scale_x = edges[-1] + (edges[-1] - edges[0]) * 0.03
        cap_w = (edges[-1] - edges[0]) * 0.005

        # Draw scale bar bracket
        ax.plot(
            [scale_x, scale_x], [-scale_val / 2, scale_val / 2], color="#555555", lw=1.5
        )
        ax.plot(
            [scale_x - cap_w, scale_x + cap_w],
            [scale_val / 2, scale_val / 2],
            color="#555555",
            lw=1.5,
        )
        ax.plot(
            [scale_x - cap_w, scale_x + cap_w],
            [-scale_val / 2, -scale_val / 2],
            color="#555555",
            lw=1.5,
        )

        ax.text(
            scale_x + cap_w * 2,
            0,
            f"{_compact_currency(scale_val)} scale",
            va="center",
            ha="left",
            fontsize=10,
            color="#555555",
            fontweight="bold",
        )

        dates = pd.to_datetime(flat_df["as_of_date"])
        _set_title_and_xlabel(
            ax, f"Vertiqal Open AR Volume Stream {get_chart_year_label(dates)}"
        )

        ax.xaxis_date()
        _format_week_axis(ax)

        # Expand x-limit to fit the scale bar, but end the axis and its labels with
        # the data
        ax.set_xlim(edges[0], scale_x + (edges[-1] - edges[0]) * 0.15)
        ax.spines["bottom"].set_bounds(edges[0], edges[-1])
        ax.xaxis.set_major_formatter(_week_formatter_until(edges[-1]))

        # The scale bar replaces the y axis, so it and its grid stay hidden
        ax.yaxis.set_visible(False)

        return _save_figure(fig, output_path, _legend_below(fig, ax, order=ranked))


def _chart_start(first_invoice: date, as_of: date, weeks: int) -> date:
    """Returns the first snapshot day of the chart window.

    The window reaches `weeks` back from `as_of` but never before the first
    invoice, and lands on the snapshot weekday so every chart shares its dates.
    """
    start = max(first_invoice, as_of - timedelta(weeks=weeks))
    return start + timedelta(days=(SNAPSHOT_WEEKDAY - start.weekday()) % 7)


def generate_all_reports(
    settings: ReportSettings | None = None,
    invoices: Sequence[Invoice] | None = None,
) -> dict[str, Path]:
    """Generates all timeseries analytics and charts from ReportSettings."""
    cfg = settings if settings is not None else ReportSettings()

    if invoices is None:
        invoices = load_invoices_from_csv(cfg.input_source)

    if not invoices:
        return {}

    first_invoice = min(inv.invoice_date for inv in invoices)
    end_date = cfg.resolve_as_of()
    if end_date < first_invoice:
        raise ValueError(
            f"as-of date {end_date} is before the first invoice date {first_invoice}"
        )
    start_date = _chart_start(first_invoice, end_date, cfg.chart_weeks)

    fx_provider = cfg.fx if cfg.fx is not None else make_fixed_fx_provider()
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _, df = run_collateral_report(
        invoices=invoices,
        start_date=start_date,
        end_date=end_date,
        criteria=cfg.criteria,
        fx=fx_provider,
    )
    dso_series = compute_weekly_dso(
        invoices=invoices, start_date=start_date, end_date=end_date, fx=fx_provider
    )
    top_n = cfg.top_n_customers
    ar_df = customer_ar_balances(
        invoices=invoices,
        start_date=start_date,
        end_date=end_date,
        fx=fx_provider,
        top_n=top_n,
    )

    charts: dict[str, Path | None] = {
        "collateral": generate_collateral_chart(
            df, output_path=out_dir / "collateral_chart.png", palette=cfg.palette
        ),
        "dso": generate_dso_chart(
            dso_series, output_path=out_dir / "dso_chart.png", palette=cfg.palette
        ),
        "top_n": generate_top_n_chart(
            ar_df, top_n, output_path=out_dir / "top_n_chart.png", palette=cfg.palette
        ),
        "customer_ar": generate_customer_ar_chart(
            ar_df,
            output_path=out_dir / "customer_ar_chart.png",
            palette=cfg.palette,
            customers=cfg.customer_palette,
        ),
        "stream": generate_customer_stream_chart(
            ar_df,
            output_path=out_dir / "customer_ar_stream_chart.png",
            palette=cfg.palette,
            customers=cfg.customer_palette,
        ),
    }
    return {name: path for name, path in charts.items() if path}
