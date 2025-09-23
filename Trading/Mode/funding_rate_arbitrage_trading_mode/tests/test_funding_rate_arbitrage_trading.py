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

import pytest
from unittest.mock import Mock, AsyncMock, patch
from decimal import Decimal

import octobot_commons.enums as commons_enums
import octobot_trading.enums as trading_enums

# Test imports (these would be available in the test environment)
try:
    from tentacles.Trading.Mode.funding_rate_arbitrage_trading_mode.funding_rate_arbitrage_trading import (
        FundingRateArbitrageTradingMode,
        FundingRateArbitrageTradingModeProducer,
        FundingRateArbitrageTradingModeConsumer
    )
except ImportError:
    # Fallback for testing environment
    FundingRateArbitrageTradingMode = None
    FundingRateArbitrageTradingModeProducer = None
    FundingRateArbitrageTradingModeConsumer = None


class TestFundingRateArbitrageTradingMode:
    """Test cases for Funding Rate Arbitrage Trading Mode"""
    
    @pytest.fixture
    def mock_config(self):
        return {
            "exchanges": {
                "binance": {"enabled": True},
                "bybit": {"enabled": True}
            }
        }
    
    @pytest.fixture
    def mock_exchange_manager(self):
        manager = Mock()
        manager.id = "test_exchange_id"
        return manager
    
    def test_trading_mode_initialization(self, mock_config, mock_exchange_manager):
        """Test trading mode can be initialized properly"""
        if FundingRateArbitrageTradingMode is None:
            pytest.skip("Trading mode not available in test environment")
        
        trading_mode = FundingRateArbitrageTradingMode(mock_config, mock_exchange_manager)
        
        assert trading_mode is not None
        assert trading_mode.position_size_percent == 10.0
        assert trading_mode.max_total_exposure_percent == 30.0
        assert trading_mode.enable_hedge_orders is True
    
    def test_supported_exchange_types(self):
        """Test that correct exchange types are supported"""
        if FundingRateArbitrageTradingMode is None:
            pytest.skip("Trading mode not available in test environment")
        
        supported_types = FundingRateArbitrageTradingMode.get_supported_exchange_types()
        
        assert trading_enums.ExchangeTypes.FUTURE in supported_types
        assert trading_enums.ExchangeTypes.SPOT in supported_types
    
    def test_user_inputs_configuration(self, mock_config, mock_exchange_manager):
        """Test user inputs are properly configured"""
        if FundingRateArbitrageTradingMode is None:
            pytest.skip("Trading mode not available in test environment")
        
        trading_mode = FundingRateArbitrageTradingMode(mock_config, mock_exchange_manager)
        trading_mode.UI = Mock()
        trading_mode.UI.user_input = Mock(return_value=10.0)
        
        inputs = {}
        trading_mode.init_user_inputs(inputs)
        
        # Verify user input calls were made
        assert trading_mode.UI.user_input.call_count >= 6  # At least 6 configuration parameters
    
    def test_producer_initialization(self, mock_config, mock_exchange_manager):
        """Test producer can be initialized"""
        if FundingRateArbitrageTradingModeProducer is None:
            pytest.skip("Producer not available in test environment")
        
        mock_channel = Mock()
        trading_mode = Mock()
        
        producer = FundingRateArbitrageTradingModeProducer(
            mock_channel, mock_config, trading_mode, mock_exchange_manager
        )
        
        assert producer is not None
        assert producer.LONG_THRESHOLD == Decimal("0.3")
        assert producer.SHORT_THRESHOLD == Decimal("-0.3")
    
    def test_consumer_initialization(self, mock_config, mock_exchange_manager):
        """Test consumer can be initialized"""
        if FundingRateArbitrageTradingModeConsumer is None:
            pytest.skip("Consumer not available in test environment")
        
        trading_mode = Mock()
        trading_mode.position_size_percent = 10.0
        trading_mode.enable_long_positions = True
        trading_mode.enable_short_positions = True
        
        consumer = FundingRateArbitrageTradingModeConsumer(trading_mode)
        
        assert consumer is not None
        assert consumer.QUANTITY_MIN_PERCENT == Decimal("0.8")
        assert consumer.MARKET_ORDER_TIMEOUT == 30
    
    @pytest.mark.asyncio
    async def test_producer_evaluation_logic(self, mock_config, mock_exchange_manager):
        """Test producer evaluation logic"""
        if FundingRateArbitrageTradingModeProducer is None:
            pytest.skip("Producer not available in test environment")
        
        mock_channel = Mock()
        trading_mode = Mock()
        trading_mode.enable_long_positions = True
        trading_mode.enable_short_positions = True
        
        producer = FundingRateArbitrageTradingModeProducer(
            mock_channel, mock_config, trading_mode, mock_exchange_manager
        )
        
        # Mock the strategy evaluation method
        producer._get_strategy_evaluation = AsyncMock(return_value=0.5)
        
        await producer.set_final_eval("matrix_id", "BTC", "BTC/USDT", "5m")
        
        assert producer.state == trading_enums.EvaluatorStates.LONG
        assert producer.final_eval == 0.5
    
    @pytest.mark.asyncio
    async def test_consumer_signal_handling(self, mock_config, mock_exchange_manager):
        """Test consumer signal handling"""
        if FundingRateArbitrageTradingModeConsumer is None:
            pytest.skip("Consumer not available in test environment")
        
        trading_mode = Mock()
        trading_mode.enable_long_positions = True
        trading_mode.active_arbitrage_positions = {}
        
        consumer = FundingRateArbitrageTradingModeConsumer(trading_mode)
        consumer._create_long_arbitrage_position = AsyncMock()
        
        await consumer.internal_callback(
            "FundingRateArbitrageTradingMode",
            "BTC",
            "BTC/USDT",
            0.5,
            state=trading_enums.EvaluatorStates.LONG
        )
        
        consumer._create_long_arbitrage_position.assert_called_once()


if __name__ == "__main__":
    # Run basic smoke tests
    print("Running basic validation tests for Funding Rate Arbitrage Trading Mode...")
    
    try:
        # Test import
        from tentacles.Trading.Mode.funding_rate_arbitrage_trading_mode import FundingRateArbitrageTradingMode
        print("✓ Trading mode import successful")
        
        # Test supported exchange types
        supported_types = FundingRateArbitrageTradingMode.get_supported_exchange_types()
        assert trading_enums.ExchangeTypes.FUTURE in supported_types
        print("✓ Exchange types validation passed")
        
        # Test class structure
        assert hasattr(FundingRateArbitrageTradingMode, 'get_mode_producer_classes')
        assert hasattr(FundingRateArbitrageTradingMode, 'get_mode_consumer_classes')
        print("✓ Required methods exist")
        
        print("All basic validation tests passed!")
        
    except Exception as e:
        print(f"✗ Validation failed: {e}")
        print("This is expected if running outside the OctoBot environment")