from __future__ import annotations

import argparse
import csv
import json
import os
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from .config import load_config


def _round_to_increment(x: float, inc: float) -> float:
    if inc <= 0:
        return float(x)
    return round(float(x) / inc) * inc


def _norm_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("empty price data")
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] for c in df.columns]
    out = df[["Open", "Close"]].copy()
    out = out.dropna()
    out.index = pd.to_datetime(out.index).tz_localize(None)
    out = out.sort_index()
    return out


def _apply_half_spread(mid: float, *, side: Literal["buy", "sell"], half_spread: float) -> float:
    hs = max(float(half_spread), 0.0)
    return mid * (1.0 + hs) if side == "buy" else mid * (1.0 - hs)


def _parse_hhmm(s: str) -> time:
    hh, mm = s.strip().split(":")
    return time(hour=int(hh), minute=int(mm))


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def _next_weekday(d: date) -> date:
    nd = d + timedelta(days=1)
    while not _is_weekday(nd):
        nd += timedelta(days=1)
    return nd


def _post_discord(webhook_url: str, content: str) -> None:
    # Use stdlib to avoid extra deps.
    import urllib.request

    payload = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        resp.read()


@dataclass
class OpenPosition:
    ticker: str
    entry_date: str  # YYYY-MM-DD
    expiration: str  # YYYY-MM-DD
    strike: float
    entry_spot: float
    entry_call_mid: float
    entry_put_mid: float
    entry_call_fill: float
    entry_put_fill: float


def _state_path(out_dir: Path) -> Path:
    return out_dir / "realtime_state.json"


def _trades_path(out_dir: Path) -> Path:
    return out_dir / "realtime_trades.csv"


def _load_state(out_dir: Path) -> dict[str, Any]:
    p = _state_path(out_dir)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_state(out_dir: Path, state: dict[str, Any]) -> None:
    p = _state_path(out_dir)
    p.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _append_trade_row(out_dir: Path, row: dict[str, Any]) -> None:
    p = _trades_path(out_dir)
    is_new = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(row)


def _fetch_daily_ohlc(ticker: str, lookback_days: int) -> pd.DataFrame:
    import yfinance as yf

    # Pull extra days for weekends/holidays.
    period_days = max(int(lookback_days * 3), 60)
    df = yf.download(ticker, period=f"{period_days}d", interval="1d", progress=False)
    df = _norm_ohlc(df)
    return df.tail(max(int(lookback_days), 10))


def _pick_expiration(ticker_obj: Any, *, target_date: date) -> str:
    expirations = list(getattr(ticker_obj, "options", []) or [])
    if not expirations:
        raise RuntimeError("No option expirations returned by yfinance")
    exp_dates: list[tuple[date, str]] = []
    for s in expirations:
        try:
            exp_dates.append((date.fromisoformat(s), s))
        except Exception:
            continue
    exp_dates.sort(key=lambda x: x[0])
    for d, s in exp_dates:
        if d >= target_date:
            return s
    return exp_dates[-1][1]


def _get_underlying_price(ticker_obj: Any) -> float:
    try:
        fi = getattr(ticker_obj, "fast_info", None)
        if fi and "last_price" in fi and fi["last_price"]:
            return float(fi["last_price"])
    except Exception:
        pass
    try:
        info = getattr(ticker_obj, "info", {}) or {}
        if "regularMarketPrice" in info and info["regularMarketPrice"]:
            return float(info["regularMarketPrice"])
    except Exception:
        pass
    raise RuntimeError("Unable to fetch underlying price")


def _select_strike(chain_df: pd.DataFrame, *, target_strike: float) -> float:
    strikes = chain_df["strike"].astype(float).values
    if len(strikes) == 0:
        raise RuntimeError("Empty option chain")
    idx = int(np.argmin(np.abs(strikes - float(target_strike))))
    return float(strikes[idx])


def _get_quote_row(df: pd.DataFrame, strike: float) -> pd.Series:
    row = df.loc[df["strike"].astype(float) == float(strike)]
    if row.empty:
        raise RuntimeError(f"Strike {strike} not found in chain")
    return row.iloc[0]


def _mid_from_row(row: pd.Series) -> float:
    bid = row.get("bid", None)
    ask = row.get("ask", None)
    try:
        bid_f = float(bid)
        ask_f = float(ask)
        if bid_f > 0 and ask_f > 0:
            return 0.5 * (bid_f + ask_f)
    except Exception:
        pass
    last = row.get("lastPrice", None)
    return float(last) if last is not None else float("nan")


