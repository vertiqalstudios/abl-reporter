import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
import pandas as pd
import seaborn as sns
from datetime import date

from abl.importer import load_invoices_from_csv
from abl.runner import run_collateral_report


def generate_collateral_chart(df: pd.DataFrame, output_path: str = "collateral_chart.png") -> None:
    # Use 'white' style to remove gridlines and set requested font
    sns.set_theme(style="white", palette="deep", rc={"font.family": "Helvetica Neue"})
    
    # Enforce exact schema match
    expected_schema = ["week", "eligible_collateral", "ineligible_collateral", "net_available_borrow"]
    if list(df.columns) != expected_schema:
        raise ValueError(f"Schema mismatch. Expected {expected_schema}, got {list(df.columns)}")
        
    date_col, elig_col, inelig_col, _ = expected_schema
    
    dates = pd.to_datetime(df[date_col])
    eligible = df[elig_col].astype(float)
    ineligible = df[inelig_col].astype(float)
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Calculate a dynamic bar width to prevent overlap on time-series axes
    diffs = dates.diff().dt.days.dropna()
    width = max(1, diffs.min() * 0.8) if not diffs.empty else 5
    
    ax.bar(dates, eligible, width=width, label="Eligible Collateral", color=sns.color_palette()[0])
    ax.bar(dates, ineligible, bottom=eligible, width=width, label="Ineligible Collateral", color=sns.color_palette()[1])
    
    avg_eligible = eligible.mean()
    ax.axhline(
        avg_eligible, 
        color='gray', 
        linestyle='--', 
        linewidth=1.5, 
        label=f"Avg: ${avg_eligible/1e3:,.0f}K"
    )
    
    year = dates.dt.year.mode()[0] if not dates.empty else date.today().year
    ax.set_title(f"Vertiqal Collateral Eligibility {year}", fontsize=14, fontweight='bold', pad=15)
    ax.set_xlabel("Collateral Week", fontsize=11, labelpad=10)
    ax.set_ylabel("Collateral Value CAD $", fontsize=11, labelpad=10)
    
    # Enforce native datetime ticks and formatting
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %y'))
    plt.setp(ax.get_xticklabels(), rotation=45, ha='right')
    
    # Format y-axis cleanly (Millions/Thousands)
    def currency_formatter(x: float, pos: int) -> str:
        if x >= 1e6:
            return f'${x/1e6:,.1f}M'
        return f'${x/1e3:,.0f}K'
        
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(currency_formatter))
    
    # Place legend inside the chart
    ax.legend(loc='upper right')
    
    plt.tight_layout()
    fig.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close(fig)


def main() -> None:
    try:
        with open("invoices.csv", "r", encoding="utf-8") as f:
            invoices = load_invoices_from_csv(f)
    except FileNotFoundError:
        print("Error: 'invoices.csv' not found in the current directory.")
        return

    if not invoices:
        print("No invoices available to process.")
        return

    # Dynamically bound the report to the actual invoice lifecycle
    start_date = min(inv.invoice_date for inv in invoices)
    
    # Determine the latest date (either the latest invoice or paid date)
    end_dates = [inv.invoice_date for inv in invoices]
    end_dates.extend([inv.paid_date for inv in invoices if inv.paid_date is not None])
    end_date = max(end_dates) if end_dates else date.today()

    reports, df = run_collateral_report(
        invoices=invoices,
        start_date=start_date,
        end_date=end_date,
    )
    
    generate_collateral_chart(df)
    print("Collateral report chart successfully generated as 'collateral_chart.png'")


if __name__ == "__main__":
    main()
