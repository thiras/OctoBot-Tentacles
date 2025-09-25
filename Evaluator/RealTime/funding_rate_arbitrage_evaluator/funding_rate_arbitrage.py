#  Drakkar-Software OctoBot-Tentacles
#  Copyright (c) Drakkar-Software, All rights reserved.
#
#  This library is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3.0 of the License, or (at your option) any later version.
#
#  This library is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library.

import time
from collections import defaultdict
from typing import Dict, List, Optional, Any

import octobot_commons.constants as commons_constants
import octobot_commons.enums as commons_enums
import octobot_evaluators.evaluators as evaluators
import octobot_evaluators.util as evaluators_util


class FundingRateArbitrageEvaluator(evaluators.RealTimeEvaluator):
    """
    Funding Rate Arbitrage Evaluator

    This evaluator monitors funding rates across different exchanges for the same symbol
    to identify arbitrage opportunities and generate trading signals. It combines both
    opportunity detection and strategic decision making in a single component.

    Features:
    - Cross-exchange funding rate monitoring
    - Real-time arbitrage opportunity detection
    - Strategic signal generation for trading modes
    - Market-neutral position recommendations
    - Risk-aware position sizing suggestions
    
    Strategy:
    - Long on exchange with negative funding rate (receive funding)
    - Short on exchange with positive funding rate (pay less or receive funding)  
    - Profit from the spread while being market neutral
    """

    # Configuration constants
    RATE_THRESHOLD_KEY = "rate_difference_threshold_percent"
    MIN_RATE_ABSOLUTE_KEY = "min_rate_absolute_percent"
    MAX_EXCHANGES_KEY = "max_exchanges_to_compare"
    EVALUATION_INTERVAL_KEY = "evaluation_interval_minutes"
    MIN_LIQUIDITY_KEY = "min_liquidity_threshold"
    
    # Strategy configuration constants
    POSITION_SIZE_KEY = "position_size_percent"
    MAX_EXPOSURE_KEY = "max_total_exposure_percent"
    SIGNAL_TIMEOUT_KEY = "signal_timeout_seconds"
    ENABLE_LONG_SIGNALS_KEY = "enable_long_signals"
    ENABLE_SHORT_SIGNALS_KEY = "enable_short_signals"
    LEVERAGE_KEY = "leverage"
    MAX_LEVERAGE_KEY = "max_leverage"
    AUTO_LEVERAGE_KEY = "auto_adjust_leverage"

    # Default configuration values
    DEFAULT_RATE_THRESHOLD = 0.008  # 0.8%
    DEFAULT_MIN_RATE_ABSOLUTE = 0.005  # 0.5%
    DEFAULT_MAX_EXCHANGES = 5
    DEFAULT_EVALUATION_INTERVAL = 1  # minutes
    DEFAULT_MIN_LIQUIDITY = 100000  # USD
    
    # Strategy defaults
    DEFAULT_POSITION_SIZE = 5.0  # 5% of portfolio
    DEFAULT_MAX_EXPOSURE = 20.0  # 20% total exposure
    DEFAULT_SIGNAL_TIMEOUT = 300  # 5 minutes
    DEFAULT_ENABLE_LONG = True
    DEFAULT_ENABLE_SHORT = True
    DEFAULT_LEVERAGE = 1.0  # 1x leverage (no leverage)
    DEFAULT_MAX_LEVERAGE = 10.0  # Maximum 10x leverage
    DEFAULT_AUTO_LEVERAGE = False  # Don't auto-adjust by default

    # Evaluation constants
    MIN_EVALUATION_CHANGE = 0.1  # 10% change threshold for notifications
    CONFIDENCE_RATE_WEIGHT = 0.7
    CONFIDENCE_ABSOLUTE_WEIGHT = 0.3
    OPPORTUNITY_BONUS_MULTIPLIER = 0.1

    def __init__(self, tentacles_setup_config):
        super().__init__(tentacles_setup_config)

        # Configuration parameters (will be set by user inputs)
        self.rate_difference_threshold: float = self.DEFAULT_RATE_THRESHOLD
        self.min_rate_absolute: float = self.DEFAULT_MIN_RATE_ABSOLUTE
        self.max_exchanges: int = self.DEFAULT_MAX_EXCHANGES
        self.evaluation_interval: int = self.DEFAULT_EVALUATION_INTERVAL
        self.min_liquidity_threshold: float = self.DEFAULT_MIN_LIQUIDITY
        
        # Strategy configuration parameters
        self.position_size_percent: float = self.DEFAULT_POSITION_SIZE
        self.max_total_exposure_percent: float = self.DEFAULT_MAX_EXPOSURE
        self.signal_timeout_seconds: int = self.DEFAULT_SIGNAL_TIMEOUT
        self.enable_long_signals: bool = self.DEFAULT_ENABLE_LONG
        self.enable_short_signals: bool = self.DEFAULT_ENABLE_SHORT
        self.leverage: float = self.DEFAULT_LEVERAGE
        self.max_leverage: float = self.DEFAULT_MAX_LEVERAGE
        self.auto_adjust_leverage: bool = self.DEFAULT_AUTO_LEVERAGE

        # State tracking
        self.funding_rates: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.last_evaluation_time: Dict[str, float] = defaultdict(float)
        self.current_opportunities: Dict[str, List[Dict]] = {}
        self.last_notification_eval: float = 0
        self.active_signals: Dict[str, Dict[str, Any]] = {}  # Track active trading signals
        
        # Advanced position and risk management
        self.current_positions: Dict[str, Dict[str, Any]] = {}  # Track open positions
        self.total_exposure: float = 0.0  # Current total exposure percentage
        self.risk_metrics: Dict[str, float] = {}  # Risk tracking metrics
        self.performance_history: List[Dict[str, Any]] = []  # Performance tracking

    def init_user_inputs(self, inputs: dict) -> None:
        """
        Initialize user configuration inputs
        """
        # Time frame configuration
        self.time_frame = self.UI.user_input(
            commons_constants.CONFIG_TIME_FRAME,
            commons_enums.UserInputTypes.OPTIONS,
            commons_enums.TimeFrames.FIVE_MINUTES.value,
            inputs,
            options=[tf.value for tf in commons_enums.TimeFrames],
            title="Time frame for funding rate updates",
        )

        # Rate difference threshold
        self.rate_difference_threshold = (
            self.UI.user_input(
                self.RATE_THRESHOLD_KEY,
                commons_enums.UserInputTypes.FLOAT,
                1.0,
                inputs,
                min_val=0.1,
                max_val=10.0,
                title="Rate difference threshold (%): Minimum funding rate difference to trigger arbitrage signal",
            )
            / 100
        )

        # Minimum absolute rate
        self.min_rate_absolute = (
            self.UI.user_input(
                self.MIN_RATE_ABSOLUTE_KEY,
                commons_enums.UserInputTypes.FLOAT,
                0.5,
                inputs,
                min_val=0.01,
                max_val=5.0,
                title="Minimum absolute rate (%): Minimum absolute funding rate to consider",
            )
            / 100
        )

        # Maximum exchanges to compare
        self.max_exchanges = self.UI.user_input(
            self.MAX_EXCHANGES_KEY,
            commons_enums.UserInputTypes.INT,
            5,
            inputs,
            min_val=2,
            max_val=10,
            title="Maximum number of exchanges to compare for arbitrage opportunities",
        )

        # Evaluation interval
        self.evaluation_interval = self.UI.user_input(
            self.EVALUATION_INTERVAL_KEY,
            commons_enums.UserInputTypes.INT,
            5,
            inputs,
            min_val=1,
            max_val=60,
            title="Evaluation interval (minutes): How often to check for arbitrage opportunities",
        )

        # Minimum liquidity threshold
        self.min_liquidity_threshold = self.UI.user_input(
            self.MIN_LIQUIDITY_KEY,
            commons_enums.UserInputTypes.FLOAT,
            100000,
            inputs,
            min_val=1000,
            max_val=10000000,
            title="Minimum liquidity (USD): Minimum liquidity required for arbitrage opportunity",
        )

        # Strategy configuration
        self.position_size_percent = self.UI.user_input(
            self.POSITION_SIZE_KEY,
            commons_enums.UserInputTypes.FLOAT,
            5.0,
            inputs,
            min_val=0.1,
            max_val=50.0,
            title="Position size (%): Percentage of portfolio to risk per arbitrage opportunity",
        )

        self.max_total_exposure_percent = self.UI.user_input(
            self.MAX_EXPOSURE_KEY,
            commons_enums.UserInputTypes.FLOAT,
            20.0,
            inputs,
            min_val=1.0,
            max_val=100.0,
            title="Maximum exposure (%): Maximum total portfolio exposure across all arbitrage positions",
        )

        self.signal_timeout_seconds = self.UI.user_input(
            self.SIGNAL_TIMEOUT_KEY,
            commons_enums.UserInputTypes.INT,
            300,
            inputs,
            min_val=30,
            max_val=3600,
            title="Signal timeout (seconds): How long to consider an arbitrage signal valid",
        )

        self.enable_long_signals = self.UI.user_input(
            self.ENABLE_LONG_SIGNALS_KEY,
            commons_enums.UserInputTypes.BOOLEAN,
            True,
            inputs,
            title="Enable long signals: Generate buy signals for arbitrage opportunities",
        )

        self.enable_short_signals = self.UI.user_input(
            self.ENABLE_SHORT_SIGNALS_KEY,
            commons_enums.UserInputTypes.BOOLEAN,
            True,
            inputs,
            title="Enable short signals: Generate sell signals for arbitrage opportunities",
        )

        # Leverage configuration
        self.leverage = self.UI.user_input(
            self.LEVERAGE_KEY,
            commons_enums.UserInputTypes.FLOAT,
            1.0,
            inputs,
            min_val=1.0,
            max_val=100.0,
            title="Leverage: Base leverage to use for arbitrage positions (1.0 = no leverage, 2.0 = 2x leverage)",
        )

        self.max_leverage = self.UI.user_input(
            self.MAX_LEVERAGE_KEY,
            commons_enums.UserInputTypes.FLOAT,
            10.0,
            inputs,
            min_val=1.0,
            max_val=100.0,
            title="Maximum leverage: Maximum leverage allowed when auto-adjusting (safety limit)",
        )

        self.auto_adjust_leverage = self.UI.user_input(
            self.AUTO_LEVERAGE_KEY,
            commons_enums.UserInputTypes.BOOLEAN,
            False,
            inputs,
            title="Auto-adjust leverage: Automatically increase leverage for stronger arbitrage opportunities",
        )

    async def ohlcv_callback(
        self,
        exchange: str,
        exchange_id: str,
        cryptocurrency: str,
        symbol: str,
        time_frame,
        candle,
    ):
        """Handle OHLCV data updates"""
        await self._process_market_update(
            exchange,
            exchange_id,
            cryptocurrency,
            symbol,
            evaluators_util.get_eval_time(full_candle=candle, time_frame=time_frame),
        )

    async def kline_callback(
        self,
        exchange: str,
        exchange_id: str,
        cryptocurrency: str,
        symbol: str,
        time_frame,
        kline,
    ):
        """Handle kline data updates"""
        await self._process_market_update(
            exchange,
            exchange_id,
            cryptocurrency,
            symbol,
            evaluators_util.get_eval_time(kline=kline),
        )

    async def funding_channel_callback(
        self,
        exchange: str,
        exchange_id: str,
        cryptocurrency: str,
        symbol: str,
        funding_rate,
        predicted_funding_rate,
        next_funding_time,
        timestamp,
    ):
        """Handle funding rate updates from the funding channel"""
        try:
            if funding_rate is not None:
                rate = float(funding_rate)
                self.funding_rates[exchange][symbol] = rate
                self.logger.debug(
                    f"Updated funding rate for {symbol} on {exchange}: {rate:.6f}"
                )

                # Trigger evaluation after funding rate update
                await self._evaluate_arbitrage_opportunities(
                    cryptocurrency, symbol, timestamp or time.time()
                )
        except (ValueError, TypeError) as e:
            self.logger.debug(
                f"Invalid funding rate data for {symbol} on {exchange}: {e}"
            )
        except Exception as e:
            self.logger.warning(f"Error processing funding rate update: {e}")

    async def _process_market_update(
        self,
        exchange: str,
        exchange_id: str,
        cryptocurrency: str,
        symbol: str,
        eval_time: float,
    ):
        """Common logic for processing market data updates"""
        await self._update_funding_rates(exchange, exchange_id, symbol)
        await self._evaluate_arbitrage_opportunities(cryptocurrency, symbol, eval_time)

    async def _update_funding_rates(self, exchange: str, exchange_id: str, symbol: str):
        """Update funding rates from local cache or exchange API if needed"""
        try:
            # In a real implementation, this could fetch from exchange API
            # For now, we rely on the funding channel callback to update rates
            current_rate = self.funding_rates.get(exchange, {}).get(symbol)
            if current_rate is not None:
                self.logger.debug(
                    f"Current funding rate for {symbol} on {exchange}: {current_rate:.6f}"
                )
        except Exception as e:
            self.logger.debug(
                f"Error accessing funding rate for {symbol} on {exchange}: {e}"
            )

    async def _evaluate_arbitrage_opportunities(
        self, cryptocurrency: str, symbol: str, eval_time: float
    ):
        """Evaluate arbitrage opportunities based on funding rate differences"""
        # Check evaluation interval
        if not self._should_evaluate(symbol, eval_time):
            return

        self.last_evaluation_time[symbol] = eval_time

        # Get funding rates for this symbol
        symbol_rates = self._get_symbol_rates(symbol)
        if len(symbol_rates) < 2:
            self.logger.debug(f"Insufficient exchanges with funding rates for {symbol}")
            return

        # Find arbitrage opportunities
        opportunities = self._find_arbitrage_opportunities(symbol, symbol_rates)

        # Update state and evaluate
        if opportunities:
            self.current_opportunities[symbol] = opportunities
            await self._calculate_evaluation_score(
                cryptocurrency, symbol, opportunities, eval_time
            )
        else:
            self.current_opportunities.pop(symbol, None)
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE

    def _should_evaluate(self, symbol: str, current_time: float) -> bool:
        """Check if enough time has passed for reevaluation"""
        last_eval = self.last_evaluation_time.get(symbol, 0)
        return (current_time - last_eval) >= (self.evaluation_interval * 60)

    def _get_symbol_rates(self, symbol: str) -> Dict[str, float]:
        """Get funding rates for a symbol across all exchanges"""
        return {
            exchange: rates[symbol]
            for exchange, rates in self.funding_rates.items()
            if symbol in rates
        }

    def _find_arbitrage_opportunities(
        self, symbol: str, rates: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        """Find arbitrage opportunities from funding rate differences"""
        opportunities = []
        exchanges = list(rates.keys())

        for i, exchange1 in enumerate(exchanges):
            for exchange2 in exchanges[i + 1 :]:
                rate1, rate2 = rates[exchange1], rates[exchange2]
                opportunity = self._analyze_rate_pair(
                    symbol, exchange1, exchange2, rate1, rate2
                )

                if opportunity:
                    opportunities.append(opportunity)

        # Sort by rate difference (highest profit first) and limit results
        return sorted(opportunities, key=lambda x: x["rate_difference"], reverse=True)[
            : self.max_exchanges
        ]

    def _analyze_rate_pair(
        self, symbol: str, exchange1: str, exchange2: str, rate1: float, rate2: float
    ) -> Optional[Dict[str, Any]]:
        """Analyze a pair of rates for arbitrage opportunity"""
        rate_diff = abs(rate1 - rate2)

        # Check thresholds
        if rate_diff < self.rate_difference_threshold or (
            abs(rate1) < self.min_rate_absolute and abs(rate2) < self.min_rate_absolute
        ):
            return None

        # Determine position strategy
        long_exchange, short_exchange, long_rate, short_rate = (
            (exchange1, exchange2, rate1, rate2)
            if rate1 < rate2
            else (exchange2, exchange1, rate2, rate1)
        )

        return {
            "symbol": symbol,
            "long_exchange": long_exchange,
            "short_exchange": short_exchange,
            "long_rate": long_rate,
            "short_rate": short_rate,
            "rate_difference": rate_diff,
            "expected_profit": rate_diff,
            "liquidity_sufficient": True,  # Simplified check
            "confidence": self._calculate_confidence(rate_diff, long_rate, short_rate),
        }

    def _calculate_confidence(
        self, rate_diff: float, long_rate: float, short_rate: float
    ) -> float:
        """Calculate confidence score for arbitrage opportunity"""
        # Rate difference factor
        rate_factor = min(rate_diff / (self.rate_difference_threshold * 2), 1.0)

        # Absolute rate factor
        abs_rate_factor = min(
            (abs(long_rate) + abs(short_rate)) / (self.min_rate_absolute * 4), 1.0
        )

        return min(
            rate_factor * self.CONFIDENCE_RATE_WEIGHT
            + abs_rate_factor * self.CONFIDENCE_ABSOLUTE_WEIGHT,
            1.0,
        )

    async def _calculate_evaluation_score(
        self,
        cryptocurrency: str,
        symbol: str,
        opportunities: List[Dict[str, Any]],
        eval_time: float,
    ):
        """Calculate evaluation score based on arbitrage opportunities"""
        if not opportunities:
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE
            return

        best_opportunity = opportunities[0]

        # Calculate score components
        rate_diff = best_opportunity["rate_difference"]
        confidence = best_opportunity["confidence"]

        # Base score from rate difference
        base_score = min(rate_diff / (self.rate_difference_threshold * 3), 1.0)

        # Apply confidence and opportunity count multipliers
        final_score = base_score * confidence
        if len(opportunities) > 1:
            final_score *= (
                1 + (len(opportunities) - 1) * self.OPPORTUNITY_BONUS_MULTIPLIER
            )

        final_score = min(final_score, 1.0)

        # Update evaluation
        self.eval_note = final_score

        # Send notification if significant change
        if abs(self.last_notification_eval - final_score) >= self.MIN_EVALUATION_CHANGE:
            self.last_notification_eval = final_score
            await self.evaluation_completed(
                cryptocurrency, symbol, self.time_frame, eval_time=eval_time
            )
            self._log_opportunity(symbol, best_opportunity, final_score)
            
            # Generate trading signals for strong opportunities
            if final_score >= 0.5:  # Only generate signals for strong opportunities
                self._generate_trading_signals(symbol, opportunities, eval_time)

    def _log_opportunity(self, symbol: str, opportunity: Dict[str, Any], score: float):
        """Log arbitrage opportunity details"""
        self.logger.info(
            f"Funding arbitrage for {symbol}: "
            f"Long {opportunity['long_exchange']} ({opportunity['long_rate']:.6f}), "
            f"Short {opportunity['short_exchange']} ({opportunity['short_rate']:.6f}), "
            f"Spread: {opportunity['rate_difference']:.6f}, Score: {score:.3f}"
        )

    def _generate_trading_signals(self, symbol: str, opportunities: List[Dict[str, Any]], eval_time: float):
        """Generate trading signals based on arbitrage opportunities"""
        if not opportunities:
            return

        best_opportunity = opportunities[0]
        
        # Calculate dynamic leverage based on opportunity strength
        target_leverage = self._calculate_dynamic_leverage(symbol, best_opportunity)
        
        # Create signal data
        signal_data = {
            "symbol": symbol,
            "timestamp": eval_time,
            "opportunity": best_opportunity,
            "suggested_position_size": self.position_size_percent / 100,
            "target_leverage": target_leverage,
            "confidence": best_opportunity["confidence"],
            "timeout": eval_time + self.signal_timeout_seconds
        }
        
        # Generate long signal if enabled
        if self.enable_long_signals:
            long_signal_key = f"{symbol}_long_{best_opportunity['long_exchange']}"
            self.active_signals[long_signal_key] = {
                **signal_data,
                "signal_type": "long",
                "exchange": best_opportunity['long_exchange'],
                "target_rate": best_opportunity['long_rate']
            }
            
        # Generate short signal if enabled  
        if self.enable_short_signals:
            short_signal_key = f"{symbol}_short_{best_opportunity['short_exchange']}"
            self.active_signals[short_signal_key] = {
                **signal_data,
                "signal_type": "short", 
                "exchange": best_opportunity['short_exchange'],
                "target_rate": best_opportunity['short_rate']
            }
        
        self.logger.debug(f"Generated trading signals for {symbol} arbitrage opportunity (leverage: {target_leverage:.2f}x)")

    def _cleanup_expired_signals(self, current_time: float):
        """Remove expired trading signals"""
        expired_signals = [
            signal_key for signal_key, signal_data in self.active_signals.items()
            if current_time > signal_data.get("timeout", 0)
        ]
        
        for signal_key in expired_signals:
            del self.active_signals[signal_key]
            
        if expired_signals:
            self.logger.debug(f"Cleaned up {len(expired_signals)} expired trading signals")

    def get_active_trading_signals(self) -> Dict[str, Dict[str, Any]]:
        """Get currently active trading signals for external access"""
        current_time = time.time()
        self._cleanup_expired_signals(current_time)
        return self.active_signals.copy()

    def get_position_manager(self) -> Dict[str, Any]:
        """Get position management interface for trading mode integration"""
        return {
            "current_positions": self.current_positions.copy(),
            "total_exposure": self.total_exposure,
            "max_exposure": self.max_total_exposure_percent,
            "position_size": self.position_size_percent,
            "can_open_position": self._can_open_new_position,
            "update_position": self._update_position_tracking,
            "close_position": self._close_position_tracking,
            "get_target_leverage": self._get_target_leverage,
            "calculate_leverage": self._calculate_dynamic_leverage,
        }

    def _can_open_new_position(self, symbol: str, position_size_percent: float) -> bool:
        """Check if a new position can be opened based on exposure limits"""
        potential_exposure = self.total_exposure + position_size_percent
        return potential_exposure <= self.max_total_exposure_percent

    def _update_position_tracking(self, symbol: str, exchange: str, side: str, size: float, price: float):
        """Update position tracking for risk management"""
        position_key = f"{symbol}_{exchange}_{side}"
        
        self.current_positions[position_key] = {
            "symbol": symbol,
            "exchange": exchange,
            "side": side,
            "size": size,
            "entry_price": price,
            "timestamp": time.time(),
            "exposure_percent": (size * price / 100) * self.position_size_percent  # Simplified calculation
        }
        
        # Update total exposure
        self.total_exposure = sum(
            pos.get("exposure_percent", 0) for pos in self.current_positions.values()
        )
        
        self.logger.debug(f"Updated position tracking: {position_key}, Total exposure: {self.total_exposure:.2f}%")

    def _close_position_tracking(self, symbol: str, exchange: str, side: str, close_price: float):
        """Close position tracking and update performance metrics"""
        position_key = f"{symbol}_{exchange}_{side}"
        
        if position_key in self.current_positions:
            position = self.current_positions.pop(position_key)
            
            # Calculate P&L (simplified)
            entry_price = position["entry_price"]
            size = position["size"]
            pnl = (close_price - entry_price) * size if side == "long" else (entry_price - close_price) * size
            
            # Record performance
            self.performance_history.append({
                "timestamp": time.time(),
                "symbol": symbol,
                "exchange": exchange,
                "side": side,
                "entry_price": entry_price,
                "close_price": close_price,
                "size": size,
                "pnl": pnl,
                "duration": time.time() - position["timestamp"]
            })
            
            # Update total exposure
            self.total_exposure = sum(
                pos.get("exposure_percent", 0) for pos in self.current_positions.values()
            )
            
            self.logger.info(f"Closed position {position_key}: P&L {pnl:.6f}, Total exposure: {self.total_exposure:.2f}%")

    def _get_target_leverage(self, symbol: str) -> float:
        """Get the target leverage for a specific symbol"""
        symbol_data = self.current_positions.get(f"{symbol}_leverage_info", {})
        return symbol_data.get("target_leverage", self.leverage)

    def _calculate_dynamic_leverage(self, symbol: str, opportunity: Optional[Dict[str, Any]] = None) -> float:
        """Calculate dynamic leverage based on opportunity strength and configuration"""
        base_leverage = self.leverage

        if not self.auto_adjust_leverage or not opportunity:
            return min(base_leverage, self.max_leverage)

        # Auto-adjust leverage based on opportunity strength
        rate_difference = opportunity.get("rate_difference", 0)
        confidence = opportunity.get("confidence", 0.5)

        # Calculate leverage multiplier based on opportunity strength
        strength_factor = min(rate_difference * 50, 2.0)  # Rate diff of 2% = 2x multiplier
        confidence_factor = confidence

        # Calculate adjusted leverage
        leverage_multiplier = 1 + (strength_factor * confidence_factor * 0.5)
        effective_leverage = base_leverage * leverage_multiplier

        # Apply safety limits
        effective_leverage = min(effective_leverage, self.max_leverage)
        effective_leverage = max(effective_leverage, 1.0)  # Never go below 1x

        # Store leverage info for later reference
        self._store_leverage_info(symbol, effective_leverage)

        return effective_leverage

    def _store_leverage_info(self, symbol: str, leverage: float):
        """Store leverage information for position tracking"""
        leverage_key = f"{symbol}_leverage_info"
        
        self.current_positions[leverage_key] = {
            "target_leverage": leverage,
            "timestamp": time.time(),
            "symbol": symbol,
        }
        
        self.logger.debug(f"Set target leverage for {symbol}: {leverage:.2f}x")

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for monitoring"""
        if not self.performance_history:
            return {"total_trades": 0, "total_pnl": 0, "win_rate": 0, "avg_duration": 0}
        
        total_trades = len(self.performance_history)
        total_pnl = sum(trade["pnl"] for trade in self.performance_history)
        winning_trades = len([trade for trade in self.performance_history if trade["pnl"] > 0])
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        avg_duration = sum(trade["duration"] for trade in self.performance_history) / total_trades
        
        return {
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "avg_duration": avg_duration,
            "winning_trades": winning_trades,
            "losing_trades": total_trades - winning_trades,
            "current_positions": len(self.current_positions),
            "total_exposure": self.total_exposure
        }

    def _get_available_exchange_ids(self) -> List[str]:
        """Get available exchange IDs using multiple fallback strategies"""
        try:
            import octobot_trading.api as trading_api

            # Strategy 1: Get from matrix (preferred)
            if hasattr(self, "exchange_name") and hasattr(self, "matrix_id"):
                try:
                    exchange_id = trading_api.get_exchange_id_from_matrix_id(
                        self.exchange_name, self.matrix_id
                    )
                    return [exchange_id]
                except Exception:
                    pass

            # Strategy 2: Use configured exchange_ids
            exchange_ids = getattr(self, "exchange_ids", None) or getattr(
                self.tentacles_setup_config, "exchange_ids", []
            )
            if exchange_ids:
                return exchange_ids

            # Strategy 3: Get all available exchange IDs
            return trading_api.get_exchange_ids() or []

        except ImportError:
            self.logger.error("Cannot import trading API - exchange discovery disabled")
            return []
        except Exception as e:
            self.logger.warning(f"Error discovering exchanges: {e}")
            return []

    def _exchange_supports_funding_rates(self, exchange_id: str) -> bool:
        """Check if exchange supports funding rates"""
        try:
            import octobot_trading.api as trading_api

            exchange_manager = trading_api.get_exchange_manager_from_exchange_id(
                exchange_id
            )
            if not exchange_manager:
                return False

            exchange = trading_api.get_exchange(exchange_manager, exchange_id)
            if not exchange:
                return False

            # Check funding rate capability
            if hasattr(exchange, "has") and isinstance(exchange.has, dict):
                return exchange.has.get("fetchFundingRate", False)

            # Fallback: check if futures exchange
            exchange_type = trading_api.get_exchange_type(exchange_manager)
            return exchange_type and "FUTURE" in str(exchange_type).upper()

        except Exception:
            return False

    async def start(self, bot_id: str) -> bool:
        """Start the evaluator and subscribe to funding channels"""
        self.logger.info(
            f"Starting Funding Rate Arbitrage Evaluator (threshold: {self.rate_difference_threshold:.4f})"
        )

        try:
            import octobot_commons.channels_name as channels_name
            import octobot_trading.exchange_channel as exchanges_channel

            # Discover and validate exchanges
            potential_exchanges = self._get_available_exchange_ids()
            if not potential_exchanges:
                self.logger.warning(
                    "No exchanges found - funding rate arbitrage disabled. "
                    "Ensure exchanges are configured properly."
                )
                return await super().start(bot_id)

            # Filter for funding rate support
            valid_exchanges = [
                ex_id
                for ex_id in potential_exchanges
                if self._exchange_supports_funding_rates(ex_id)
            ]

            if not valid_exchanges:
                self.logger.warning(
                    f"No exchanges support funding rates (checked: {potential_exchanges}) - "
                    f"funding rate arbitrage disabled."
                )
                return await super().start(bot_id)

            # Subscribe to funding channels
            subscribed_count = await self._subscribe_to_funding_channels(
                channels_name, exchanges_channel, valid_exchanges
            )

            if subscribed_count > 0:
                self.logger.info(
                    f"Successfully subscribed to {subscribed_count} funding channels"
                )
            else:
                self.logger.warning("Failed to subscribe to any funding channels")

        except Exception as e:
            self.logger.error(f"Error during funding channel setup: {e}")

        return await super().start(bot_id)

    async def _subscribe_to_funding_channels(
        self, channels_name, exchanges_channel, exchange_ids: List[str]
    ) -> int:
        """Subscribe to funding channels for valid exchanges"""
        subscribed_count = 0

        for exchange_id in exchange_ids:
            try:
                await exchanges_channel.get_chan(
                    channels_name.OctoBotTradingChannelsName.FUNDING_CHANNEL.value,
                    exchange_id,
                ).new_consumer(self.funding_channel_callback)

                subscribed_count += 1
                self.logger.debug(f"Subscribed to funding channel: {exchange_id}")

            except Exception as e:
                self.logger.warning(f"Failed to subscribe to {exchange_id}: {e}")

        return subscribed_count

    async def stop(self) -> None:
        """Stop the evaluator"""
        self.logger.info("Stopping Funding Rate Arbitrage Evaluator")
        await super().stop()

    def get_current_opportunities(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get current arbitrage opportunities for external access"""
        return self.current_opportunities.copy()

    def get_strategy_interface(self) -> Dict[str, Any]:
        """
        Comprehensive strategy interface for trading mode integration
        
        This method provides all the functionality that would typically be
        handled by a separate StrategyEvaluator, making this RealTime evaluator
        fully self-contained for funding rate arbitrage operations.
        """
        current_time = time.time()
        self._cleanup_expired_signals(current_time)
        
        return {
            # Core strategy data
            "signals": self.get_active_trading_signals(),
            "opportunities": self.get_current_opportunities(),
            "position_manager": self.get_position_manager(),
            "performance": self.get_performance_metrics(),
            
            # Configuration access
            "config": {
                "position_size_percent": self.position_size_percent,
                "max_exposure_percent": self.max_total_exposure_percent,
                "signal_timeout": self.signal_timeout_seconds,
                "rate_threshold": self.rate_difference_threshold,
                "min_rate_absolute": self.min_rate_absolute,
                "enable_long": self.enable_long_signals,
                "enable_short": self.enable_short_signals,
                "leverage": self.leverage,
                "max_leverage": self.max_leverage,
                "auto_leverage": self.auto_adjust_leverage,
            },
            
            # Strategy state
            "state": {
                "total_exposure": self.total_exposure,
                "active_positions_count": len(self.current_positions),
                "active_signals_count": len(self.active_signals),
                "opportunities_count": sum(len(ops) for ops in self.current_opportunities.values()),
                "last_evaluation": max(self.last_evaluation_time.values()) if self.last_evaluation_time else 0,
            },
            
            # Helper methods for trading modes
            "methods": {
                "can_trade": lambda symbol, size: self._can_open_new_position(symbol, size),
                "get_position_size": lambda: self.position_size_percent / 100,
                "get_best_opportunity": lambda symbol: self.current_opportunities.get(symbol, [{}])[0] if symbol in self.current_opportunities else None,
                "validate_signal": lambda signal: current_time <= signal.get("timeout", 0),
                "get_target_leverage": lambda symbol: self._get_target_leverage(symbol),
                "calculate_leverage": lambda symbol, opp: self._calculate_dynamic_leverage(symbol, opp),
            }
        }

    def get_evaluation_summary(self) -> str:
        """Get a summary of current evaluation state"""
        if not self.current_opportunities:
            return "No arbitrage opportunities detected"

        summaries = []
        active_signals = self.get_active_trading_signals()
        
        for symbol, opportunities in self.current_opportunities.items():
            if opportunities:
                best = opportunities[0]
                signal_count = len([s for s in active_signals.keys() if symbol in s])
                signal_suffix = f" [{signal_count} signals]" if signal_count > 0 else ""
                
                summaries.append(
                    f"{symbol}: {best['rate_difference']:.4f}% spread "
                    f"({best['long_exchange']} vs {best['short_exchange']}){signal_suffix}"
                )

        return "; ".join(summaries) if summaries else "No opportunities"
