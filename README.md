# Overnight options backtest (close → next open)

This folder contains a backtest that compares three overnight positions:

- Straddle: call + put at the same (ATM) strike
- Strangle: OTM call + OTM put at different strikes
- Equity baseline: long shares from close to next open

For each trading day, the backtest enters at the close and exits at the next open.

## What gets plotted and written

Outputs go to `outputs/`:

- `pnl.png`: three panels
  - Cumulative P&L in dollars
  - Free cash after entry (account equity minus capital required)
  - Trade-by-trade P&L in dollars
- `trades.csv`: one row per trade per strategy
- `performance.csv`: trailing percent returns (1d, 1m, 1y, etc.) per strategy line

## Pricing model (options)

Historical option prices are not pulled from an options data feed. Instead, the backtest prices the call and put using Black–Scholes (European, no dividends) with volatility coming from either:

- Rolling realized volatility from the underlying’s daily closes, or
- A fixed implied volatility from the config

This means the option P&L is model-based and will differ from live fills and real option markets.

## Running

From `OvernightStraddleBacktest/`:

Show the plot window:

```bash
PYTHONPATH=src python3 -m overnight_straddle.main --config config.yaml
```

Run without opening a plot window (still writes PNG and CSVs):

```bash
MPLBACKEND=Agg PYTHONPATH=src python3 -m overnight_straddle.main --config config.yaml --no-show
```

## `config.yaml` reference

### `tickers`
- List of tickers to backtest, e.g. `["SPY"]`. Each ticker is run independently.

### `data`
- `source`: `yfinance` or `csv`
- `interval`: only `1d` is expected for this strategy
- `csv_folder`: used only if `source=csv`. Expects `data/<TICKER>.csv` with columns `Date,Open,High,Low,Close`.

### `backtest`
- `lookback_trading_days`: number of most recent trading days to include
- `end_date`: optional end date (YYYY-MM-DD). `null` means use the most recent data.

### `run`
- `strategies`: any of `straddle`, `strangle` (the equity baseline is controlled separately)
- `position`:
  - `long`: buy at close, sell at next open
  - `short`: sell at close, buy back at next open
- `include_equity_baseline`: if true, also runs `long equity` (close → next open)

### `calendar`
- `allow_weekend_holds`:
  - `true`: includes Friday close → Monday open
  - `false`: skips trades where the next trading day is more than 1 calendar day away

### `strategy`
- `dte_days`: assumed calendar days to expiry for the option used at entry
- `include_weekends_in_time_decay`:
  - `true`: Friday close → Monday open uses the larger calendar time gap for theta
  - `false`: treats any close → next open as a fixed overnight duration for theta
- `strike_rounding`: rounds strikes to the nearest increment (e.g. 1.0 means $1 strikes)
- `strangle.call_otm_pct`: call strike set to \(S \cdot (1 + call\_otm\_pct)\)
- `strangle.put_otm_pct`: put strike set to \(S \cdot (1 - put\_otm\_pct)\)
- `contract_multiplier`: typically 100 for US equity options
- `contracts_per_trade`: number of straddles/strangles per trade per day

#### `strategy.vol`
- `method`: `rolling_realized` or `fixed`
- `lookback_days`: lookback for realized volatility (daily closes)
- `fixed_iv`: used if `method=fixed` (and as a fallback)

#### `strategy.rates`
- `risk_free_rate`: annualized rate used by Black–Scholes

#### `strategy.costs`
- `slippage_bps`: bps applied on entry and exit notional (options and equity)

### `equity`
- `shares_per_trade`: used by the equity baseline

### `margin`
- `short_options_pct_underlying`: used only for plotting “capital required” on short option trades.
  It is a proxy for how much of the account is tied up when holding a short straddle/strangle.

### `performance`
- `initial_balance`: starting account balance for percent return calculations (default 100000)
- `horizons_trading_days`: trailing return windows in trading days (e.g. 1m=21, 1y=252)

### `plot`
- `output_dir`: output directory (relative to config file)
- `save_png`: whether to write `pnl.png`
- `show`: whether to display the matplotlib window (can be overridden by `--no-show`)

### `stress_test`
This is a sensitivity analysis for the option strategies (straddle/strangle). It does not use real option data. It re-prices each trade using Black–Scholes under a grid of assumptions, then writes additional CSVs and a chart.

- `enabled`: runs the stress test if true
- `iv_shift_entry_points`: list of absolute volatility shifts applied at entry (e.g. `0.05` means +5 vol points)
- `iv_shift_exit_points`: list of absolute volatility shifts applied at exit
- `option_half_spread_pct`: half-spread applied to option premiums:
  - buys use `mid * (1 + half_spread)`
  - sells use `mid * (1 - half_spread)`
- `extra_slippage_bps`: extra bps slippage applied on top of `strategy.costs.slippage_bps`
- `plot`: if true, writes `outputs/stress.png` showing total return vs exit IV shift


