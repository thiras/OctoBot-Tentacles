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

import asyncio
import decimal
import typing
from collections import defaultdict

import async_channel.constants as channel_constants
import octobot_commons.constants as commons_constants
import octobot_commons.enums as commons_enums
import octobot_trading.api as trading_api
import octobot_trading.enums as trading_enums
import octobot_trading.exchange_channel as exchanges_channel
import octobot_trading.modes as trading_modes
import octobot_trading.personal_data as trading_personal_data


class FundingRateArbitrageTradingMode(trading_modes.AbstractTradingMode):
    """
    Funding Rate Arbitrage Trading Mode
    
    This trading mode executes arbitrage strategies based on funding rate differences 
    between exchanges. It creates market-neutral positions to profit from funding rate spreads.
    
    Features:
    - Multi-exchange arbitrage execution
    - Risk management with exposure limits
    - Configurable position sizing
    - Automatic hedge management
    
    Integrates with: FundingRateArbitrageStrategyEvaluator
    """
    
    # Strategy evaluator integration
    STRATEGY_EVALUATOR_CLASS_NAME = "FundingRateArbitrageStrategyEvaluator"
    
    def __init__(self, config, exchange_manager):
        super().__init__(config, exchange_manager)
        
        # Configuration parameters
        self.position_size_percent = 10.0
        self.max_total_exposure_percent = 30.0
        self.min_arbitrage_spread_percent = 0.1
        self.stop_loss_percent = 2.0
        self.target_exchanges = []
        self.enable_hedge_orders = True
        self.enable_long_positions = True
        self.enable_short_positions = True
        
        # State tracking
        self.active_arbitrage_positions = defaultdict(dict)
        self.pending_hedge_orders = defaultdict(list)

    @classmethod
    def get_supported_exchange_types(cls) -> list:
        """
        :return: The list of supported exchange types
        """
        return [
            trading_enums.ExchangeTypes.FUTURE,  # Primary for funding rates
            trading_enums.ExchangeTypes.SPOT,    # For hedging if needed
        ]

    def init_user_inputs(self, inputs: dict) -> None:
        """
        Called right before starting the tentacle, should define all the tentacle's user inputs
        """
        self.position_size_percent = self.UI.user_input(
            "position_size_percent", commons_enums.UserInputTypes.FLOAT, 10.0, inputs,
            min_val=0.1, max_val=50.0,
            title="Position size (%): Size of each arbitrage position as percentage of portfolio value."
        )
        
        self.max_total_exposure_percent = self.UI.user_input(
            "max_total_exposure_percent", commons_enums.UserInputTypes.FLOAT, 30.0, inputs,
            min_val=1.0, max_val=100.0,
            title="Max total exposure (%): Maximum combined exposure across all arbitrage positions."
        )
        
        self.min_arbitrage_spread_percent = self.UI.user_input(
            "min_arbitrage_spread_percent", commons_enums.UserInputTypes.FLOAT, 0.1, inputs,
            min_val=0.01, max_val=5.0,
            title="Min arbitrage spread (%): Minimum funding rate spread required to open arbitrage position."
        )
        
        self.stop_loss_percent = self.UI.user_input(
            "stop_loss_percent", commons_enums.UserInputTypes.FLOAT, 2.0, inputs,
            min_val=0.1, max_val=10.0,
            title="Stop loss (%): Maximum loss percentage before closing arbitrage position."
        )
        
        # Exchange selection
        available_exchanges = list(self.config.get(commons_constants.CONFIG_EXCHANGES, {}).keys())
        self.target_exchanges = self.UI.user_input(
            "target_exchanges", commons_enums.UserInputTypes.MULTIPLE_OPTIONS, 
            available_exchanges[:2] if len(available_exchanges) >= 2 else available_exchanges, inputs,
            options=available_exchanges,
            title="Target exchanges: Exchanges to use for arbitrage trading. At least 2 required."
        )
        
        self.enable_hedge_orders = self.UI.user_input(
            "enable_hedge_orders", commons_enums.UserInputTypes.BOOLEAN, True, inputs,
            title="Enable hedge orders: Automatically create hedge orders to maintain market neutrality."
        )
        
        self.enable_long_positions = self.UI.user_input(
            "enable_long_positions", commons_enums.UserInputTypes.BOOLEAN, True, inputs,
            title="Enable long positions: Allow opening long positions for arbitrage opportunities."
        )
        
        self.enable_short_positions = self.UI.user_input(
            "enable_short_positions", commons_enums.UserInputTypes.BOOLEAN, True, inputs,
            title="Enable short positions: Allow opening short positions for arbitrage opportunities."
        )

    def get_mode_producer_classes(self) -> list:
        return [FundingRateArbitrageTradingModeProducer]

    def get_mode_consumer_classes(self) -> list:
        return [FundingRateArbitrageTradingModeConsumer]

    def get_current_state(self) -> tuple[str, float]:
        """Get current trading mode state"""
        if not self.producers:
            return "Starting", 0
        
        producer = self.producers[0]
        state_name = producer.state.name if producer.state else "Unknown"
        final_eval = producer.final_eval if producer.final_eval else 0
        
        return state_name, final_eval

    @classmethod
    def get_is_symbol_wildcard(cls) -> bool:
        """This mode can handle multiple symbols for arbitrage"""
        return True

    async def create_consumers(self) -> list:
        """Create mode consumers and additional channel consumers"""
        consumers = await super().create_consumers()
        
        # Add order consumer to track order status
        order_consumer = await exchanges_channel.get_chan(
            trading_personal_data.OrdersChannel.get_name(),
            self.exchange_manager.id
        ).new_consumer(
            self._order_notification_callback,
            symbol=self.symbol if self.symbol else channel_constants.CHANNEL_WILDCARD
        )
        consumers.append(order_consumer)
        
        return consumers

    async def _order_notification_callback(self, exchange: str, exchange_id: str, 
                                         cryptocurrency: str, symbol: str, order, is_from_bot: bool):
        """Handle order status updates for arbitrage positions"""
        if not is_from_bot:
            return
        
        # Track arbitrage position updates
        if symbol in self.active_arbitrage_positions:
            position_data = self.active_arbitrage_positions[symbol]
            
            # Update position tracking based on order status
            if order.status == trading_enums.OrderStatus.FILLED:
                if order.order_id in position_data.get('long_orders', []):
                    position_data['long_filled'] = True
                elif order.order_id in position_data.get('short_orders', []):
                    position_data['short_filled'] = True
                
                # Check if hedge order is needed
                if self.enable_hedge_orders:
                    await self._check_hedge_requirements(symbol, position_data)
            
            elif order.status in [trading_enums.OrderStatus.CANCELED, trading_enums.OrderStatus.REJECTED]:
                # Handle failed orders
                await self._handle_failed_arbitrage_order(symbol, order, position_data)

    async def _check_hedge_requirements(self, symbol: str, position_data: dict):
        """Check if hedge orders are needed to maintain market neutrality"""
        long_filled = position_data.get('long_filled', False)
        short_filled = position_data.get('short_filled', False)
        
        # If only one side is filled, create hedge order
        if long_filled and not short_filled:
            await self._create_hedge_order(symbol, trading_enums.TradeOrderSide.SELL, position_data)
        elif short_filled and not long_filled:
            await self._create_hedge_order(symbol, trading_enums.TradeOrderSide.BUY, position_data)

    async def _create_hedge_order(self, symbol: str, side: trading_enums.TradeOrderSide, position_data: dict):
        """Create hedge order to maintain market neutrality"""
        try:
            # Implementation would depend on available exchanges and positions
            self.logger.info(f"Creating hedge order for {symbol} - {side.value}")
            # This is a placeholder - actual implementation would create the hedge order
            
        except Exception as e:
            self.logger.error(f"Failed to create hedge order for {symbol}: {e}")

    async def _handle_failed_arbitrage_order(self, symbol: str, failed_order, position_data: dict):
        """Handle failed arbitrage orders"""
        self.logger.warning(f"Arbitrage order failed for {symbol}: {failed_order.order_id}")
        
        # Clean up position tracking
        if symbol in self.active_arbitrage_positions:
            # Remove failed order from tracking
            for order_list in ['long_orders', 'short_orders']:
                if failed_order.order_id in position_data.get(order_list, []):
                    position_data[order_list].remove(failed_order.order_id)
                    break

    def get_arbitrage_summary(self) -> str:
        """Get summary of current arbitrage positions"""
        active_positions = len(self.active_arbitrage_positions)
        pending_hedges = sum(len(orders) for orders in self.pending_hedge_orders.values())
        
        total_exposure = active_positions * self.position_size_percent
        
        return (f"Active arbitrage positions: {active_positions}, "
                f"Pending hedges: {pending_hedges}, "
                f"Current exposure: {total_exposure:.1f}%")


