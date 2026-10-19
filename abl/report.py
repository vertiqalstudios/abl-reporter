from collections.abc import Sequence
from datetime import date
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.interpolate import pchip_interpolate

from abl.fx import make_fixed_fx_provider
from abl.importer import load_invoices_from_csv
from abl.models import Invoice, ReportPalette, ReportSettings
from abl.runner import run_collateral_report
from abl.timeseries import compute_weekly_dso, customer_ar_balances


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


def generate_collateral_chart(
    df: pd.DataFrame,
    output_path: Path | str = "collateral_chart.png",
    palette: ReportPalette | None = None,
) -> Path:
    theme = palette if palette is not None else ReportPalette()
    sns.set_theme(style="white", palette="deep", rc={"font.family": theme.font_family})

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

    fig, ax = plt.subplots(figsize=(10, 6))

    diffs = dates.diff().dt.days.dropna()
    width = max(1, diffs.min() * 0.8) if not diffs.empty else 5

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
    ax.axhline(
        avg_eligible,
        color="gray",
        linestyle="--",
        linewidth=1.5,
        label=f"Avg: ${avg_eligible/1e3:,.0f}K",
    )

    year = get_chart_year_label(dates)
    ax.set_title(
        f"Vertiqal Collateral Eligibility {year}",
        fontsize=14,
        fontweight="bold",
        pad=15,
    )
    ax.set_xlabel("Collateral Week", fontsize=11, labelpad=10)
    ax.set_ylabel("Collateral Value CAD $", fontsize=11, labelpad=10)

    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    def currency_formatter(x: float, pos: int) -> str:
        if x >= 1e6:
            return f"${x/1e6:,.1f}M"
        return f"${x/1e3:,.0f}K"

    ax.yaxis.set_major_formatter(ticker.FuncFormatter(currency_formatter))
    ax.legend(loc="upper right")

    plt.tight_layout()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def generate_dso_chart(
    dso_series: tuple,
    output_path: Path | str = "dso_chart.png",
    palette: ReportPalette | None = None,
) -> Path | None:
    theme = palette if palette is not None else ReportPalette()
    sns.set_theme(style="white", palette="deep", rc={"font.family": theme.font_family})

    df = pd.DataFrame(dso_series)
    if df.empty:
        print("No DSO data available to plot.")
        return None

    dates = pd.to_datetime(df["as_of_date"])
    eq_dso = df["dso_equal_weight"].astype(float)
    dol_dso = df["dso_dollar_weight"].astype(float)

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

    pd_dates = pd.to_datetime(dates)
    year = get_chart_year_label(pd_dates)
    ax.set_title(f"Vertiqal DSO Trends {year}", fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel("Collateral Week", fontsize=11, labelpad=10)
    ax.set_ylabel("Days Sales Outstanding", fontsize=11, labelpad=10)

    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    ax.legend(loc="upper right")

    plt.tight_layout()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def generate_top_n_chart(
    df: pd.DataFrame,
    top_n: int,
    output_path: Path | str = "top_n_chart.png",
    palette: ReportPalette | None = None,
) -> Path | None:
    theme = palette if palette is not None else ReportPalette()
    sns.set_theme(style="white", palette="deep", rc={"font.family": theme.font_family})

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
    for cat in [top_label, other_label]:
        if cat not in pivot_df.columns:
            pivot_df[cat] = 0.0

    flat_df = pivot_df.reset_index()
    dates = pd.to_datetime(flat_df["as_of_date"])

    diffs = dates.diff().dt.days.dropna()
    width = max(1, diffs.min() * 0.8) if not diffs.empty else 5

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

    year = get_chart_year_label(dates)
    ax.set_title(
        f"Vertiqal Top Customers Concentration {year}",
        fontsize=14,
        fontweight="bold",
        pad=15,
    )
    ax.set_xlabel("Collateral Week", fontsize=11, labelpad=10)
    ax.set_ylabel("Open AR Balance CAD $", fontsize=11, labelpad=10)

    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    def currency_formatter(x: float, pos: int) -> str:
        if x >= 1e6:
            return f"${x/1e6:,.1f}M"
        return f"${x/1e3:,.0f}K"

    ax.yaxis.set_major_formatter(ticker.FuncFormatter(currency_formatter))
    ax.legend(loc="upper right")

    plt.tight_layout()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def generate_customer_ar_chart(
    df: pd.DataFrame,
    output_path: Path | str = "customer_ar_chart.png",
    palette: ReportPalette | None = None,
) -> Path | None:
    theme = palette if palette is not None else ReportPalette()
    sns.set_theme(style="white", palette="deep", rc={"font.family": theme.font_family})

    df_filtered = df[df["client_name"] != "NONE"]
    if df_filtered.empty:
        print("No customer data available to plot AR chart.")
        return None

    pivot_df = df_filtered.pivot_table(
        index="as_of_date", columns="client_name", values="balance", aggfunc="sum"
    ).fillna(0)
    client_cols = sorted(pivot_df.columns)

    flat_df = pivot_df.reset_index()
    dates = pd.to_datetime(flat_df["as_of_date"])

    diffs = dates.diff().dt.days.dropna()
    width = max(1, diffs.min() * 0.8) if not diffs.empty else 5

    fig, ax = plt.subplots(figsize=(10, 6))
    colors = (
        list(theme.debtor_colors[: len(client_cols)])
        if len(client_cols) <= len(theme.debtor_colors)
        else sns.color_palette("tab20", len(client_cols))
    )

    bottom = pd.Series(0.0, index=flat_df.index)
    for i, col in enumerate(client_cols):
        ax.bar(dates, flat_df[col], bottom=bottom, width=width, color=colors[i])
        bottom += flat_df[col]

    year = get_chart_year_label(dates)
    ax.set_title(
        f"Vertiqal Open AR by Customer {year}", fontsize=14, fontweight="bold", pad=15
    )
    ax.set_xlabel("Collateral Week", fontsize=11, labelpad=10)
    ax.set_ylabel("Open AR Balance CAD $", fontsize=11, labelpad=10)

    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    def currency_formatter(x: float, pos: int) -> str:
        if x >= 1e6:
            return f"${x/1e6:,.1f}M"
        return f"${x/1e3:,.0f}K"

    ax.yaxis.set_major_formatter(ticker.FuncFormatter(currency_formatter))

    plt.tight_layout()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def _smooth_timeseries(
    dates: pd.Series, y_data: list[pd.Series], points: int = 300
) -> tuple[np.ndarray, list[np.ndarray]]:
    x_num = mdates.date2num(dates)
    x_dense = np.linspace(x_num.min(), x_num.max(), points)

    y_dense = []
    for y in y_data:
        # PCHIP is shape-preserving; prevents splines from dipping below 0
        y_interp = np.clip(pchip_interpolate(x_num, y, x_dense), 0, None)
        y_dense.append(y_interp)

    return x_dense, y_dense


def generate_customer_stream_chart(
    df: pd.DataFrame,
    output_path: Path | str = "customer_ar_stream_chart.png",
    palette: ReportPalette | None = None,
) -> Path | None:
    theme = palette if palette is not None else ReportPalette()
    sns.set_theme(style="white", palette="deep", rc={"font.family": theme.font_family})

    df_filtered = df[df["client_name"] != "NONE"]
    if df_filtered.empty:
        return None

    pivot_df = df_filtered.pivot_table(
        index="as_of_date", columns="client_name", values="balance", aggfunc="sum"
    ).fillna(0)

    # Inside-out sorting for stream aesthetics
    col_sums = pivot_df.sum().sort_values(ascending=False)
    sorted_keys = col_sums.index.tolist()
    center_ordered = []
    for i, col in enumerate(sorted_keys):
        if i % 2 == 0:
            center_ordered.append(col)
        else:
            center_ordered.insert(0, col)

    flat_df = pivot_df.reset_index()
    dates = pd.to_datetime(flat_df["as_of_date"])

    y_raw = [flat_df[col] for col in center_ordered]
    x_dense, y_dense = _smooth_timeseries(dates, y_raw)

    fig, ax = plt.subplots(figsize=(10, 6))
    colors = (
        list(theme.debtor_colors[: len(center_ordered)])
        if len(center_ordered) <= len(theme.debtor_colors)
        else sns.color_palette("tab20", len(center_ordered))
    )

    ax.stackplot(
        x_dense,
        *y_dense,
        baseline="sym",
        colors=colors,
        alpha=0.6,
        edgecolor="white",
        linewidth=0.5,
    )

    max_vol = np.max(np.sum(y_dense, axis=0))
    magnitude = 10 ** np.floor(np.log10(max_vol if max_vol > 0 else 1))

    # Find a clean scale marker value
    if max_vol / magnitude >= 5:
        scale_val = 5 * magnitude
    elif max_vol / magnitude >= 2:
        scale_val = 2 * magnitude
    else:
        scale_val = magnitude

    scale_x = x_dense[-1] + (x_dense[-1] - x_dense[0]) * 0.03
    cap_w = (x_dense[-1] - x_dense[0]) * 0.005

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

    def currency_label(x: float) -> str:
        if x >= 1e6:
            return f"${x/1e6:,.1f}M"
        return f"${x/1e3:,.0f}K"

    ax.text(
        scale_x + cap_w * 2,
        0,
        f"↕ {currency_label(scale_val)}",
        va="center",
        ha="left",
        fontsize=10,
        color="#555555",
        fontweight="bold",
    )

    year = get_chart_year_label(dates)
    ax.set_title(
        f"Vertiqal Open AR Volume Stream {year}", fontsize=14, fontweight="bold", pad=15
    )
    ax.set_xlabel("Collateral Week", fontsize=11, labelpad=10)

    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    # Expand x-limit to fit the scale bar
    ax.set_xlim(x_dense[0], scale_x + (x_dense[-1] - x_dense[0]) * 0.15)

    # Hide Y axis and borders entirely
    ax.yaxis.set_visible(False)
    for spine in ["left", "right", "top"]:
        ax.spines[spine].set_visible(False)

    plt.tight_layout()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def generate_all_reports(
    settings: ReportSettings | None = None,
    invoices: Sequence[Invoice] | None = None,
) -> dict[str, Path]:
    """Generates all timeseries analytics and chart visualizations using ReportSettings."""
    cfg = settings if settings is not None else ReportSettings()

    if invoices is None:
        invoices = load_invoices_from_csv(cfg.input_source)

    if not invoices:
        return {}

    start_date = min(inv.invoice_date for inv in invoices)
    end_dates = [inv.invoice_date for inv in invoices]
    end_dates.extend([inv.paid_date for inv in invoices if inv.paid_date is not None])
    end_date = max(end_dates) if end_dates else date.today()

    fx_provider = cfg.fx if cfg.fx is not None else make_fixed_fx_provider()
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated: dict[str, Path] = {}

    reports, df = run_collateral_report(
        invoices=invoices,
        start_date=start_date,
        end_date=end_date,
        criteria=cfg.criteria,
        fx=fx_provider,
    )
    collat_path = generate_collateral_chart(
        df,
        output_path=out_dir / "collateral_chart.png",
        palette=cfg.palette,
    )
    generated["collateral"] = collat_path

    dso_series = compute_weekly_dso(
        invoices=invoices, start_date=start_date, end_date=end_date, fx=fx_provider
    )
    dso_path = generate_dso_chart(
        dso_series,
        output_path=out_dir / "dso_chart.png",
        palette=cfg.palette,
    )
    if dso_path:
        generated["dso"] = dso_path

    top_n = 4
    ar_df = customer_ar_balances(
        invoices=invoices,
        start_date=start_date,
        end_date=end_date,
        fx=fx_provider,
        top_n=top_n,
    )

    top_n_path = generate_top_n_chart(
        ar_df,
        top_n,
        output_path=out_dir / "top_n_chart.png",
        palette=cfg.palette,
    )
    if top_n_path:
        generated["top_n"] = top_n_path

    ar_path = generate_customer_ar_chart(
        ar_df,
        output_path=out_dir / "customer_ar_chart.png",
        palette=cfg.palette,
    )
    if ar_path:
        generated["customer_ar"] = ar_path

    stream_path = generate_customer_stream_chart(
        ar_df,
        output_path=out_dir / "customer_ar_stream_chart.png",
        palette=cfg.palette,
    )
    if stream_path:
        generated["stream"] = stream_path

    return generated


def main() -> None:
    """Executes all chart report generation using default ReportSettings."""
    outputs = generate_all_reports()
    for name, path in outputs.items():
        print(f"Report [{name}] saved to: {path}")


if __name__ == "__main__":
    main()
