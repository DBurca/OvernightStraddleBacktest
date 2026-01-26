from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from .black_scholes import black_scholes_quote


@dataclass(frozen=True)
class Trade:
    ticker: str
    strategy: str
    position: str
    entry_date: date
    exit_date: date
    entry_spot: float
    exit_spot: float
    call_strike: float
    put_strike: float
    entry_premium: float  # per 1 share (call@call_strike + put@put_strike)
    exit_premium: float  # per 1 share (call@call_strike + put@put_strike)
    vol: float
    pnl: float  # dollars, includes multiplier+contracts+slippage
    entry_cashflow: float  # dollars, + means cash received
    exit_cashflow: float  # dollars, + means cash received
    capital_required: float  # dollars, rough capital tied up at entry


def _round_to_increment(x: float, inc: float) -> float:
    if inc <= 0:
        return float(x)
    return round(float(x) / inc) * inc


def _estimate_vol(
    closes: pd.Series,
    i: int,
    *,
    method: Literal["rolling_realized", "fixed"],
    lookback_days: int,
    fixed_iv: float,
    trading_days_per_year: int = 252,
) -> float:
    if method == "fixed":
        return float(fixed_iv)

    # rolling realized vol from log returns
    lb = max(int(lookback_days), 2)
    start = max(i - lb, 0)
    window = closes.iloc[start : i + 1]
    rets = np.log(window / window.shift(1)).dropna()
    if len(rets) < 2:
        return float(fixed_iv)
    vol = float(rets.std(ddof=1) * math.sqrt(trading_days_per_year))
    if not math.isfinite(vol) or vol <= 0:
        return float(fixed_iv)
    return vol


def _overnight_dt_years(
    *,
    entry_day: date,
    exit_day: date,
    include_weekends: bool,
) -> float:
    """
    Approximate time between close (16:00) and next open (09:30).

    - include_weekends=True: counts the actual calendar gap (Fri→Mon is longer)
    - include_weekends=False: treats any next-trading-day hold as a single overnight
      (always 17.5 hours)
    """
    if not include_weekends:
        return (17.5 / 24.0) / 365.0

    close_dt = datetime.combine(entry_day, time(16, 0))
    open_dt = datetime.combine(exit_day, time(9, 30))
    dt = open_dt - close_dt
    return max(dt.total_seconds(), 0.0) / (365.0 * 24.0 * 3600.0)