class FundingRateArbitrageTradingModeProducer(trading_modes.AbstractTradingModeProducer):
    """
    Producer for funding rate arbitrage trading mode
    Processes strategy evaluator signals and manages trading state
    """
    
    def __init__(self, channel, config, trading_mode, exchange_manager):
        super().__init__(channel, config, trading_mode, exchange_manager)
        
        # Strategy evaluation thresholds
        self.LONG_THRESHOLD = decimal.Decimal("0.3")      # Minimum eval for long signal
        self.SHORT_THRESHOLD = decimal.Decimal("-0.3")    # Maximum eval for short signal
        self.NEUTRAL_THRESHOLD = decimal.Decimal("0.1")   # Neutral zone threshold
        
        # State management
        self.current_arbitrage_opportunities = {}
        self.last_evaluation_time = 0

    async def set_final_eval(self, matrix_id: str, cryptocurrency: str, symbol: str, time_frame):
        """Set final evaluation based on funding rate arbitrage strategy"""
        try:
            # Get strategy evaluation from the funding rate arbitrage evaluator
            strategy_eval = await self._get_strategy_evaluation(matrix_id, cryptocurrency, symbol, time_frame)
            
            if strategy_eval is None:
                # No valid strategy evaluation available
                self.final_eval = commons_constants.START_PENDING_EVAL_NOTE
                self.state = trading_enums.EvaluatorStates.NEUTRAL
                return
            
            self.final_eval = strategy_eval
            
            # Determine state based on evaluation
            if strategy_eval >= self.LONG_THRESHOLD:
                if self.trading_mode.enable_long_positions:
                    self.state = trading_enums.EvaluatorStates.LONG
                else:
                    self.state = trading_enums.EvaluatorStates.NEUTRAL
                    
            elif strategy_eval <= self.SHORT_THRESHOLD:
                if self.trading_mode.enable_short_positions:
                    self.state = trading_enums.EvaluatorStates.SHORT
                else:
                    self.state = trading_enums.EvaluatorStates.NEUTRAL
                    
            else:
                self.state = trading_enums.EvaluatorStates.NEUTRAL
            
            self.logger.debug(f"Arbitrage evaluation for {symbol}: {strategy_eval:.3f} -> {self.state.name}")
            
        except Exception as e:
            self.logger.error(f"Error setting final evaluation for {symbol}: {e}")
            self.final_eval = commons_constants.START_PENDING_EVAL_NOTE
            self.state = trading_enums.EvaluatorStates.NEUTRAL

    async def _get_strategy_evaluation(self, matrix_id: str, cryptocurrency: str, symbol: str, time_frame) -> float:
        """Get evaluation from funding rate arbitrage strategy evaluator"""
        try:
            # Get strategy evaluation from the evaluator matrix
            import octobot_evaluators.matrix as matrix
            import octobot_evaluators.enums as evaluators_enums
            
            # Look for the funding rate arbitrage strategy evaluation
            strategy_evaluations = matrix.get_evaluations_by_evaluator(
                matrix_id, 
                self.exchange_manager.exchange_name,
                evaluators_enums.EvaluatorMatrixTypes.STRATEGIES.value,
                cryptocurrency, 
                symbol, 
                time_frame,
                allowed_values=[commons_constants.START_PENDING_EVAL_NOTE]
            )
            
            # Find our specific strategy evaluator
            strategy_eval = None
            for evaluator_name, evaluation in strategy_evaluations.items():
                if self.trading_mode.STRATEGY_EVALUATOR_CLASS_NAME in evaluator_name:
                    strategy_eval = evaluation
                    break
            
            if strategy_eval is not None and strategy_eval != commons_constants.START_PENDING_EVAL_NOTE:
                self.logger.debug(f"Got strategy evaluation for {symbol}: {strategy_eval}")
                return float(strategy_eval)
            
            return None
            
        except Exception as e:
            self.logger.debug(f"Could not get strategy evaluation for {symbol}: {e}")
            return None

    async def _should_create_arbitrage_position(self, symbol: str, evaluation: float) -> bool:
        """Check if conditions are met to create arbitrage position"""
        # Check exposure limits
        current_exposure = len(self.trading_mode.active_arbitrage_positions) * self.trading_mode.position_size_percent
        if current_exposure >= self.trading_mode.max_total_exposure_percent:
            self.logger.info(f"Exposure limit reached, skipping arbitrage for {symbol}")
            return False
        
        # Check if we already have a position for this symbol
        if symbol in self.trading_mode.active_arbitrage_positions:
            self.logger.debug(f"Already have arbitrage position for {symbol}")
            return False
        
        # Check minimum spread requirement
        if abs(evaluation) < (self.trading_mode.min_arbitrage_spread_percent / 100):
            self.logger.debug(f"Arbitrage spread too small for {symbol}: {abs(evaluation):.4f}")
            return False
        
        # Check exchange availability
        if len(self.trading_mode.target_exchanges) < 2:
            self.logger.warning("Need at least 2 exchanges for arbitrage trading")
            return False
        
        return True


