from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from .backtest import Trade


def trades_to_frame(trades: list[Trade]) -> pd.DataFrame:
    df = pd.DataFrame([asdict(t) for t in trades])
    if df.empty:
        return df
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df.sort_values(["ticker", "position", "strategy", "entry_date"]).reset_index(
        drop=True
    )
    df["cum_pnl"] = df.groupby(["ticker", "position", "strategy"])["pnl"].cumsum()
    return df


def save_outputs(
    *,
    df: pd.DataFrame,
    output_dir: str | Path,
    save_png: bool,
    show: bool,
) -> tuple[Path, Path | None]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "trades.csv"
    df.to_csv(csv_path, index=False)

    png_path: Path | None = None
    if save_png:
        png_path = out_dir / "pnl.png"

        fig, (ax1, ax2, ax3) = plt.subplots(
            3,
            1,
            figsize=(12, 9),
            sharex=True,
            gridspec_kw={"height_ratios": [2, 1, 1]},
        )

        multi_ticker = df["ticker"].nunique() > 1
        multi_position = df["position"].nunique() > 1
        multi_strategy = df["strategy"].nunique() > 1

        def _label(ticker: str, position: str, strategy: str) -> str:
            base = f"{position} {strategy}"
            if multi_ticker:
                return f"{ticker} {base}"
            return base

        # Plot cumulative P&L for each (ticker, strategy).
        for (ticker, position, strategy), g in df.groupby(["ticker", "position", "strategy"]):
            ax1.plot(
                g["exit_date"],
                g["cum_pnl"],
                label=_label(ticker, position, strategy),
                linewidth=2,
            )
        ax1.axhline(0, color="black", linewidth=1, alpha=0.5)
        if multi_strategy:
            ax1.set_title("Overnight Strategies — Cumulative P&L")
        else:
            ax1.set_title("Overnight Strategy — Cumulative P&L")
        ax1.set_ylabel("Cumulative P&L ($)")
        ax1.grid(True, alpha=0.25)
        if multi_ticker or multi_strategy or multi_position:
            ax1.legend(loc="best")

        # Capital usage / free cash (how much account is being "put into" each trade).
        if "free_cash_after_entry" in df.columns and "capital_used_pct" in df.columns:
            for (ticker, position, strategy), g in df.groupby(
                ["ticker", "position", "strategy"]
            ):
                ax2.plot(
                    g["exit_date"],
                    g["free_cash_after_entry"],
                    linewidth=1.8,
                    label=_label(ticker, position, strategy),
                )
            ax2.axhline(0, color="black", linewidth=1, alpha=0.5)
            ax2.set_title("Free Cash After Entry (Equity − Capital Required)")
            ax2.set_ylabel("Free cash ($)")
            ax2.grid(True, alpha=0.25)
            if multi_ticker or multi_strategy or multi_position:
                ax2.legend(loc="best")
        else:
            ax2.text(
                0.5,
                0.5,
                "Capital usage columns missing",
                transform=ax2.transAxes,
                ha="center",
                va="center",
            )
            ax2.set_axis_off()

        # Trade-by-trade P&L markers (colored by strategy; aggregated across tickers).
        for (ticker, position, strategy), g in df.groupby(
            ["ticker", "position", "strategy"]
        ):
            ax3.scatter(
                g["exit_date"],
                g["pnl"],
                s=14,
                alpha=0.7,
                label=_label(ticker, position, strategy),
            )
        ax3.axhline(0, color="black", linewidth=1, alpha=0.5)
        ax3.set_title("Trade P&L (Close → Next Open)")
        ax3.set_ylabel("P&L ($)")
        ax3.grid(True, alpha=0.25)
        if multi_ticker or multi_strategy or multi_position:
            ax3.legend(loc="best")

        fig.tight_layout()
        fig.savefig(png_path, dpi=160)

    if show:
        plt.show()
    else:
        plt.close("all")

    return csv_path, png_path