def _backtest_overnight_combo(
    *,
    strategy_name: str,
    position: Literal["long", "short"],
    ticker: str,
    ohlc: pd.DataFrame,
    allow_weekend_holds: bool,
    include_weekends_in_time_decay: bool,
    dte_days: int,
    strike_rounding: float,
    call_otm_pct: float,
    put_otm_pct: float,
    short_margin_pct_underlying: float,
    risk_free_rate: float,
    vol_method: Literal["rolling_realized", "fixed"],
    vol_lookback_days: int,
    fixed_iv: float,
    contracts_per_trade: int,
    contract_multiplier: int,
    slippage_bps: float,
) -> list[Trade]:
    df = ohlc.copy()
    df["Date"] = pd.to_datetime(df.index).date
    dates: list[date] = list(df["Date"].values)
    closes = df["Close"].astype(float)
    opens = df["Open"].astype(float)

    trades: list[Trade] = []
    for i in range(len(df) - 1):
        entry_day = dates[i]
        exit_day = dates[i + 1]

        # Weekend holds: if disabled, skip when next trading day is more than 1 day away.
        if not allow_weekend_holds:
            if (exit_day - entry_day) > timedelta(days=1):
                continue

        s_entry = float(closes.iloc[i])
        s_exit = float(opens.iloc[i + 1])
        if not (math.isfinite(s_entry) and math.isfinite(s_exit)):
            continue
        if s_entry <= 0 or s_exit <= 0:
            continue

        call_strike = _round_to_increment(
            s_entry * (1.0 + float(call_otm_pct)), strike_rounding
        )
        put_strike = _round_to_increment(
            s_entry * (1.0 - float(put_otm_pct)), strike_rounding
        )
        if call_strike <= 0 or put_strike <= 0:
            continue
        if put_strike >= call_strike:
            # Ensure a proper strangle shape even with small rounding increments.
            # If they collide, nudge call up by one increment (or $0.01 fallback).
            bump = strike_rounding if strike_rounding > 0 else 0.01
            call_strike = put_strike + bump

        vol = _estimate_vol(
            closes,
            i,
            method=vol_method,
            lookback_days=vol_lookback_days,
            fixed_iv=fixed_iv,
        )

        # Assume an option expiry dte_days from entry close (calendar days).
        expiry_dt = datetime.combine(entry_day, time(16, 0)) + timedelta(
            days=int(dte_days)
        )

        entry_dt = datetime.combine(entry_day, time(16, 0))
        exit_dt = datetime.combine(exit_day, time(9, 30))

        # Time-to-expiry at entry/exit (calendar-year fraction).
        t_entry = max((expiry_dt - entry_dt).total_seconds(), 0.0) / (
            365.0 * 24.0 * 3600.0
        )
        t_exit = max((expiry_dt - exit_dt).total_seconds(), 0.0) / (
            365.0 * 24.0 * 3600.0
        )

        # If the user wants weekend time-decay excluded, reduce the overnight chunk.
        # This only affects how much time passes between entry and exit (theta),
        # not the selected expiry itself.
        if not include_weekends_in_time_decay:
            dt_years = _overnight_dt_years(
                entry_day=entry_day,
                exit_day=exit_day,
                include_weekends=False,
            )
            t_exit = max(t_entry - dt_years, 0.0)

        if t_entry <= 0.0 or t_exit < 0.0:
            continue

        q_entry_call = black_scholes_quote(
            spot=s_entry,
            strike=call_strike,
            time_to_expiry_years=t_entry,
            risk_free_rate=risk_free_rate,
            vol=vol,
        )
        q_entry_put = black_scholes_quote(
            spot=s_entry,
            strike=put_strike,
            time_to_expiry_years=t_entry,
            risk_free_rate=risk_free_rate,
            vol=vol,
        )

        q_exit_call = black_scholes_quote(
            spot=s_exit,
            strike=call_strike,
            time_to_expiry_years=t_exit,
            risk_free_rate=risk_free_rate,
            vol=vol,
        )
        q_exit_put = black_scholes_quote(
            spot=s_exit,
            strike=put_strike,
            time_to_expiry_years=t_exit,
            risk_free_rate=risk_free_rate,
            vol=vol,
        )

        prem_entry = float(q_entry_call.call + q_entry_put.put)
        prem_exit = float(q_exit_call.call + q_exit_put.put)

        gross_entry = prem_entry * contract_multiplier * contracts_per_trade
        gross_exit = prem_exit * contract_multiplier * contracts_per_trade

        slip = float(slippage_bps) / 10_000.0
        if position == "long":
            # Buy at close, sell at next open
            entry_cashflow = -gross_entry * (1.0 + slip)
            exit_cashflow = gross_exit * (1.0 - slip)
            capital_required = max(-entry_cashflow, 0.0)
        else:
            # Sell at close, buy back at next open
            entry_cashflow = gross_entry * (1.0 - slip)
            exit_cashflow = -gross_exit * (1.0 + slip)
            # Rough proxy for how much capital is "tied up" to hold short options.
            # Default is % of underlying notional (multiplier * contracts * spot).
            margin_pct = max(float(short_margin_pct_underlying), 0.0)
            underlying_notional = s_entry * contract_multiplier * contracts_per_trade
            capital_required = max(entry_cashflow, margin_pct * underlying_notional)

        pnl = entry_cashflow + exit_cashflow
        trades.append(
            Trade(
                ticker=ticker,
                strategy=strategy_name,
                position=position,
                entry_date=entry_day,
                exit_date=exit_day,
                entry_spot=s_entry,
                exit_spot=s_exit,
                call_strike=call_strike,
                put_strike=put_strike,
                entry_premium=prem_entry,
                exit_premium=prem_exit,
                vol=vol,
                pnl=pnl,
                entry_cashflow=entry_cashflow,
                exit_cashflow=exit_cashflow,
                capital_required=capital_required,
            )
        )

    return trades