def _fill_from_row(
    row: pd.Series, *, side: Literal["buy", "sell"], fallback_half_spread: float
) -> float:
    bid = row.get("bid", None)
    ask = row.get("ask", None)
    try:
        bid_f = float(bid)
        ask_f = float(ask)
        if bid_f > 0 and ask_f > 0:
            return bid_f if side == "sell" else ask_f
    except Exception:
        pass
    mid = _mid_from_row(row)
    if not np.isfinite(mid) or mid <= 0:
        raise RuntimeError("No usable option price (bid/ask/last missing)")
    return _apply_half_spread(mid, side=side, half_spread=fallback_half_spread)


def _price_straddle_mid(
    *,
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    vol: float,
) -> float:
    raise RuntimeError("Black–Scholes pricing is disabled in the realtime runner.")


def _time_to_expiry_years(entry_day: date, *, dte_days: int, at: Literal["entry", "exit"]) -> float:
    raise RuntimeError("Black–Scholes time-to-expiry is unused in the realtime runner.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Realtime (paper) short straddle runner")
    p.add_argument("--config", default="realtime_config.yaml")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)

    ticker = str(cfg.get("ticker", "")).strip().upper()
    if not ticker:
        raise SystemExit("realtime config: ticker is required")

    out_dir = Path(cfg.get("outputs.output_dir", "outputs"))
    out_dir.mkdir(parents=True, exist_ok=True)

    tz = ZoneInfo(str(cfg.get("schedule.timezone", "America/New_York")))
    entry_time = _parse_hhmm(str(cfg.get("schedule.entry_time", "15:55")))
    exit_time = _parse_hhmm(str(cfg.get("schedule.exit_time", "09:31")))
    poll_seconds = int(cfg.get("schedule.poll_seconds", 30))
    allow_weekend_holds = bool(cfg.get("schedule.allow_weekend_holds", True))

    initial_balance = float(cfg.get("capital.initial_balance", 50_000))
    contracts = int(cfg.get("strategy.contracts", 1))
    multiplier = int(cfg.get("strategy.contract_multiplier", 100))
    strike_rounding = float(cfg.get("strategy.strike_rounding", 1.0))
    dte_days = int(cfg.get("strategy.dte_days", 7))

    slippage_bps = float(cfg.get("strategy.costs.slippage_bps", 0.0))
    half_spread = float(cfg.get("strategy.costs.option_half_spread_pct", 0.01))

    discord_enabled = bool(cfg.get("discord.enabled", True))
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

    state = _load_state(out_dir)
    equity = float(state.get("equity", initial_balance))
    open_pos = state.get("open_position", None)

    print(f"Realtime runner started for {ticker}. Equity=${equity:,.2f}")
    if discord_enabled and not webhook_url:
        print("DISCORD_WEBHOOK_URL is not set; webhook messages are disabled.")
        discord_enabled = False

    while True:
        now = datetime.now(tz)
        today = now.date()

        # Skip weekends completely.
        if not _is_weekday(today):
            time_module.sleep(poll_seconds)
            continue

        # If weekend holds are disabled, do not open on Friday (would exit Monday).
        if not allow_weekend_holds and today.weekday() == 4:
            # Still allow closing if somehow open.
            if open_pos is None:
                time_module.sleep(poll_seconds)
                continue

        # Exit logic (next day open window).
        if open_pos is not None:
            entry_day = date.fromisoformat(open_pos["entry_date"])
            next_day = _next_weekday(entry_day)
            if today == next_day and now.time() >= exit_time:
                import yfinance as yf

                tkr = yf.Ticker(ticker)
                exit_spot = _get_underlying_price(tkr)
                expiration = str(open_pos["expiration"])
                strike = float(open_pos["strike"])

                chain = tkr.option_chain(expiration)
                calls = chain.calls.copy()
                puts = chain.puts.copy()

                call_row = _get_quote_row(calls, strike)
                put_row = _get_quote_row(puts, strike)

                exit_call_mid = _mid_from_row(call_row)
                exit_put_mid = _mid_from_row(put_row)
                exit_call_fill = _fill_from_row(
                    call_row, side="buy", fallback_half_spread=half_spread
                )
                exit_put_fill = _fill_from_row(
                    put_row, side="buy", fallback_half_spread=half_spread
                )

                gross_entry = (
                    float(open_pos["entry_call_fill"]) + float(open_pos["entry_put_fill"])
                ) * multiplier * contracts
                gross_exit = (float(exit_call_fill) + float(exit_put_fill)) * multiplier * contracts

                slip = slippage_bps / 10_000.0
                entry_cashflow = gross_entry * (1.0 - slip)  # short: credit
                exit_cashflow = -gross_exit * (1.0 + slip)  # short: debit
                pnl = entry_cashflow + exit_cashflow
                equity += pnl

                row = {
                    "ticker": ticker,
                    "strategy": "short_straddle",
                    "entry_date": open_pos["entry_date"],
                    "exit_date": today.isoformat(),
                    "expiration": expiration,
                    "strike": strike,
                    "entry_spot": float(open_pos["entry_spot"]),
                    "exit_spot": exit_spot,
                    "entry_call_mid": float(open_pos["entry_call_mid"]),
                    "entry_put_mid": float(open_pos["entry_put_mid"]),
                    "entry_call_fill": float(open_pos["entry_call_fill"]),
                    "entry_put_fill": float(open_pos["entry_put_fill"]),
                    "exit_call_mid": float(exit_call_mid),
                    "exit_put_mid": float(exit_put_mid),
                    "exit_call_fill": float(exit_call_fill),
                    "exit_put_fill": float(exit_put_fill),
                    "contracts": contracts,
                    "multiplier": multiplier,
                    "pnl": pnl,
                    "equity": equity,
                }
                _append_trade_row(out_dir, row)

                msg = (
                    f"{ticker} short straddle closed.\n"
                    f"Entry: {open_pos['entry_date']} exp {expiration} @ spot {float(open_pos['entry_spot']):.2f}, K={strike:.2f}\n"
                    f"Exit:  {today.isoformat()} @ spot {exit_spot:.2f}\n"
                    f"Trade P&L: ${pnl:,.2f}\n"
                    f"Equity: ${equity:,.2f}"
                )
                print(msg)
                if discord_enabled:
                    try:
                        _post_discord(webhook_url, msg)
                    except Exception as e:
                        print(f"Webhook post failed: {e}")

                open_pos = None
                state = {"equity": equity, "open_position": None}
                _save_state(out_dir, state)

        # Entry logic (near close).
        if open_pos is None and now.time() >= entry_time:
            import yfinance as yf

            tkr = yf.Ticker(ticker)
            entry_spot = _get_underlying_price(tkr)

            target_exp = today + timedelta(days=int(dte_days))
            expiration = _pick_expiration(tkr, target_date=target_exp)
            chain = tkr.option_chain(expiration)
            calls = chain.calls.copy()
            puts = chain.puts.copy()

            target_strike = _round_to_increment(entry_spot, strike_rounding)
            strike = _select_strike(calls, target_strike=target_strike)

            call_row = _get_quote_row(calls, strike)
            put_row = _get_quote_row(puts, strike)

            entry_call_mid = _mid_from_row(call_row)
            entry_put_mid = _mid_from_row(put_row)
            entry_call_fill = _fill_from_row(
                call_row, side="sell", fallback_half_spread=half_spread
            )
            entry_put_fill = _fill_from_row(
                put_row, side="sell", fallback_half_spread=half_spread
            )

            # Simple capital check: do not allow more than equity.
            # For short options, true broker margin differs; this is only a guardrail.
            est_margin = 0.20 * entry_spot * multiplier * contracts
            if est_margin > equity:
                print(
                    f"Skipping entry (estimated margin ${est_margin:,.0f} > equity ${equity:,.0f})."
                )
                time_module.sleep(poll_seconds)
                continue

            open_pos = {
                "ticker": ticker,
                "entry_date": today.isoformat(),
                "expiration": expiration,
                "strike": float(strike),
                "entry_spot": float(entry_spot),
                "entry_call_mid": float(entry_call_mid),
                "entry_put_mid": float(entry_put_mid),
                "entry_call_fill": float(entry_call_fill),
                "entry_put_fill": float(entry_put_fill),
            }
            state = {"equity": equity, "open_position": open_pos}
            _save_state(out_dir, state)

            print(
                f"{ticker} short straddle opened.\n"
                f"Entry: {today.isoformat()} exp {expiration} @ spot {entry_spot:.2f}, K={strike:.2f}\n"
                f"Call (mid/fill): {entry_call_mid:.4f} / {entry_call_fill:.4f}\n"
                f"Put  (mid/fill): {entry_put_mid:.4f} / {entry_put_fill:.4f}\n"
                f"Equity: ${equity:,.2f}"
            )

        time_module.sleep(poll_seconds)


if __name__ == "__main__":
    main()

