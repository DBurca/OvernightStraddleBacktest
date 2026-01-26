from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


def _series_id(df: pd.DataFrame) -> pd.Series:
    return (
        df["ticker"].astype(str)
        + "|"
        + df["position"].astype(str)
        + "|"
        + df["strategy"].astype(str)
    )


def add_equity_columns(df: pd.DataFrame, *, initial_balance: float) -> pd.DataFrame:
    """
    Adds account-level equity/return columns per (ticker, position, strategy).

    Assumes `df` already contains:
      - exit_date (datetime)
      - pnl (dollars)
      - cum_pnl (dollars)
      - ticker, position, strategy
    """
    out = df.copy()
    out["series_id"] = _series_id(out)
    out["initial_balance"] = float(initial_balance)
    out["equity"] = float(initial_balance) + out["cum_pnl"]
    out["return_pct"] = out["equity"] / float(initial_balance) - 1.0

    # Per-trade % return vs previous equity (start at initial balance).
    out["prev_equity"] = (
        out.groupby("series_id")["equity"].shift(1).fillna(float(initial_balance))
    )
    out["trade_return_pct"] = out["pnl"] / out["prev_equity"]

    # Capital usage / "cash tied up" (requires capital_required from trades).
    if "capital_required" in out.columns:
        out["equity_before_entry"] = out["prev_equity"]
        out["capital_used_pct"] = out["capital_required"] / out["equity_before_entry"]
        out["free_cash_after_entry"] = out["equity_before_entry"] - out["capital_required"]

    return out


def trailing_return_pct(
    equity: pd.Series, *, trading_days: int
) -> float | None:
    if trading_days <= 0:
        return None
    if len(equity) <= trading_days:
        return None
    last = float(equity.iloc[-1])
    prev = float(equity.iloc[-(trading_days + 1)])
    if prev == 0:
        return None
    return last / prev - 1.0


def build_performance_summary(
    df: pd.DataFrame,
    *,
    horizons_trading_days: dict[str, int],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for series_id, g in df.groupby("series_id"):
        g = g.sort_values("exit_date")
        equity = g["equity"]
        initial_balance = float(g["initial_balance"].iloc[0])
        row: dict[str, object] = {
            "series_id": series_id,
            "ticker": g["ticker"].iloc[0],
            "position": g["position"].iloc[0],
            "strategy": g["strategy"].iloc[0],
            "start_date": g["exit_date"].iloc[0].date(),
            "end_date": g["exit_date"].iloc[-1].date(),
            "trades": int(len(g)),
            "initial_balance": initial_balance,
            "ending_equity": float(equity.iloc[-1]),
            "total_return_pct": float(equity.iloc[-1] / initial_balance - 1.0),
        }

        for name, n in horizons_trading_days.items():
            row[f"return_{name}_pct"] = trailing_return_pct(equity, trading_days=int(n))

        rows.append(row)

    return pd.DataFrame(rows).sort_values(["ticker", "position", "strategy"])


def save_performance_summary(
    summary: pd.DataFrame, *, output_dir: str | Path
) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "performance.csv"
    summary.to_csv(path, index=False)
    return path