def backtest_overnight_straddle(
    *,
    ticker: str,
    position: Literal["long", "short"],
    ohlc: pd.DataFrame,
    allow_weekend_holds: bool,
    include_weekends_in_time_decay: bool,
    dte_days: int,
    strike_rounding: float,
    short_margin_pct_underlying: float,
    risk_free_rate: float,
    vol_method: Literal["rolling_realized", "fixed"],
    vol_lookback_days: int,
    fixed_iv: float,
    contracts_per_trade: int,
    contract_multiplier: int,
    slippage_bps: float,
) -> list[Trade]:
    # Straddle = ATM call + ATM put (same strike).
    return _backtest_overnight_combo(
        strategy_name="straddle",
        position=position,
        ticker=ticker,
        ohlc=ohlc,
        allow_weekend_holds=allow_weekend_holds,
        include_weekends_in_time_decay=include_weekends_in_time_decay,
        dte_days=dte_days,
        strike_rounding=strike_rounding,
        call_otm_pct=0.0,
        put_otm_pct=0.0,
        short_margin_pct_underlying=short_margin_pct_underlying,
        risk_free_rate=risk_free_rate,
        vol_method=vol_method,
        vol_lookback_days=vol_lookback_days,
        fixed_iv=fixed_iv,
        contracts_per_trade=contracts_per_trade,
        contract_multiplier=contract_multiplier,
        slippage_bps=slippage_bps,
    )


def backtest_overnight_strangle(
    *,
    ticker: str,
    position: Literal["long", "short"],
    ohlc: pd.DataFrame,
    allow_weekend_holds: bool,
    include_weekends_in_time_decay: bool,
    dte_days: int,
    strike_rounding: float,
    call_otm_pct: float,
    put_otm_pct: float,
    short_margin_pct_underlying: float,
    risk_free_rate: float,
    vol_method: Literal["rolling_realized", "fixed"],
    vol_lookback_days: int,
    fixed_iv: float,
    contracts_per_trade: int,
    contract_multiplier: int,
    slippage_bps: float,
) -> list[Trade]:
    return _backtest_overnight_combo(
        strategy_name="strangle",
        position=position,
        ticker=ticker,
        ohlc=ohlc,
        allow_weekend_holds=allow_weekend_holds,
        include_weekends_in_time_decay=include_weekends_in_time_decay,
        dte_days=dte_days,
        strike_rounding=strike_rounding,
        call_otm_pct=call_otm_pct,
        put_otm_pct=put_otm_pct,
        short_margin_pct_underlying=short_margin_pct_underlying,
        risk_free_rate=risk_free_rate,
        vol_method=vol_method,
        vol_lookback_days=vol_lookback_days,
        fixed_iv=fixed_iv,
        contracts_per_trade=contracts_per_trade,
        contract_multiplier=contract_multiplier,
        slippage_bps=slippage_bps,
    )


def backtest_overnight_equity(
    *,
    ticker: str,
    ohlc: pd.DataFrame,
    allow_weekend_holds: bool,
    shares_per_trade: int,
    slippage_bps: float,
) -> list[Trade]:
    """
    Equity baseline: buy at close, sell at next open.
    """
    df = ohlc.copy()
    df["Date"] = pd.to_datetime(df.index).date
    dates: list[date] = list(df["Date"].values)
    closes = df["Close"].astype(float)
    opens = df["Open"].astype(float)

    n_shares = int(shares_per_trade)
    if n_shares <= 0:
        raise ValueError("shares_per_trade must be positive")

    slip = float(slippage_bps) / 10_000.0

    trades: list[Trade] = []
    for i in range(len(df) - 1):
        entry_day = dates[i]
        exit_day = dates[i + 1]

        if not allow_weekend_holds:
            if (exit_day - entry_day) > timedelta(days=1):
                continue

        s_entry = float(closes.iloc[i])
        s_exit = float(opens.iloc[i + 1])
        if not (math.isfinite(s_entry) and math.isfinite(s_exit)):
            continue
        if s_entry <= 0 or s_exit <= 0:
            continue

        gross_entry = s_entry * n_shares
        gross_exit = s_exit * n_shares

        entry_cashflow = -gross_entry * (1.0 + slip)
        exit_cashflow = gross_exit * (1.0 - slip)
        pnl = entry_cashflow + exit_cashflow

        trades.append(
            Trade(
                ticker=ticker,
                strategy="equity",
                position="long",
                entry_date=entry_day,
                exit_date=exit_day,
                entry_spot=s_entry,
                exit_spot=s_exit,
                call_strike=float("nan"),
                put_strike=float("nan"),
                entry_premium=0.0,
                exit_premium=0.0,
                vol=float("nan"),
                pnl=pnl,
                entry_cashflow=entry_cashflow,
                exit_cashflow=exit_cashflow,
                capital_required=max(-entry_cashflow, 0.0),
            )
        )

    return trades

