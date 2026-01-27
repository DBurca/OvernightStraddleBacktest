from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .backtest import (
    backtest_buy_and_hold,
    backtest_overnight_equity,
    backtest_overnight_straddle,
    backtest_overnight_strangle,
)
from .config import load_config
from .data import load_price_history
from .performance import add_equity_columns, build_performance_summary, save_performance_summary
from .plotting import save_outputs, trades_to_frame
from .stress_test import run_stress_test


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Overnight long straddle backtest")
    p.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config (default: config.yaml)",
    )
    p.add_argument(
        "--no-show",
        action="store_true",
        help="Do not pop up matplotlib window (still saves PNG if enabled)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)

    tickers = cfg.tickers
    if not tickers:
        raise SystemExit("config tickers is empty")

    source = str(cfg.get("data.source", "yfinance"))
    interval = str(cfg.get("data.interval", "1d"))
    csv_folder = str(cfg.get("data.csv_folder", "data"))

    lookback = int(cfg.get("backtest.lookback_trading_days", 252))
    end_date = cfg.get("backtest.end_date", None)
    end_date = str(end_date) if end_date else None

    allow_weekend_holds = bool(cfg.get("calendar.allow_weekend_holds", True))

    strategies = cfg.get("run.strategies", ["straddle", "strangle"])
    if not isinstance(strategies, list) or not all(isinstance(x, str) for x in strategies):
        raise SystemExit("config run.strategies must be a list of strings")
    strategies = [s.strip().lower() for s in strategies if s.strip()]
    if not strategies:
        raise SystemExit("config run.strategies is empty")

    position = str(cfg.get("run.position", "short")).strip().lower()
    if position not in {"long", "short"}:
        raise SystemExit("config run.position must be 'long' or 'short'")

    include_equity = bool(cfg.get("run.include_equity_baseline", True))
    include_buy_and_hold = bool(cfg.get("run.include_buy_and_hold", True))

    dte_days = int(cfg.get("strategy.dte_days", 7))
    strike_rounding = float(cfg.get("strategy.strike_rounding", 1.0))
    call_otm_pct = float(cfg.get("strategy.strangle.call_otm_pct", 0.02))
    put_otm_pct = float(cfg.get("strategy.strangle.put_otm_pct", 0.02))
    contract_multiplier = int(cfg.get("strategy.contract_multiplier", 100))
    contracts_per_trade = int(cfg.get("strategy.contracts_per_trade", 1))
    risk_free_rate = float(cfg.get("strategy.rates.risk_free_rate", 0.04))

    vol_method = str(cfg.get("strategy.vol.method", "rolling_realized"))
    vol_lookback_days = int(cfg.get("strategy.vol.lookback_days", 30))
    fixed_iv = float(cfg.get("strategy.vol.fixed_iv", 0.40))

    slippage_bps = float(cfg.get("strategy.costs.slippage_bps", 0.0))
    shares_per_trade = int(cfg.get("equity.shares_per_trade", 100))
    short_margin_pct_underlying = float(cfg.get("margin.short_options_pct_underlying", 0.20))

    include_weekends_in_time_decay = bool(
        cfg.get("strategy.include_weekends_in_time_decay", True)
    )

    initial_balance = float(cfg.get("performance.initial_balance", 100_000))
    horizons = cfg.get(
        "performance.horizons_trading_days",
        {"1d": 1, "1w": 5, "1m": 21, "1y": 252},
    )
    if not isinstance(horizons, dict):
        raise SystemExit("config performance.horizons_trading_days must be a mapping")
    horizons = {str(k): int(v) for k, v in horizons.items()}

    all_trades = []
    for t in tickers:
        ph = load_price_history(
            ticker=t,
            source=source,  # type: ignore[arg-type]
            interval=interval,
            lookback_trading_days=lookback,
            end_date=end_date,
            csv_folder=csv_folder,
        )

        for strat in strategies:
            if strat == "straddle":
                trades = backtest_overnight_straddle(
                    ticker=ph.ticker,
                    position=position,  # type: ignore[arg-type]
                    ohlc=ph.df,
                    allow_weekend_holds=allow_weekend_holds,
                    include_weekends_in_time_decay=include_weekends_in_time_decay,
                    dte_days=dte_days,
                    strike_rounding=strike_rounding,
                    short_margin_pct_underlying=short_margin_pct_underlying,
                    risk_free_rate=risk_free_rate,
                    vol_method=vol_method,  # type: ignore[arg-type]
                    vol_lookback_days=vol_lookback_days,
                    fixed_iv=fixed_iv,
                    contracts_per_trade=contracts_per_trade,
                    contract_multiplier=contract_multiplier,
                    slippage_bps=slippage_bps,
                )
            elif strat == "strangle":
                trades = backtest_overnight_strangle(
                    ticker=ph.ticker,
                    position=position,  # type: ignore[arg-type]
                    ohlc=ph.df,
                    allow_weekend_holds=allow_weekend_holds,
                    include_weekends_in_time_decay=include_weekends_in_time_decay,
                    dte_days=dte_days,
                    strike_rounding=strike_rounding,
                    call_otm_pct=call_otm_pct,
                    put_otm_pct=put_otm_pct,
                    short_margin_pct_underlying=short_margin_pct_underlying,
                    risk_free_rate=risk_free_rate,
                    vol_method=vol_method,  # type: ignore[arg-type]
                    vol_lookback_days=vol_lookback_days,
                    fixed_iv=fixed_iv,
                    contracts_per_trade=contracts_per_trade,
                    contract_multiplier=contract_multiplier,
                    slippage_bps=slippage_bps,
                )
            elif strat == "equity":
                trades = backtest_overnight_equity(
                    ticker=ph.ticker,
                    ohlc=ph.df,
                    allow_weekend_holds=allow_weekend_holds,
                    shares_per_trade=shares_per_trade,
                    slippage_bps=slippage_bps,
                )
            else:
                raise SystemExit(
                    f"Unknown strategy '{strat}'. Supported: straddle, strangle, equity"
                )

            all_trades.extend(trades)

        if include_equity and ("equity" not in strategies):
            all_trades.extend(
                backtest_overnight_equity(
                    ticker=ph.ticker,
                    ohlc=ph.df,
                    allow_weekend_holds=allow_weekend_holds,
                    shares_per_trade=shares_per_trade,
                    slippage_bps=slippage_bps,
                )
            )

        if include_buy_and_hold:
            all_trades.extend(
                backtest_buy_and_hold(
                    ticker=ph.ticker,
                    ohlc=ph.df,
                    initial_capital=initial_balance,
                    slippage_bps=slippage_bps,
                )
            )

    df = trades_to_frame(all_trades)
    if df.empty:
        raise SystemExit("No trades produced (check date range / data availability).")

    df = add_equity_columns(df, initial_balance=initial_balance)

    output_dir = str(cfg.get("plot.output_dir", "outputs"))
    out_dir = Path(args.config).parent / output_dir
    save_png = bool(cfg.get("plot.save_png", True))
    show = bool(cfg.get("plot.show", True)) and (not bool(args.no_show))

    csv_path, png_path = save_outputs(
        df=df,
        output_dir=out_dir,
        save_png=save_png,
        show=show,
    )

    summary = build_performance_summary(df, horizons_trading_days=horizons)
    perf_path = save_performance_summary(summary, output_dir=out_dir)

    print(f"Wrote {len(df)} trades to: {csv_path}")
    print(f"Wrote performance summary to: {perf_path}")
    if not summary.empty:
        cols = [
            "ticker",
            "position",
            "strategy",
            "trades",
            "total_return_pct",
        ] + [f"return_{k}_pct" for k in horizons.keys()]
        cols = [c for c in cols if c in summary.columns]
        print()
        print("Performance (percent):")
        display = summary[cols].copy()
        pct_cols = [c for c in display.columns if c.endswith("_pct")]
        for c in pct_cols:
            display[c] = (pd.to_numeric(display[c], errors="coerce") * 100.0).round(2)
        print(display.to_string(index=False))
    if png_path:
        print(f"Wrote plot to: {png_path}")

    # Stress test (options only): IV shocks + option bid/ask half-spread.
    if bool(cfg.get("stress_test.enabled", False)):
        iv_entry = cfg.get("stress_test.iv_shift_entry_points", [0.0])
        iv_exit = cfg.get("stress_test.iv_shift_exit_points", [0.0])
        if not isinstance(iv_entry, list) or not isinstance(iv_exit, list):
            raise SystemExit("stress_test.iv_shift_* must be lists")

        option_half_spread_pct = float(cfg.get("stress_test.option_half_spread_pct", 0.0))
        extra_slippage_bps = float(cfg.get("stress_test.extra_slippage_bps", 0.0))
        stress_plot = bool(cfg.get("stress_test.plot", True))

        stress_perf, _, stress_plot_path = run_stress_test(
            trades_df=df,
            output_dir=out_dir,
            initial_balance=initial_balance,
            horizons_trading_days=horizons,
            risk_free_rate=risk_free_rate,
            dte_days=dte_days,
            include_weekends_in_time_decay=include_weekends_in_time_decay,
            contract_multiplier=contract_multiplier,
            contracts_per_trade=contracts_per_trade,
            base_slippage_bps=slippage_bps,
            iv_shift_entry_points=[float(x) for x in iv_entry],
            iv_shift_exit_points=[float(x) for x in iv_exit],
            option_half_spread_pct=option_half_spread_pct,
            extra_slippage_bps=extra_slippage_bps,
            plot=stress_plot,
        )

        if not stress_perf.empty:
            print()
            print("Stress test outputs:")
            print(f"- outputs/stress_performance.csv")
            print(f"- outputs/stress_trades.csv")
            if stress_plot_path:
                print(f"- outputs/stress.png")


if __name__ == "__main__":
    main()

