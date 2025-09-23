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

from collections import defaultdict
from typing import Dict, List, Optional

import octobot_commons.constants as commons_constants
import octobot_commons.enums as commons_enums
import octobot_evaluators.evaluators as evaluators
import octobot_evaluators.util as evaluators_util
import octobot_trading.api as trading_api


class FundingRateArbitrageEvaluator(evaluators.RealTimeEvaluator):
    """
    Funding Rate Arbitrage Evaluator

    This evaluator monitors funding rates across different exchanges for the same symbol
    to identify arbitrage opportunities. When funding rates significantly differ between
    exchanges, it suggests taking opposite positions to profit from the rate differential.

    Strategy:
    - Long on exchange with negative funding rate (receive funding)
    - Short on exchange with positive funding rate (pay less or receive funding)
    - Profit from the spread while being market neutral
    """

    # Configuration keys
    RATE_THRESHOLD_KEY = "rate_difference_threshold_percent"
    MIN_RATE_ABSOLUTE_KEY = "min_rate_absolute_percent"
    MAX_EXCHANGES_KEY = "max_exchanges_to_compare"
    EVALUATION_INTERVAL_KEY = "evaluation_interval_minutes"
    MIN_LIQUIDITY_KEY = "min_liquidity_threshold"

    def __init__(self, tentacles_setup_config):
        super().__init__(tentacles_setup_config)

        # Time frame (required for RealTimeEvaluator)
        self.time_frame = None

        # Configuration parameters
        self.rate_difference_threshold = 0.008  # 0.8% difference threshold
        self.min_rate_absolute = 0.005  # 0.5% minimum absolute rate
        self.max_exchanges = 5  # Maximum number of exchanges to compare
        self.evaluation_interval = 1  # minutes
        self.min_liquidity_threshold = 100000  # Minimum liquidity in USD

        # State tracking
        self.funding_rates = defaultdict(dict)  # {exchange: {symbol: rate}}
        self.last_evaluation_time = defaultdict(float)  # {symbol: timestamp}
        self.arbitrage_opportunities = defaultdict(list)  # {symbol: [opportunities]}

        # Evaluation tracking
        self.last_notification_eval = 0
        self.current_opportunities = {}

    def init_user_inputs(self, inputs: dict) -> None:
        """
        Called right before starting the tentacle, should define all the tentacle's user inputs
        """
        # Time frame configuration (required for RealTimeEvaluator)
        self.time_frame = self.time_frame or self.UI.user_input(
            commons_constants.CONFIG_TIME_FRAME,
            commons_enums.UserInputTypes.OPTIONS,
            commons_enums.TimeFrames.FIVE_MINUTES.value,
            inputs,
            options=[tf.value for tf in commons_enums.TimeFrames],
            title="Time frame: The time frame to observe for funding rate updates.",
        )

        self.rate_difference_threshold = (
            self.UI.user_input(
                self.RATE_THRESHOLD_KEY,
                commons_enums.UserInputTypes.FLOAT,
                1.0,
                inputs,
                min_val=0.1,
                max_val=10.0,
                title="Rate difference threshold (%): Minimum funding rate difference between exchanges to trigger arbitrage signal.",
            )
            / 100
        )

        self.min_rate_absolute = (
            self.UI.user_input(
                self.MIN_RATE_ABSOLUTE_KEY,
                commons_enums.UserInputTypes.FLOAT,
                0.5,
                inputs,
                min_val=0.01,
                max_val=5.0,
                title="Minimum absolute rate (%): Minimum absolute funding rate to consider for arbitrage.",
            )
            / 100
        )

        self.max_exchanges = self.UI.user_input(
            self.MAX_EXCHANGES_KEY,
            commons_enums.UserInputTypes.INT,
            5,
            inputs,
            min_val=2,
            max_val=10,
            title="Max exchanges: Maximum number of exchanges to compare for arbitrage opportunities.",
        )

        self.evaluation_interval = self.UI.user_input(
            self.EVALUATION_INTERVAL_KEY,
            commons_enums.UserInputTypes.INT,
            5,
            inputs,
            min_val=1,
            max_val=60,
            title="Evaluation interval (minutes): How often to check for arbitrage opportunities.",
        )

        self.min_liquidity_threshold = self.UI.user_input(
            self.MIN_LIQUIDITY_KEY,
            commons_enums.UserInputTypes.FLOAT,
            100000,
            inputs,
            min_val=1000,
            max_val=10000000,
            title="Minimum liquidity (USD): Minimum liquidity required to consider arbitrage opportunity.",
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
        """
        Called when new OHLCV data is available. We use this to trigger funding rate checks.
        """
        await self._update_funding_rates(exchange, exchange_id, symbol)
        await self._evaluate_arbitrage_opportunities(
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
        """
        Called when new kline data is available. We use this to trigger funding rate checks.
        """
        await self._update_funding_rates(exchange, exchange_id, symbol)
        await self._evaluate_arbitrage_opportunities(
            cryptocurrency, symbol, evaluators_util.get_eval_time(kline=kline)
        )

    async def _update_funding_rates(self, exchange: str, exchange_id: str, symbol: str):
        """
        Update funding rates for the given exchange and symbol
        """
        try:
            # Get funding rate from the exchange
            funding_rate = await self._get_funding_rate(exchange, exchange_id, symbol)
            if funding_rate is not None:
                self.funding_rates[exchange][symbol] = funding_rate
                self.logger.debug(
                    f"Updated funding rate for {symbol} on {exchange}: {funding_rate:.6f}"
                )
        except Exception as e:
            self.logger.debug(
                f"Failed to get funding rate for {symbol} on {exchange}: {e}"
            )

    async def _get_funding_rate(
        self, exchange: str, exchange_id: str, symbol: str
    ) -> Optional[float]:
        """
        Get the current funding rate for a symbol on an exchange from the local funding_rates cache.
        """
        return self.funding_rates.get(exchange, {}).get(symbol)

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
        # Update the funding_rates cache with the latest funding rate
        try:
            if funding_rate is not None:
                self.funding_rates[exchange][symbol] = float(funding_rate)
                self.logger.debug(
                    f"[FundingChannel] Updated funding rate for {symbol} on {exchange}: {funding_rate}"
                )
        except Exception as e:
            self.logger.debug(f"[FundingChannel] Error updating funding rate: {e}")

    async def _evaluate_arbitrage_opportunities(
        self, cryptocurrency: str, symbol: str, eval_time
    ):
        """
        Evaluate arbitrage opportunities based on funding rate differences
        """
        current_time = eval_time

        # Check if enough time has passed since last evaluation
        if (current_time - self.last_evaluation_time[symbol]) < (
            self.evaluation_interval * 60
        ):
            return

        self.last_evaluation_time[symbol] = current_time

        # Get funding rates for this symbol across all exchanges
        symbol_rates = {}
        for exchange, rates in self.funding_rates.items():
            if symbol in rates:
                symbol_rates[exchange] = rates[symbol]

        if len(symbol_rates) < 2:
            self.logger.debug(f"Not enough exchanges with funding rates for {symbol}")
            return

        # Find arbitrage opportunities
        opportunities = await self._find_arbitrage_opportunities(symbol, symbol_rates)

        if opportunities:
            self.current_opportunities[symbol] = opportunities
            await self._calculate_evaluation_score(
                cryptocurrency, symbol, opportunities, eval_time
            )
        else:
            # Clear opportunities if none found
            self.current_opportunities.pop(symbol, None)
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE

    async def _find_arbitrage_opportunities(
        self, symbol: str, rates: Dict[str, float]
    ) -> List[Dict]:
        """
        Find arbitrage opportunities from funding rate differences
        """
        opportunities = []
        exchanges = list(rates.keys())

        # Compare all pairs of exchanges
        for i, exchange1 in enumerate(exchanges):
            for exchange2 in exchanges[i + 1 :]:
                rate1 = rates[exchange1]
                rate2 = rates[exchange2]

                # Calculate the absolute difference
                rate_diff = abs(rate1 - rate2)

                # Check if difference meets threshold and minimum absolute rate
                if rate_diff >= self.rate_difference_threshold and (
                    abs(rate1) >= self.min_rate_absolute
                    or abs(rate2) >= self.min_rate_absolute
                ):

                    # Determine which exchange to long/short
                    if rate1 < rate2:
                        long_exchange = exchange1
                        short_exchange = exchange2
                        long_rate = rate1
                        short_rate = rate2
                    else:
                        long_exchange = exchange2
                        short_exchange = exchange1
                        long_rate = rate2
                        short_rate = rate1

                    # Check liquidity if possible
                    liquidity_check = await self._check_liquidity(
                        symbol, long_exchange, short_exchange
                    )

                    opportunity = {
                        "symbol": symbol,
                        "long_exchange": long_exchange,
                        "short_exchange": short_exchange,
                        "long_rate": long_rate,
                        "short_rate": short_rate,
                        "rate_difference": rate_diff,
                        "expected_profit": rate_diff,  # Simplified profit calculation
                        "liquidity_sufficient": liquidity_check,
                        "confidence": self._calculate_confidence(
                            rate_diff, long_rate, short_rate
                        ),
                    }

                    opportunities.append(opportunity)

        # Sort by expected profit (rate difference) descending
        opportunities.sort(key=lambda x: x["rate_difference"], reverse=True)

        # Limit to max exchanges
        return opportunities[: self.max_exchanges]

    async def _check_liquidity(
        self, symbol: str, long_exchange: str, short_exchange: str
    ) -> bool:
        """
        Check if there's sufficient liquidity on both exchanges
        """
        try:
            # This is a simplified liquidity check
            # In a real implementation, you'd check order book depth
            return True  # Placeholder - assume sufficient liquidity
        except Exception as e:
            self.logger.debug(f"Error checking liquidity for {symbol}: {e}")
            return False

    def _calculate_confidence(
        self, rate_diff: float, long_rate: float, short_rate: float
    ) -> float:
        """
        Calculate confidence score for the arbitrage opportunity
        """
        # Higher rate difference = higher confidence
        rate_factor = min(rate_diff / (self.rate_difference_threshold * 2), 1.0)

        # Higher absolute rates = higher confidence
        abs_rate_factor = min(
            (abs(long_rate) + abs(short_rate)) / (self.min_rate_absolute * 4), 1.0
        )

        # Combine factors
        confidence = (rate_factor * 0.7) + (abs_rate_factor * 0.3)

        return min(confidence, 1.0)

    async def _calculate_evaluation_score(
        self, cryptocurrency: str, symbol: str, opportunities: List[Dict], eval_time
    ):
        """
        Calculate evaluation score based on arbitrage opportunities
        """
        if not opportunities:
            self.eval_note = commons_constants.START_PENDING_EVAL_NOTE
            return

        # Get the best opportunity
        best_opportunity = opportunities[0]

        # Calculate evaluation score based on:
        # 1. Rate difference (higher = stronger signal)
        # 2. Confidence score
        # 3. Number of opportunities

        rate_diff = best_opportunity["rate_difference"]
        confidence = best_opportunity["confidence"]

        # Normalize rate difference to evaluation scale (-1 to 1)
        # Positive eval_note for profitable arbitrage opportunities
        base_score = min(rate_diff / (self.rate_difference_threshold * 3), 1.0)

        # Apply confidence multiplier
        final_score = base_score * confidence

        # Multiple opportunities increase strength
        if len(opportunities) > 1:
            final_score *= 1 + (len(opportunities) - 1) * 0.1

        # Cap at 1.0
        final_score = min(final_score, 1.0)

        # Set evaluation note
        self.eval_note = final_score

        # Trigger notification if significant change
        if (
            abs(self.last_notification_eval - self.eval_note) >= 0.1
        ):  # 10% change threshold
            self.last_notification_eval = self.eval_note
            await self.evaluation_completed(
                cryptocurrency, symbol, self.available_time_frame, eval_time=eval_time
            )

            # Log the opportunity
            self.logger.info(
                f"Funding rate arbitrage opportunity for {symbol}: "
                f"Long {best_opportunity['long_exchange']} ({best_opportunity['long_rate']:.6f}), "
                f"Short {best_opportunity['short_exchange']} ({best_opportunity['short_rate']:.6f}), "
                f"Difference: {best_opportunity['rate_difference']:.6f}, "
                f"Score: {final_score:.3f}"
            )

    async def start(self, bot_id: str) -> bool:
        """
        Start the evaluator and subscribe to the Funding channel for all exchanges.
        """
        self.logger.info(
            f"Starting Funding Rate Arbitrage Evaluator with threshold: {self.rate_difference_threshold:.4f}"
        )
        # Subscribe to the Funding channel for each exchange_id in the config
        try:
            import octobot_commons.channels_name as channels_name
            import octobot_trading.exchange_channel as exchanges_channel
            # If you have a list of exchange_ids, subscribe to each
            exchange_ids = self.config.get("exchange_ids")
            if not exchange_ids and hasattr(self, "exchange_ids"):
                exchange_ids = self.exchange_ids
            if not exchange_ids:
                # Fallback: try to get from tentacles_setup_config
                exchange_ids = self.tentacles_setup_config.get("exchange_ids", [])
            for exchange_id in exchange_ids:
                await exchanges_channel.get_chan(
                    channels_name.OctoBotTradingChannelsName.FUNDING_CHANNEL.value,
                    exchange_id
                ).new_consumer(
                    self.funding_channel_callback
                )
            self.logger.info(f"Subscribed to Funding channel for exchange_ids: {exchange_ids}")
        except Exception as e:
            self.logger.warning(f"Could not subscribe to Funding channel: {e}")
        return await super().start(bot_id)

    async def stop(self) -> None:
        """
        Stop the evaluator
        """
        self.logger.info("Stopping Funding Rate Arbitrage Evaluator")
        await super().stop()

    def get_current_opportunities(self) -> Dict:
        """
        Get current arbitrage opportunities for external access
        """
        return self.current_opportunities.copy()

    def get_evaluation_summary(self) -> str:
        """
        Get a summary of the current evaluation state
        """
        if not self.current_opportunities:
            return "No arbitrage opportunities detected"

        summary_lines = []
        for symbol, opportunities in self.current_opportunities.items():
            if opportunities:
                best = opportunities[0]
                summary_lines.append(
                    f"{symbol}: {best['rate_difference']:.4f}% spread "
                    f"({best['long_exchange']} vs {best['short_exchange']})"
                )

        return "; ".join(summary_lines) if summary_lines else "No opportunities"
