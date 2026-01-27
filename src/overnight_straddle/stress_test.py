from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from .black_scholes import black_scholes_quote
from .performance import add_equity_columns, build_performance_summary


@dataclass(frozen=True)
class StressScenario:
    name: str
    iv_shift_entry: float
    iv_shift_exit: float
    option_half_spread_pct: float
    extra_slippage_bps: float


def _compute_t_years(
    *,
    entry_day: date,
    exit_day: date,
    dte_days: int,
    include_weekends_in_time_decay: bool,
) -> tuple[float, float]:
    """
    Returns (t_entry, t_exit) as calendar-year fractions, matching the main backtest logic.
    """
    expiry_dt = datetime.combine(entry_day, time(16, 0)) + timedelta(days=int(dte_days))
    entry_dt = datetime.combine(entry_day, time(16, 0))
    exit_dt = datetime.combine(exit_day, time(9, 30))

    t_entry = max((expiry_dt - entry_dt).total_seconds(), 0.0) / (365.0 * 24.0 * 3600.0)
    t_exit = max((expiry_dt - exit_dt).total_seconds(), 0.0) / (365.0 * 24.0 * 3600.0)

    if not include_weekends_in_time_decay:
        # Fixed overnight of 17.5 hours (close -> next open).
        dt_years = (17.5 / 24.0) / 365.0
        t_exit = max(t_entry - dt_years, 0.0)

    return t_entry, t_exit


def _apply_half_spread(mid: float, *, side: str, half_spread_pct: float) -> float:
    """
    side: 'buy' or 'sell'
    """
    hs = max(float(half_spread_pct), 0.0)
    if side == "buy":
        return mid * (1.0 + hs)
    if side == "sell":
        return mid * (1.0 - hs)
    raise ValueError("side must be 'buy' or 'sell'")


def _clamp_vol(v: float) -> float:
    # Avoid degenerate/negative vols.
    return max(float(v), 0.01)