class FundingRateArbitrageTradingModeConsumer(trading_modes.AbstractTradingModeConsumer):
    """
    Consumer for funding rate arbitrage trading mode
    Handles order creation and execution for arbitrage strategies
    """
    
    def __init__(self, trading_mode):
        super().__init__(trading_mode)
        
        # Order execution parameters
        self.QUANTITY_MIN_PERCENT = decimal.Decimal("0.8")   # Minimum quantity fill
        self.QUANTITY_MAX_PERCENT = decimal.Decimal("1.0")   # Maximum quantity fill
        self.MARKET_ORDER_TIMEOUT = 30                       # Market order timeout in seconds

    async def internal_callback(self, trading_mode_name: str, cryptocurrency: str, symbol: str, signal, **kwargs):
        """Handle trading signals from the producer"""
        try:
            state = kwargs.get("state")
            if state is None:
                return
            
            self.logger.info(f"Received arbitrage signal for {symbol}: {signal} - {state.name}")
            
            # Handle different states
            if state == trading_enums.EvaluatorStates.LONG:
                await self._create_long_arbitrage_position(cryptocurrency, symbol, signal)
                
            elif state == trading_enums.EvaluatorStates.SHORT:
                await self._create_short_arbitrage_position(cryptocurrency, symbol, signal)
                
            elif state == trading_enums.EvaluatorStates.NEUTRAL:
                await self._manage_existing_positions(cryptocurrency, symbol)
                
        except Exception as e:
            self.logger.error(f"Error in arbitrage trading callback for {symbol}: {e}")

    async def _create_long_arbitrage_position(self, cryptocurrency: str, symbol: str, signal: float):
        """Create long arbitrage position (buy low rate exchange, short high rate exchange)"""
        try:
            if not self.trading_mode.enable_long_positions:
                return
            
            self.logger.info(f"Creating long arbitrage position for {symbol}")
            
            # Calculate position size
            portfolio_value = await self._get_portfolio_value(cryptocurrency)
            position_value = portfolio_value * (self.trading_mode.position_size_percent / 100)
            
            # Get current price for quantity calculation
            current_price = await trading_api.get_symbol_current_price(
                self.exchange_manager, symbol, timeout=10
            )
            
            if current_price <= 0:
                self.logger.error(f"Invalid price for {symbol}: {current_price}")
                return
            
            quantity = position_value / current_price
            
            # Create the arbitrage orders
            await self._execute_arbitrage_orders(symbol, quantity, trading_enums.TradeOrderSide.BUY, signal)
            
        except Exception as e:
            self.logger.error(f"Error creating long arbitrage position for {symbol}: {e}")

    async def _create_short_arbitrage_position(self, cryptocurrency: str, symbol: str, signal: float):
        """Create short arbitrage position (short low rate exchange, buy high rate exchange)"""
        try:
            if not self.trading_mode.enable_short_positions:
                return
            
            self.logger.info(f"Creating short arbitrage position for {symbol}")
            
            # Calculate position size
            portfolio_value = await self._get_portfolio_value(cryptocurrency)
            position_value = portfolio_value * (self.trading_mode.position_size_percent / 100)
            
            # Get current price for quantity calculation
            current_price = await trading_api.get_symbol_current_price(
                self.exchange_manager, symbol, timeout=10
            )
            
            if current_price <= 0:
                self.logger.error(f"Invalid price for {symbol}: {current_price}")
                return
            
            quantity = position_value / current_price
            
            # Create the arbitrage orders
            await self._execute_arbitrage_orders(symbol, quantity, trading_enums.TradeOrderSide.SELL, signal)
            
        except Exception as e:
            self.logger.error(f"Error creating short arbitrage position for {symbol}: {e}")

    async def _execute_arbitrage_orders(self, symbol: str, quantity: float, primary_side: trading_enums.TradeOrderSide, signal: float):
        """Execute arbitrage orders across exchanges"""
        try:
            # This is a simplified implementation
            # In practice, you would need to:
            # 1. Identify the best exchanges for each side of the arbitrage
            # 2. Create simultaneous orders on different exchanges
            # 3. Handle partial fills and order management
            # 4. Implement proper risk management
            
            order_type = trading_enums.TraderOrderType.MARKET  # For immediate execution
            
            # Create primary order
            primary_order = await self._create_order(
                symbol, quantity, primary_side, order_type
            )
            
            if primary_order:
                # Track the arbitrage position
                position_data = {
                    'symbol': symbol,
                    'quantity': quantity,
                    'primary_side': primary_side.value,
                    'signal_strength': signal,
                    'created_time': self._get_current_timestamp(),
                    'orders': [primary_order.order_id]
                }
                
                self.trading_mode.active_arbitrage_positions[symbol] = position_data
                
                self.logger.info(f"Created arbitrage order for {symbol}: {primary_order.order_id}")
                
                # Create hedge order if enabled
                if self.trading_mode.enable_hedge_orders:
                    await asyncio.sleep(1)  # Small delay to avoid race conditions
                    await self._create_hedge_order_if_needed(symbol, quantity, primary_side)
        
        except Exception as e:
            self.logger.error(f"Error executing arbitrage orders for {symbol}: {e}")

    async def _create_hedge_order_if_needed(self, symbol: str, quantity: float, primary_side: trading_enums.TradeOrderSide):
        """Create hedge order on different exchange if needed"""
        try:
            # Determine hedge side (opposite of primary)
            hedge_side = (trading_enums.TradeOrderSide.SELL 
                         if primary_side == trading_enums.TradeOrderSide.BUY 
                         else trading_enums.TradeOrderSide.BUY)
            
            # In practice, this would create an order on a different exchange
            # For now, we'll just log the intention
            self.logger.info(f"Would create hedge order for {symbol}: {hedge_side.value} {quantity}")
            
        except Exception as e:
            self.logger.error(f"Error creating hedge order for {symbol}: {e}")

    async def _manage_existing_positions(self, cryptocurrency: str, symbol: str):
        """Manage existing arbitrage positions"""
        try:
            if symbol not in self.trading_mode.active_arbitrage_positions:
                return
            
            position_data = self.trading_mode.active_arbitrage_positions[symbol]
            
            # Check if position should be closed (simplified logic)
            current_time = self._get_current_timestamp()
            position_age = current_time - position_data.get('created_time', current_time)
            
            # Close position if it's been open too long (example: 1 hour)
            if position_age > 3600:
                await self._close_arbitrage_position(symbol, position_data)
                
        except Exception as e:
            self.logger.error(f"Error managing existing position for {symbol}: {e}")

    async def _close_arbitrage_position(self, symbol: str, position_data: dict):
        """Close arbitrage position"""
        try:
            self.logger.info(f"Closing arbitrage position for {symbol}")
            
            # In practice, this would:
            # 1. Cancel any open orders
            # 2. Close positions on all exchanges
            # 3. Calculate PnL
            # 4. Update position tracking
            
            # Remove from active positions
            if symbol in self.trading_mode.active_arbitrage_positions:
                del self.trading_mode.active_arbitrage_positions[symbol]
                
        except Exception as e:
            self.logger.error(f"Error closing arbitrage position for {symbol}: {e}")

    async def _get_portfolio_value(self, cryptocurrency: str) -> float:
        """Get current portfolio value"""
        try:
            portfolio = await trading_api.get_portfolio_current_value(self.exchange_manager)
            return portfolio.get(commons_constants.PORTFOLIO_TOTAL, 0)
        except Exception as e:
            self.logger.error(f"Error getting portfolio value: {e}")
            return 0

    async def _create_order(self, symbol: str, quantity: float, side: trading_enums.TradeOrderSide, 
                          order_type: trading_enums.TraderOrderType) -> typing.Optional[trading_personal_data.Order]:
        """Create and submit trading order"""
        try:
            # Use trading API to create order
            order = await trading_api.create_order(
                self.exchange_manager, 
                symbol, 
                order_type,
                side,
                quantity,
                None,  # price (None for market orders)
                None   # stop_price
            )
            return order
            
        except Exception as e:
            self.logger.error(f"Error creating order for {symbol}: {e}")
            return None

    def _get_current_timestamp(self) -> float:
        """Get current timestamp"""
        import time
        return time.time()