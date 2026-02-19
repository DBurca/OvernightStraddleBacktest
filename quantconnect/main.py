# region imports
from AlgorithmImports import *
from datetime import timedelta
# endregion

class OvernightOptionsStrategy(QCAlgorithm):
    """Overnight straddle/strangle on SPY selling options at market close and closing at next open"""
    
    def initialize(self):
        self.set_start_date(2020, 1, 1)
        self.set_cash(100000)
        
        # Configuration
        self._strategy_type = "strangle"  # "straddle" or "strangle"
        self._otm_percent = 0.02  # 2% OTM for strangle
        self._target_dte = 7  # Target days to expiration
        self._contracts_per_leg = 1  # Number of contracts per leg
        
        # Add SPY equity and options
        spy = self.add_equity("SPY", Resolution.MINUTE)
        spy.set_data_normalization_mode(DataNormalizationMode.RAW)
        
        option = self.add_option("SPY", Resolution.MINUTE)
        option.set_filter(self._option_filter)
        
        self._spy = spy.symbol
        self._option_symbol = option.symbol
        
        # Track active contracts and option chain
        self._active_contracts = []
        self._latest_chain = None
        
        # Schedule events
        self.schedule.on(self.date_rules.every_day(self._spy),
                        self.time_rules.before_market_close(self._spy, 1),
                        self._open_position)
        
        self.schedule.on(self.date_rules.every_day(self._spy),
                        self.time_rules.after_market_open(self._spy, 1),
                        self._close_position)
        
        self.settings.seed_initial_prices = True
    
    def _option_filter(self, universe):
        return universe.strikes(-5, 5).expiration(self._target_dte - 2, self._target_dte + 2)
    
    def on_data(self, data: Slice):
        """Update latest option chain"""
        if data.option_chains.contains_key(self._option_symbol):
            self._latest_chain = data.option_chains[self._option_symbol]
    
    def _open_position(self):
        """Open straddle/strangle at market close"""
        # Close any existing positions first
        if self._active_contracts:
            self._close_position()
        
        # Get option chain
        if not self._latest_chain:
            return
        
        chain = self._latest_chain
        if not chain or len(chain) == 0:
            return
        
        # Get current SPY price
        spy_price = self.securities[self._spy].price
        if spy_price == 0:
            return
        
        # Filter for target DTE and get sorted contracts
        calls = [x for x in chain if x.right == OptionRight.CALL]
        puts = [x for x in chain if x.right == OptionRight.PUT]
        
        if not calls or not puts:
            return
        
        # Find the best expiry closest to target DTE
        expiries = set([x.expiry for x in chain])
        if not expiries:
            return
        
        target_expiry = min(expiries, key=lambda x: abs((x - self.time).days - self._target_dte))
        
        # Filter by expiry
        calls = [x for x in calls if x.expiry == target_expiry]
        puts = [x for x in puts if x.expiry == target_expiry]
        
        if not calls or not puts:
            return
        
        # Select strikes based on strategy type
        if self._strategy_type == "straddle":
            # ATM straddle - find strikes closest to current price
            call = min(calls, key=lambda x: abs(x.strike - spy_price))
            put = min(puts, key=lambda x: abs(x.strike - spy_price))
        else:  # strangle
            # OTM strangle - call above, put below
            target_call_strike = spy_price * (1 + self._otm_percent)
            target_put_strike = spy_price * (1 - self._otm_percent)
            
            call = min(calls, key=lambda x: abs(x.strike - target_call_strike))
            put = min(puts, key=lambda x: abs(x.strike - target_put_strike))
        
        # Check if we have valid prices
        if call.bid_price == 0 or put.bid_price == 0:
            self.debug(f"Invalid option prices at {self.time}")
            return
        
        # Sell the options
        self.market_order(call.symbol, -self._contracts_per_leg)
        self.market_order(put.symbol, -self._contracts_per_leg)
        
        self._active_contracts = [call.symbol, put.symbol]
        
        premium_collected = (call.bid_price + put.bid_price) * 100 * self._contracts_per_leg
        self.debug(f"{self.time}: Opened {self._strategy_type} - Call: {call.strike}, Put: {put.strike}, DTE: {(target_expiry - self.time).days}, Premium: ${premium_collected:.2f}")
    
    def _close_position(self):
        """Close all option positions at market open"""
        if not self._active_contracts:
            return
        
        for contract in self._active_contracts:
            if self.portfolio[contract].invested:
                self.liquidate(contract)
        
        self.debug(f"{self.time}: Closed positions")
        self._active_contracts = []