def run_stress_test(
    *,
    trades_df: pd.DataFrame,
    output_dir: str | Path,
    initial_balance: float,
    horizons_trading_days: dict[str, int],
    # Pricing / sizing config used by the run
    risk_free_rate: float,
    dte_days: int,
    include_weekends_in_time_decay: bool,
    contract_multiplier: int,
    contracts_per_trade: int,
    base_slippage_bps: float,
    # Stress config
    iv_shift_entry_points: list[float],
    iv_shift_exit_points: list[float],
    option_half_spread_pct: float,
    extra_slippage_bps: float,
    plot: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, Path | None]:
    """
    Re-price option trades under a grid of IV shocks and option half-spread.

    Returns:
      - scenario_performance_df
      - scenario_trades_df (one row per trade per scenario, subset of columns)
      - stress_plot_path (or None)
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Only option strategies; equity baseline is a separate instrument.
    df = trades_df.copy()
    df = df[df["strategy"].isin(["straddle", "strangle"])].copy()
    if df.empty:
        perf_empty = pd.DataFrame()
        trades_empty = pd.DataFrame()
        return perf_empty, trades_empty, None

    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.date

    scenarios: list[StressScenario] = []
    for ve in iv_shift_entry_points:
        for vx in iv_shift_exit_points:
            scenarios.append(
                StressScenario(
                    name=f"ivEntry{ve:+.2f}_ivExit{vx:+.2f}_hs{option_half_spread_pct:.3f}_xslip{extra_slippage_bps:.1f}",
                    iv_shift_entry=float(ve),
                    iv_shift_exit=float(vx),
                    option_half_spread_pct=float(option_half_spread_pct),
                    extra_slippage_bps=float(extra_slippage_bps),
                )
            )

    stress_rows: list[dict[str, object]] = []
    for sc in scenarios:
        for _, r in df.iterrows():
            entry_day: date = r["entry_date"]
            exit_day: date = r["exit_date"]
            t_entry, t_exit = _compute_t_years(
                entry_day=entry_day,
                exit_day=exit_day,
                dte_days=dte_days,
                include_weekends_in_time_decay=include_weekends_in_time_decay,
            )

            s_entry = float(r["entry_spot"])
            s_exit = float(r["exit_spot"])
            if not (math.isfinite(s_entry) and math.isfinite(s_exit)):
                continue

            call_k = float(r["call_strike"])
            put_k = float(r["put_strike"])
            base_vol = float(r["vol"]) if math.isfinite(float(r["vol"])) else 0.0
            vol_entry = _clamp_vol(base_vol + sc.iv_shift_entry)
            vol_exit = _clamp_vol(base_vol + sc.iv_shift_exit)

            position = str(r["position"]).lower()
            # Mid premiums under stressed vols.
            q_entry_call = black_scholes_quote(
                spot=s_entry,
                strike=call_k,
                time_to_expiry_years=t_entry,
                risk_free_rate=risk_free_rate,
                vol=vol_entry,
            )
            q_entry_put = black_scholes_quote(
                spot=s_entry,
                strike=put_k,
                time_to_expiry_years=t_entry,
                risk_free_rate=risk_free_rate,
                vol=vol_entry,
            )
            q_exit_call = black_scholes_quote(
                spot=s_exit,
                strike=call_k,
                time_to_expiry_years=t_exit,
                risk_free_rate=risk_free_rate,
                vol=vol_exit,
            )
            q_exit_put = black_scholes_quote(
                spot=s_exit,
                strike=put_k,
                time_to_expiry_years=t_exit,
                risk_free_rate=risk_free_rate,
                vol=vol_exit,
            )
            entry_mid = float(q_entry_call.call + q_entry_put.put)
            exit_mid = float(q_exit_call.call + q_exit_put.put)

            if position == "long":
                entry_px = _apply_half_spread(
                    entry_mid, side="buy", half_spread_pct=sc.option_half_spread_pct
                )
                exit_px = _apply_half_spread(
                    exit_mid, side="sell", half_spread_pct=sc.option_half_spread_pct
                )
            else:
                entry_px = _apply_half_spread(
                    entry_mid, side="sell", half_spread_pct=sc.option_half_spread_pct
                )
                exit_px = _apply_half_spread(
                    exit_mid, side="buy", half_spread_pct=sc.option_half_spread_pct
                )

            gross_entry = entry_px * contract_multiplier * contracts_per_trade
            gross_exit = exit_px * contract_multiplier * contracts_per_trade

            slip = (float(base_slippage_bps) + float(sc.extra_slippage_bps)) / 10_000.0
            if position == "long":
                entry_cashflow = -gross_entry * (1.0 + slip)
                exit_cashflow = gross_exit * (1.0 - slip)
            else:
                entry_cashflow = gross_entry * (1.0 - slip)
                exit_cashflow = -gross_exit * (1.0 + slip)

            pnl = entry_cashflow + exit_cashflow

            stress_rows.append(
                {
                    "scenario": sc.name,
                    "ticker": r["ticker"],
                    "strategy": r["strategy"],
                    "position": r["position"],
                    "entry_date": entry_day,
                    "exit_date": exit_day,
                    "pnl": pnl,
                }
            )

    stress_df = pd.DataFrame(stress_rows)
    if stress_df.empty:
        perf_empty = pd.DataFrame()
        trades_empty = pd.DataFrame()
        return perf_empty, trades_empty, None

    # Build equity curves per scenario + series.
    stress_df["exit_date"] = pd.to_datetime(stress_df["exit_date"])
    stress_df = stress_df.sort_values(["scenario", "ticker", "position", "strategy", "exit_date"])
    stress_df["cum_pnl"] = stress_df.groupby(
        ["scenario", "ticker", "position", "strategy"]
    )["pnl"].cumsum()
    stress_df["series_id"] = (
        stress_df["ticker"].astype(str)
        + "|"
        + stress_df["position"].astype(str)
        + "|"
        + stress_df["strategy"].astype(str)
    )

    # Compute equity/returns per (scenario, series_id).
    enriched_parts: list[pd.DataFrame] = []
    for scenario, g in stress_df.groupby("scenario"):
        g2 = add_equity_columns(g, initial_balance=float(initial_balance))
        g2["scenario"] = scenario
        enriched_parts.append(g2)
    stress_df2 = pd.concat(enriched_parts, ignore_index=True)

    perf_parts: list[pd.DataFrame] = []
    for scenario, g in stress_df2.groupby("scenario"):
        perf = build_performance_summary(g, horizons_trading_days=horizons_trading_days)
        perf["scenario"] = scenario
        perf_parts.append(perf)
    perf_df = pd.concat(perf_parts, ignore_index=True).sort_values(
        ["ticker", "position", "strategy", "scenario"]
    )

    trades_out = out_dir / "stress_trades.csv"
    perf_out = out_dir / "stress_performance.csv"
    stress_df2.to_csv(trades_out, index=False)
    perf_df.to_csv(perf_out, index=False)

    plot_path: Path | None = None
    if plot:
        plot_path = out_dir / "stress.png"

        # Summarize total return vs iv_exit shift for each (position, strategy).
        # We parse ivExit from scenario name (generated above).
        plot_df = perf_df.copy()
        plot_df["iv_exit"] = (
            plot_df["scenario"].str.extract(r"ivExit([+-]\d+\.\d+)").astype(float)
        )

        fig, ax = plt.subplots(figsize=(10, 5))
        for (pos, strat), g in plot_df.groupby(["position", "strategy"]):
            g = g.sort_values("iv_exit")
            ax.plot(
                g["iv_exit"],
                g["total_return_pct"] * 100.0,
                marker="o",
                linewidth=2,
                label=f"{pos} {strat}",
            )

        ax.axhline(0, color="black", linewidth=1, alpha=0.5)
        ax.set_title("Stress test: total return vs IV shift at exit")
        ax.set_xlabel("IV shift at exit (vol points)")
        ax.set_ylabel("Total return (%)")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="best")
        fig.tight_layout()
        fig.savefig(plot_path, dpi=160)
        plt.close(fig)

    return perf_df, stress_df2, plot_path

