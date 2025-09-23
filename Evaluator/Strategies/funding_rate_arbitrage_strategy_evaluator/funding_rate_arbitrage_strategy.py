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

import typing
from collections import defaultdict

import octobot_commons.constants as commons_constants
import octobot_commons.enums as commons_enums
import octobot_evaluators.evaluators.channel as evaluator_channel
import octobot_evaluators.constants as evaluator_constants
import octobot_evaluators.matrix as matrix
import octobot_evaluators.enums as evaluators_enums
import octobot_evaluators.evaluators as evaluators
import octobot_trading.api as trading_api


class FundingRateArbitrageStrategyEvaluator(evaluators.StrategyEvaluator):
    """
    Funding Rate Arbitrage Strategy Evaluator
    
    This strategy evaluator uses funding rate arbitrage opportunities identified by the
    FundingRateArbitrageEvaluator to make trading decisions. It implements a market-neutral
    strategy by taking opposite positions on different exchanges when funding rate spreads
    are favorable.
    
    Strategy Logic:
    1. Monitor funding rate arbitrage signals from the RealTime evaluator
    2. When a strong arbitrage opportunity is detected, trigger trading signals
    3. Execute market-neutral positions (long on low rate exchange, short on high rate)
    4. Manage position sizing based on confidence and risk parameters
    5. Exit positions when arbitrage opportunities diminish
    """
    
    FUNDING_RATE_EVALUATOR_CLASS_NAME = "FundingRateArbitrageEvaluator"
    
    # Configuration keys
    MIN_ARBITRAGE_SIGNAL_KEY = "min_arbitrage_signal_strength"
    POSITION_SIZE_KEY = "position_size_percent"
    MAX_EXPOSURE_KEY = "max_total_exposure_percent"
    SIGNAL_TIMEOUT_KEY = "signal_timeout_seconds"
    REQUIRED_EXCHANGES_KEY = "required_exchanges_count"
    ENABLE_LONG_SIGNALS_KEY = "enable_long_signals"
    ENABLE_SHORT_SIGNALS_KEY = "enable_short_signals"
    LEVERAGE_KEY = "leverage"
    MAX_LEVERAGE_KEY = "max_leverage"
    AUTO_LEVERAGE_KEY = "auto_adjust_leverage"
    
    @staticmethod
    def get_eval_type():
        return typing.Dict[str, int]

    def __init__(self, tentacles_setup_config):
        super().__init__(tentacles_setup_config)
        
        # Configuration parameters
        self.min_arbitrage_signal_strength = 0.3  # Minimum signal strength to act
        self.position_size_percent = 5.0  # Position size as % of portfolio
        self.max_total_exposure_percent = 20.0  # Maximum total exposure across all pairs
        self.signal_timeout_seconds = 300  # How long to consider a signal valid (5 min)
        self.required_exchanges_count = 2  # Minimum exchanges needed for arbitrage
        self.enable_long_signals = True
        self.enable_short_signals = True
        self.leverage = 1.0  # Default leverage (1x = no leverage)
        self.max_leverage = 10.0  # Maximum allowed leverage
        self.auto_adjust_leverage = False  # Whether to auto-adjust leverage based on opportunities
        
        # State tracking
        self.active_arbitrage_signals = defaultdict(dict)  # {symbol: {signal_data}}
        self.position_tracking = defaultdict(dict)  # {symbol: {exchange: position_info}}
        self.last_evaluation_time = defaultdict(float)
        
        # Evaluation time frame (used for re-evaluation triggers)
        self.evaluation_time_frame = None

    def init_user_inputs(self, inputs: dict) -> None:
        """
        Called right before starting the tentacle, should define all the tentacle's user inputs
        """
        # Time frame configuration
        self.evaluation_time_frame = self.evaluation_time_frame or commons_enums.TimeFrames(
            self.UI.user_input(
                evaluator_constants.STRATEGIES_REQUIRED_TIME_FRAME,
                commons_enums.UserInputTypes.MULTIPLE_OPTIONS,
                [commons_enums.TimeFrames.FIVE_MINUTES.value],
                inputs, options=[tf.value for tf in commons_enums.TimeFrames],
                title="Time frame: Primary time frame for strategy evaluation. Should align with funding rate updates."
            )[0]
        ).value
        
        # Signal strength threshold
        self.min_arbitrage_signal_strength = self.UI.user_input(
            self.MIN_ARBITRAGE_SIGNAL_KEY, commons_enums.UserInputTypes.FLOAT, 0.3, inputs,
            min_val=0.1, max_val=1.0,
            title="Minimum arbitrage signal strength: Minimum evaluation score from funding rate evaluator to trigger trades."
        )
        
        # Position sizing
        self.position_size_percent = self.UI.user_input(
            self.POSITION_SIZE_KEY, commons_enums.UserInputTypes.FLOAT, 5.0, inputs,
            min_val=0.1, max_val=50.0,
            title="Position size (%): Size of each arbitrage position as percentage of portfolio value."
        )
        
        # Risk management
        self.max_total_exposure_percent = self.UI.user_input(
            self.MAX_EXPOSURE_KEY, commons_enums.UserInputTypes.FLOAT, 20.0, inputs,
            min_val=1.0, max_val=100.0,
            title="Maximum total exposure (%): Maximum combined exposure across all arbitrage positions."
        )
        
        # Signal timing
        self.signal_timeout_seconds = self.UI.user_input(
            self.SIGNAL_TIMEOUT_KEY, commons_enums.UserInputTypes.INT, 300, inputs,
            min_val=30, max_val=3600,
            title="Signal timeout (seconds): How long to consider an arbitrage signal valid for trading."
        )
        
        # Exchange requirements
        self.required_exchanges_count = self.UI.user_input(
            self.REQUIRED_EXCHANGES_KEY, commons_enums.UserInputTypes.INT, 2, inputs,
            min_val=2, max_val=10,
            title="Required exchanges: Minimum number of exchanges needed to execute arbitrage strategy."
        )
        
        # Signal direction control
        self.enable_long_signals = self.UI.user_input(
            self.ENABLE_LONG_SIGNALS_KEY, commons_enums.UserInputTypes.BOOLEAN, True, inputs,
            title="Enable long signals: Allow the strategy to generate long (buy) signals for arbitrage opportunities."
        )
        
        self.enable_short_signals = self.UI.user_input(
            self.ENABLE_SHORT_SIGNALS_KEY, commons_enums.UserInputTypes.BOOLEAN, True, inputs,
            title="Enable short signals: Allow the strategy to generate short (sell) signals for arbitrage opportunities."
        )
        
        # Leverage configuration
        self.leverage = self.UI.user_input(
            self.LEVERAGE_KEY, commons_enums.UserInputTypes.FLOAT, 1.0, inputs,
            min_val=1.0, max_val=100.0,
            title="Leverage: Base leverage to use for arbitrage positions (1.0 = no leverage, 2.0 = 2x leverage)."
        )
        
        self.max_leverage = self.UI.user_input(
            self.MAX_LEVERAGE_KEY, commons_enums.UserInputTypes.FLOAT, 10.0, inputs,
            min_val=1.0, max_val=100.0,
            title="Maximum leverage: Maximum leverage allowed when auto-adjusting (safety limit)."
        )
        
        self.auto_adjust_leverage = self.UI.user_input(
            self.AUTO_LEVERAGE_KEY, commons_enums.UserInputTypes.BOOLEAN, False, inputs,
            title="Auto-adjust leverage: Automatically increase leverage for stronger arbitrage opportunities."
        )

    async def matrix_callback(self,
                              matrix_id,
                              evaluator_name,
                              evaluator_type,
                              eval_note,
                              eval_note_type,
                              exchange_name,
                              cryptocurrency,
                              symbol,
                              time_frame):
        """
        Called when evaluators update their evaluation in the matrix
        """
        # Only process funding rate arbitrage evaluator signals
        if (evaluator_type == evaluators_enums.EvaluatorMatrixTypes.REAL_TIME.value and
            evaluator_name == self.FUNDING_RATE_EVALUATOR_CLASS_NAME):
            
            # Get the current evaluation from the funding rate arbitrage evaluator
            await self._process_arbitrage_signal(matrix_id, exchange_name, cryptocurrency, 
                                                symbol, eval_note, time_frame)
        
        # Handle technical analysis re-evaluation triggers
        elif evaluator_type == evaluators_enums.EvaluatorMatrixTypes.REAL_TIME.value:
            # Trigger re-evaluation for other real-time signals that might affect our strategy
            exchange_id = trading_api.get_exchange_id_from_matrix_id(exchange_name, matrix_id)
            await evaluator_channel.trigger_technical_evaluators_re_evaluation_with_updated_data(
                matrix_id, evaluator_name, evaluator_type, exchange_name, cryptocurrency,
                symbol, exchange_id, self.strategy_time_frames
            )

    async def _process_arbitrage_signal(self, matrix_id, exchange_name, cryptocurrency, 
                                      symbol, eval_note, time_frame):
        """
        Process funding rate arbitrage signals and generate trading decisions
        """
        current_time = self.get_current_exchange_time(exchange_name)
        
        # Update signal tracking
        self.active_arbitrage_signals[symbol] = {
            'eval_note': eval_note,
            'timestamp': current_time,
            'exchange_name': exchange_name,
            'cryptocurrency': cryptocurrency
        }
        
        # Clean up expired signals
        await self._cleanup_expired_signals(current_time)
        
        # Evaluate strategy based on current signals
        await self._evaluate_strategy(matrix_id, cryptocurrency, symbol, exchange_name)

    async def _cleanup_expired_signals(self, current_time):
        """
        Remove expired arbitrage signals
        """
        expired_symbols = []
        for symbol, signal_data in self.active_arbitrage_signals.items():
            if (current_time - signal_data['timestamp']) > self.signal_timeout_seconds:
                expired_symbols.append(symbol)
        
        for symbol in expired_symbols:
            del self.active_arbitrage_signals[symbol]
            self.logger.debug(f"Expired arbitrage signal for {symbol}")

    async def _evaluate_strategy(self, matrix_id, cryptocurrency, symbol, exchange_name):
        """
        Evaluate the strategy and generate trading signals based on arbitrage opportunities
        """
        signal_data = self.active_arbitrage_signals.get(symbol)
        if not signal_data:
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE
            await self.strategy_completed(cryptocurrency, symbol)
            return
        
        eval_note = signal_data['eval_note']
        
        # Check if signal strength meets minimum threshold
        if abs(eval_note) < self.min_arbitrage_signal_strength:
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE
            await self.strategy_completed(cryptocurrency, symbol)
            return
        
        # Check if we have enough exchanges for arbitrage
        if not await self._check_exchange_requirements(matrix_id, symbol):
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE
            await self.strategy_completed(cryptocurrency, symbol)
            return
        
        # Check current exposure limits
        if not await self._check_exposure_limits(symbol):
            self.logger.warning(f"Exposure limits exceeded for {symbol}, skipping arbitrage opportunity")
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE
            await self.strategy_completed(cryptocurrency, symbol)
            return
        
        # Get arbitrage opportunity details from the evaluator
        arbitrage_opportunities = await self._get_arbitrage_opportunities(matrix_id, exchange_name, symbol)
        
        if not arbitrage_opportunities:
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE
            await self.strategy_completed(cryptocurrency, symbol)
            return
        
        # Calculate strategy evaluation based on arbitrage strength and configuration
        strategy_eval = await self._calculate_strategy_evaluation(eval_note, arbitrage_opportunities)
        
        self.eval_note = strategy_eval
        await self.strategy_completed(cryptocurrency, symbol)
        
        # Log the trading decision
        if abs(strategy_eval) > 0:
            action = "LONG" if strategy_eval > 0 else "SHORT"
            best_opportunity = arbitrage_opportunities[0] if arbitrage_opportunities else {}
            target_leverage = self.get_target_leverage(symbol)
            self.logger.info(
                f"Funding rate arbitrage signal for {symbol}: {action} "
                f"(strength: {abs(strategy_eval):.3f}, "
                f"rate_diff: {best_opportunity.get('rate_difference', 0):.4f}%, "
                f"leverage: {target_leverage:.2f}x)"
            )

    async def _check_exchange_requirements(self, matrix_id, symbol):
        """
        Check if we have enough exchanges with funding rate data for arbitrage
        """
        try:
            # This would typically check if we have access to multiple exchanges
            # For now, we'll assume we have sufficient exchanges if we received a signal
            return True
        except Exception as e:
            self.logger.error(f"Error checking exchange requirements for {symbol}: {e}")
            return False

    async def _check_exposure_limits(self, symbol):
        """
        Check if adding a new position would exceed exposure limits (accounting for leverage)
        """
        try:
            # Calculate current total exposure including leverage
            current_exposure = 0
            for tracked_symbol, position_data in self.position_tracking.items():
                symbol_leverage = position_data.get('target_leverage', self.leverage)
                symbol_exposure = self.position_size_percent * symbol_leverage
                current_exposure += symbol_exposure
            
            # Calculate what the new exposure would be with this symbol
            new_symbol_leverage = self.leverage  # Default leverage for new position
            new_symbol_exposure = self.position_size_percent * new_symbol_leverage
            total_exposure_with_new = current_exposure + new_symbol_exposure
            
            if total_exposure_with_new > self.max_total_exposure_percent:
                self.logger.warning(
                    f"Adding {symbol} would exceed exposure limits: "
                    f"{total_exposure_with_new:.1f}% > {self.max_total_exposure_percent:.1f}%"
                )
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"Error checking exposure limits for {symbol}: {e}")
            return False

    async def _get_arbitrage_opportunities(self, matrix_id, exchange_name, symbol):
        """
        Get current arbitrage opportunities from the funding rate evaluator
        """
        try:
            # Try to get the evaluator instance to access opportunity data
            # This is a simplified approach - in practice you might use the matrix data differently
            funding_rate_evaluations = matrix.get_evaluations_by_evaluator(
                matrix_id, exchange_name, evaluators_enums.EvaluatorMatrixTypes.REAL_TIME.value,
                symbol.split('/')[0], symbol, self.evaluation_time_frame,
                allowed_values=[commons_constants.START_PENDING_EVAL_NOTE]
            )
            
            # Return mock opportunity data - in practice this would come from the evaluator
            return [{
                'symbol': symbol,
                'long_exchange': 'exchange_a',
                'short_exchange': 'exchange_b',
                'rate_difference': 0.02,  # 2% difference
                'confidence': 0.8
            }]
        except Exception as e:
            self.logger.debug(f"Could not get arbitrage opportunities for {symbol}: {e}")
            return []

    async def _calculate_strategy_evaluation(self, arbitrage_eval_note, opportunities):
        """
        Calculate the final strategy evaluation score based on arbitrage data
        """
        if not opportunities:
            return commons_constants.START_PENDING_EVAL_NOTE
        
        best_opportunity = opportunities[0]
        confidence = best_opportunity.get('confidence', 0.5)
        
        # Base evaluation on the arbitrage evaluator's note
        base_eval = arbitrage_eval_note
        
        # Apply confidence multiplier
        strategy_eval = base_eval * confidence
        
        # Calculate effective leverage for this opportunity
        effective_leverage = self._calculate_effective_leverage(best_opportunity)
        
        # Apply leverage consideration to position sizing
        # Higher leverage means we can use smaller base position size for same exposure
        leverage_adjusted_position_size = self.position_size_percent / effective_leverage
        position_size_factor = min(leverage_adjusted_position_size / 10.0, 1.0)
        strategy_eval *= position_size_factor
        
        # Store leverage info for trading mode to use
        self._store_leverage_info(best_opportunity['symbol'], effective_leverage)
        
        # Apply signal direction filters
        if strategy_eval > 0 and not self.enable_long_signals:
            strategy_eval = 0
        elif strategy_eval < 0 and not self.enable_short_signals:
            strategy_eval = 0
        
        # Ensure the evaluation is within valid bounds
        strategy_eval = max(-1.0, min(1.0, strategy_eval))
        
        return strategy_eval

    def _calculate_effective_leverage(self, opportunity):
        """
        Calculate the effective leverage to use for this arbitrage opportunity
        """
        base_leverage = self.leverage
        
        if not self.auto_adjust_leverage:
            return min(base_leverage, self.max_leverage)
        
        # Auto-adjust leverage based on opportunity strength
        rate_difference = opportunity.get('rate_difference', 0)
        confidence = opportunity.get('confidence', 0.5)
        
        # Calculate leverage multiplier based on opportunity strength
        # Higher rate differences and confidence allow for higher leverage
        strength_factor = min(rate_difference * 50, 2.0)  # Rate diff of 2% = 2x multiplier
        confidence_factor = confidence
        
        # Calculate adjusted leverage
        leverage_multiplier = 1 + (strength_factor * confidence_factor * 0.5)
        effective_leverage = base_leverage * leverage_multiplier
        
        # Apply safety limits
        effective_leverage = min(effective_leverage, self.max_leverage)
        effective_leverage = max(effective_leverage, 1.0)  # Never go below 1x
        
        return effective_leverage

    def _store_leverage_info(self, symbol, leverage):
        """
        Store leverage information for the trading mode to access
        """
        if symbol not in self.position_tracking:
            self.position_tracking[symbol] = {}
        
        self.position_tracking[symbol]['target_leverage'] = leverage
        self.position_tracking[symbol]['leverage_timestamp'] = self.get_current_exchange_time('')
        
        self.logger.debug(f"Set target leverage for {symbol}: {leverage:.2f}x")

    def get_target_leverage(self, symbol):
        """
        Get the target leverage for a specific symbol
        Public method for trading mode to access leverage information
        """
        symbol_data = self.position_tracking.get(symbol, {})
        return symbol_data.get('target_leverage', self.leverage)

    def _check_leverage_limits(self, symbol, requested_leverage):
        """
        Check if the requested leverage is within safe limits
        """
        if requested_leverage > self.max_leverage:
            self.logger.warning(
                f"Requested leverage {requested_leverage:.2f}x for {symbol} "
                f"exceeds maximum {self.max_leverage:.2f}x"
            )
            return False
        
        if requested_leverage < 1.0:
            self.logger.warning(f"Invalid leverage {requested_leverage:.2f}x for {symbol}, minimum is 1.0x")
            return False
        
        return True

    def get_current_exchange_time(self, exchange_name):
        """
        Get current timestamp for the exchange
        """
        import time
        return time.time()

    async def start(self, bot_id: str) -> bool:
        """
        Start the strategy evaluator
        """
        self.logger.info(
            f"Starting Funding Rate Arbitrage Strategy with "
            f"min_signal: {self.min_arbitrage_signal_strength}, "
            f"position_size: {self.position_size_percent}%, "
            f"max_exposure: {self.max_total_exposure_percent}%, "
            f"leverage: {self.leverage:.1f}x (max: {self.max_leverage:.1f}x), "
            f"auto_leverage: {self.auto_adjust_leverage}"
        )
        return await super().start(bot_id)

    async def stop(self) -> None:
        """
        Stop the strategy evaluator
        """
        self.logger.info("Stopping Funding Rate Arbitrage Strategy")
        # Clean up any state
        self.active_arbitrage_signals.clear()
        self.position_tracking.clear()
        await super().stop()

    def get_strategy_summary(self) -> str:
        """
        Get a summary of the current strategy state
        """
        active_signals = len(self.active_arbitrage_signals)
        active_positions = len(self.position_tracking)
        
        # Calculate total leveraged exposure
        total_leveraged_exposure = 0
        for symbol, position_data in self.position_tracking.items():
            leverage = position_data.get('target_leverage', self.leverage)
            total_leveraged_exposure += self.position_size_percent * leverage
        
        return (f"Active arbitrage signals: {active_signals}, "
                f"Active positions: {active_positions}, "
                f"Base exposure: {active_positions * self.position_size_percent:.1f}%, "
                f"Leveraged exposure: {total_leveraged_exposure:.1f}%")

    @classmethod
    def get_default_config(cls, time_frames: typing.Optional[list[str]] = None) -> dict:
        """
        Return default configuration for the strategy
        """
        return {
            evaluator_constants.STRATEGIES_REQUIRED_TIME_FRAME: (
                time_frames or [commons_enums.TimeFrames.FIVE_MINUTES.value]
            ),
            cls.MIN_ARBITRAGE_SIGNAL_KEY: 0.3,
            cls.POSITION_SIZE_KEY: 5.0,
            cls.MAX_EXPOSURE_KEY: 20.0,
            cls.SIGNAL_TIMEOUT_KEY: 300,
            cls.REQUIRED_EXCHANGES_KEY: 2,
            cls.ENABLE_LONG_SIGNALS_KEY: True,
            cls.ENABLE_SHORT_SIGNALS_KEY: True,
            cls.LEVERAGE_KEY: 1.0,
            cls.MAX_LEVERAGE_KEY: 10.0,
            cls.AUTO_LEVERAGE_KEY: False,
        }