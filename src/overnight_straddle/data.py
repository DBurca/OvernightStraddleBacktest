from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class PriceHistory:
    ticker: str
    df: pd.DataFrame  # index: DatetimeIndex (date), columns: Open, High, Low, Close


def _normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    # yfinance sometimes returns columns like ("Open", "AAPL") for multi-ticker pulls.
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] for c in df.columns]

    required = ["Open", "High", "Low", "Close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")

    out = df[required].copy()
    out = out.dropna()
    out.index = pd.to_datetime(out.index).tz_localize(None)
    out = out.sort_index()
    return out


def load_price_history(
    *,
    ticker: str,
    source: Literal["yfinance", "csv"],
    interval: str,
    lookback_trading_days: int,
    end_date: str | None,
    csv_folder: str | Path,
) -> PriceHistory:
    t = ticker.upper().strip()
    if source == "yfinance":
        import yfinance as yf

        # For daily bars, asking for a few extra days helps ensure enough
        # trading days even with holidays/weekends.
        period_days = int(max(lookback_trading_days * 3, 30))
        kwargs = {"interval": interval, "auto_adjust": False, "progress": False}
        if end_date:
            # yfinance end is exclusive; add 1 day buffer later.
            df = yf.download(t, period=f"{period_days}d", end=end_date, **kwargs)
        else:
            df = yf.download(t, period=f"{period_days}d", **kwargs)

        df = _normalize_ohlc(df)

    elif source == "csv":
        p = Path(csv_folder) / f"{t}.csv"
        df = pd.read_csv(p)
        if "Date" not in df.columns:
            raise ValueError(f"CSV {p} must contain a Date column")
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
        df = _normalize_ohlc(df)
    else:
        raise ValueError(f"unknown source: {source}")

    if len(df) < 5:
        raise ValueError(f"not enough data for {t} (rows={len(df)})")

    # Keep last N trading days.
    df = df.tail(int(lookback_trading_days))
    return PriceHistory(ticker=t, df=df)


def next_trading_day(dates: list[date], i: int) -> date | None:
    if i + 1 >= len(dates):
        return None
    return dates[i + 1]

