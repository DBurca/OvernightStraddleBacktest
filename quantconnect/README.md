# QuantConnect integration: overnight straddle/strangle backtest

This folder contains a **QuantConnect** algorithm that mirrors your local backtest: sell straddle or strangle at market close, buy back at next market open. You can run it in QuantConnect’s cloud (Algorithm Lab) or locally with the Lean CLI.

---

## 1. Option 1: Algorithm Lab (QuantConnect cloud)

1. **Sign in** at [QuantConnect](https://www.quantconnect.com/) and open **Algorithm Lab**.
2. **New Algorithm** → **Blank Algorithm** (Python).
3. **Replace** the default `main.py` with the contents of `quantconnect/main.py` in this repo.
4. Set **project** backtest dates and capital in the algorithm (or in the Lab UI).
5. **Run Backtest**. Results, charts, and logs appear in the Lab.

No local install needed; data and execution run on QuantConnect.

---

## 2. Option 2: Lean CLI (local backtests)

Run the same algorithm locally with the open-source LEAN engine.

### Install Lean CLI

```bash
pip install lean
```

Docker is required for the engine. Then:

```bash
cd /path/to/OvernightStraddleBacktest
lean init
```

When prompted, log in with your QuantConnect credentials (or create an account). This links the CLI to QC for data.

### Add this project as a Lean algorithm

1. **Create a Lean project** (if you don’t have one):

   ```bash
   lean create-project "OvernightStraddle" --language python
   cd OvernightStraddle
   ```

2. **Copy the algorithm** into the Lean project’s `main.py`:

   - Copy `quantconnect/main.py` from this repo into the Lean project’s `main.py` (overwriting the template).

3. **Run a local backtest**:

   ```bash
   lean backtest "OvernightStraddle"
   ```

   Data is pulled from QuantConnect when needed. The first run may take longer while data is downloaded.

### Alternative: use this repo as the Lean project root

You can also turn this repo into a Lean project:

```bash
cd /path/to/OvernightStraddleBacktest
lean init
# When asked for project name, use e.g. "OvernightStraddle"
# Point the main file to quantconnect/main.py when creating the project, or
# copy quantconnect/main.py to the path Lean expects (e.g. ./main.py) and run:
lean backtest "OvernightStraddle"
```

Exact steps depend on how Lean names the project; the key is that `main.py` is the entry point and contains the `OvernightStraddleAlgorithm` class.

---

## 3. Config mapping: `config.yaml` → QuantConnect

| Your `config.yaml` | QuantConnect algorithm |
|-------------------|-------------------------|
| `tickers: [SPY]` | `self.underlying_ticker = "SPY"` |
| `run.position: short` | `self.position_side = "short"` |
| `run.strategies: [straddle]` or `[strangle]` | `self.strategy = "straddle"` or `"strangle"` |
| `strategy.dte_days: 7` | `self.dte_min = 5`, `self.dte_max = 10` (filters ~7 DTE) |
| `strategy.contracts_per_trade: 6` | `self.contracts_per_leg = 6` |
| `strategy.strangle.call_otm_pct: 0.02` | `self.call_otm_pct = 0.02` (strangle only) |
| `strategy.strangle.put_otm_pct: 0.02` | `self.put_otm_pct = 0.02` (strangle only) |
| `performance.initial_balance: 100000` | `self.SetCash("USD", 100_000)` |
| `backtest.lookback_trading_days` / `end_date` | `SetStartDate` / `SetEndDate` in `Initialize()` |

Edit the top of `Initialize()` in `quantconnect/main.py` to match your `config.yaml` (or add a config loader if you prefer).

---

## 4. What QuantConnect gives you

- **Real option chains**: bid/ask, IV, and actual expiries/strikes from QC’s options data (no Black–Scholes needed for prices if you use market orders).
- **Realistic execution**: market orders (or limit orders if you add them) at close and open; fills and slippage follow QC’s fill model.
- **Margin/collateral**: QC’s backtester applies margin rules for short options.
- **American options**: pricing and exercise are handled by the engine.

So the QC backtest is a useful complement to your local backtest: same strategy logic, different data and execution model.

---

## 5. Optional: sync config from YAML

To avoid editing the algorithm by hand, you can add a small script in this repo that reads `config.yaml` and prints the corresponding `Initialize()` snippet (or a JSON config) for pasting into the algorithm. That would live in this repo only; the algorithm in Algorithm Lab / Lean would still be edited or generated from that output.

---

## 6. References

- [QuantConnect Options API](https://www.quantconnect.com/learning/articles/introduction-to-options/quantconnect-options-api)
- [Scheduled Events](https://www.quantconnect.com/docs/v2/writing-algorithms/scheduled-events)
- [Lean CLI](https://www.quantconnect.com/docs/v2/lean-cli)
