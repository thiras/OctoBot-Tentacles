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

import octobot_commons.symbols
import octobot_trading.exchanges as exchanges
import octobot_trading.enums as trading_enums


class HyperliquidConnector(exchanges.CCXTConnector):

    def _client_factory(
        self,
        force_unauth,
        keys_adapter: typing.Callable[
            [exchanges.ExchangeCredentialsData], exchanges.ExchangeCredentialsData
        ] = None,
    ) -> tuple:
        return super()._client_factory(force_unauth, keys_adapter=self._keys_adapter)

    def _keys_adapter(
        self, creds: exchanges.ExchangeCredentialsData
    ) -> exchanges.ExchangeCredentialsData:
        # use api key and secret as wallet address and private key
        creds.wallet_address = creds.api_key
        creds.private_key = creds.secret
        creds.api_key = creds.secret = None
        return creds


class Hyperliquid(exchanges.RestExchange):
    @classmethod
    def get_supported_exchange_types(cls) -> list:
        """
        :return: The list of supported exchange types
        """
        return [
            trading_enums.ExchangeTypes.SPOT,
            trading_enums.ExchangeTypes.FUTURE,
        ]

    SUPPORTED_ELEMENTS = {
        trading_enums.ExchangeTypes.FUTURE.value: {
            trading_enums.ExchangeSupportedElements.UNSUPPORTED_ORDERS.value: [
                trading_enums.TraderOrderType.STOP_LOSS_LIMIT,
                trading_enums.TraderOrderType.TAKE_PROFIT,
                trading_enums.TraderOrderType.TAKE_PROFIT_LIMIT,
                trading_enums.TraderOrderType.TRAILING_STOP,
                trading_enums.TraderOrderType.TRAILING_STOP_LIMIT,
            ],
            trading_enums.ExchangeSupportedElements.SUPPORTED_BUNDLED_ORDERS.value: {},
        },
        trading_enums.ExchangeTypes.SPOT.value: {
            trading_enums.ExchangeSupportedElements.UNSUPPORTED_ORDERS.value: [],
            trading_enums.ExchangeSupportedElements.SUPPORTED_BUNDLED_ORDERS.value: {},
        },
    }
    DESCRIPTION = ""
    DEFAULT_CONNECTOR_CLASS = HyperliquidConnector

    FIX_MARKET_STATUS = True
    REQUIRE_ORDER_FEES_FROM_TRADES = (
        True  # set True when get_order is not giving fees on closed orders and fees
    )
    # should be fetched using recent trades.

    def get_reference_market(self):
        """
        Override to return USDC as the reference market for Hyperliquid
        since all pairs use USDC/USD as quote currencies, not USDT
        """
        return "USDC"

    async def initialize_impl(self):
        await super().initialize_impl()
        
        # WebSocket symbol check
        if not self.symbols:
            self.logger.warning("Hyperliquid's websocket has no symbol to feed")
            return
        # log pairs as hyperliquid renames some of them (ex: ETH/USDC -> UETH/USDC)
        if self.exchange_manager.is_trading and not self.exchange_manager.exchange_only:
            spot_pairs = [
                pair
                for pair in self.symbols
                if octobot_commons.symbols.parse_symbol(pair).is_spot()
            ]
            futures_pairs = [
                pair
                for pair in self.symbols
                if octobot_commons.symbols.parse_symbol(pair).is_future()
            ]
            if spot_pairs:
                self.logger.info(
                    f"Hyperliquid {len(spot_pairs)} spot trading pairs: {sorted(spot_pairs)}"
                )
            if futures_pairs:
                self.logger.info(
                    f"Hyperliquid {len(futures_pairs)} futures trading pairs: {sorted(futures_pairs)}"
                )

    @classmethod
    def get_name(cls):
        return "hyperliquid"

    def get_adapter_class(self):
        return HyperLiquidCCXTAdapter

    async def get_symbol_leverage(self, symbol: str, **kwargs: dict):
        """
        Get the leverage for a specific symbol
        :param symbol: the symbol to get leverage for
        :return: the current leverage for the symbol
        """
        if symbol is None:
            self.logger.warning("get_symbol_leverage called with None symbol, returning default leverage")
            return 1.0
            
        try:
            # Try to get leverage from position first
            position = await self.get_position(symbol, **kwargs)
            if position and position.get(trading_enums.ExchangeConstantsPositionColumns.LEVERAGE.value):
                return float(position[trading_enums.ExchangeConstantsPositionColumns.LEVERAGE.value])
        except Exception as e:
            self.logger.debug(f"Could not get leverage from position for {symbol}: {e}")
        
        # Return default leverage if not found
        return 1.0
    
    async def load_pair_future_contract(self, pair: str):
        """
        Load and create a new FutureContract for the pair with validation
        :param pair: the contract pair
        """
        if pair is None:
            self.logger.warning("load_pair_future_contract called with None pair, skipping")
            return None
            
        try:
            return await super().load_pair_future_contract(pair)
        except Exception as e:
            self.logger.warning(f"Failed to load contract for {pair}: {e}")
            return None
    
    async def get_margin_type(self, symbol: str):
        """
        Get the margin type for a specific symbol
        :param symbol: the symbol to get margin type for
        :return: the margin type (default to CROSS)
        """
        if symbol is None:
            self.logger.warning("get_margin_type called with None symbol, returning CROSS")
            return trading_enums.MarginType.CROSS
            
        try:
            position = await self.get_position(symbol)
            if position and position.get(trading_enums.ExchangeConstantsPositionColumns.MARGIN_TYPE.value):
                return position[trading_enums.ExchangeConstantsPositionColumns.MARGIN_TYPE.value]
        except Exception as e:
            self.logger.debug(f"Could not get margin type from position for {symbol}: {e}")
        
        # Return default margin type
        return trading_enums.MarginType.CROSS
    
    async def get_position_mode(self, symbol: str):
        """
        Get the position mode for a specific symbol
        :param symbol: the symbol to get position mode for
        :return: the position mode (default to ONE_WAY)
        """
        if symbol is None:
            self.logger.warning("get_position_mode called with None symbol, returning ONE_WAY")
            return trading_enums.PositionMode.ONE_WAY
            
        try:
            position = await self.get_position(symbol)
            if position and position.get(trading_enums.ExchangeConstantsPositionColumns.POSITION_MODE.value):
                return position[trading_enums.ExchangeConstantsPositionColumns.POSITION_MODE.value]
        except Exception as e:
            self.logger.debug(f"Could not get position mode from position for {symbol}: {e}")
        
        # Return default position mode
        return trading_enums.PositionMode.ONE_WAY
    
    async def get_maintenance_margin_rate(self, symbol: str):
        """
        Get the maintenance margin rate for a specific symbol
        :param symbol: the symbol to get maintenance margin rate for
        :return: the maintenance margin rate (default to 0.005)
        """
        if symbol is None:
            self.logger.warning("get_maintenance_margin_rate called with None symbol, returning default rate")
            return 0.005
            
        try:
            position = await self.get_position(symbol)
            if position and position.get(trading_enums.ExchangeConstantsPositionColumns.MAINTENANCE_MARGIN_RATE.value):
                return float(position[trading_enums.ExchangeConstantsPositionColumns.MAINTENANCE_MARGIN_RATE.value])
        except Exception as e:
            self.logger.debug(f"Could not get maintenance margin rate from position for {symbol}: {e}")
        
        # Return default maintenance margin rate
        return 0.005
    
    def get_default_balance(self):
        """
        Override to provide default balance for Hyperliquid using USDC instead of USDT
        :return: default balance dict with USDC
        """
        return {
            "USDC": {
                "total": 10000,
                "free": 10000,
                "locked": 0,
            }
        }
    
    def get_market_status(self, symbol, **kwargs):
        """
        Override to handle Hyperliquid's symbol mapping for ValueConverter compatibility
        """
        # Try original symbol first
        try:
            return super().get_market_status(symbol, **kwargs)
        except Exception:
            # If that fails, try mapping common symbols to Hyperliquid format
            mapped_symbol = self._map_symbol_to_hyperliquid_format(symbol)
            if mapped_symbol != symbol:
                try:
                    return super().get_market_status(mapped_symbol, **kwargs)
                except Exception:
                    pass
            raise

    def get_exchange_current_time(self):
        """
        Override to provide current exchange time
        """
        return self.connector.client.seconds() if hasattr(self.connector, 'client') and self.connector.client else super().get_exchange_current_time()
    
    async def set_symbol_margin_type(self, symbol: str, isolated: bool, **kwargs: dict):
        """
        Set the symbol margin type with None symbol validation and proper leverage parameter
        :param symbol: the symbol
        :param isolated: when False, margin type is cross, else it's isolated
        :return: the update result
        """
        if symbol is None:
            self.logger.warning("set_symbol_margin_type called with None symbol, skipping operation")
            return None
            
        try:
            # Get current leverage for the symbol or use default
            current_leverage = await self.get_symbol_leverage(symbol)
            if not current_leverage or current_leverage < 1:
                current_leverage = 1.0
            
            # Add leverage to kwargs if not provided (Hyperliquid requirement)
            if 'leverage' not in kwargs:
                kwargs['leverage'] = current_leverage
                
            return await super().set_symbol_margin_type(symbol, isolated, **kwargs)
        except NotImplementedError:
            self.logger.debug(f"set_symbol_margin_type not implemented for {symbol}, using default behavior")
            return None
        except Exception as e:
            self.logger.warning(f"Failed to set margin type for {symbol}: {e}")
            return None
    
    def is_linear_symbol(self, symbol):
        """
        Check if symbol is a linear futures symbol (uses USDC as quote/settlement)
        """
        try:
            parsed = octobot_commons.symbols.parse_symbol(symbol)
            return parsed.is_future() and parsed.quote in ("USDC", "USD")
        except Exception:
            return False
    
    def is_inverse_symbol(self, symbol):
        """
        Check if symbol is an inverse futures symbol (uses base currency as settlement)
        """
        try:
            parsed = octobot_commons.symbols.parse_symbol(symbol)
            return parsed.is_future() and parsed.settlement_asset == parsed.base
        except Exception:
            return False

    async def get_position(self, symbol: str, **kwargs: dict) -> dict:
        """
        Get position for a specific symbol with None symbol validation
        """
        if symbol is None:
            self.logger.warning("get_position called with None symbol, returning empty position")
            return {}
            
        try:
            return await super().get_position(symbol, **kwargs)
        except Exception as e:
            self.logger.debug(f"Could not get position for {symbol}: {e}")
            return {}


class HyperLiquidCCXTAdapter(exchanges.CCXTAdapter):

    def fix_ticker(self, raw, **kwargs):
        fixed = super().fix_ticker(raw, **kwargs)
        fixed[trading_enums.ExchangeConstantsTickersColumns.TIMESTAMP.value] = (
            fixed.get(trading_enums.ExchangeConstantsTickersColumns.TIMESTAMP.value)
            or self.connector.client.seconds()
        )
        return fixed

    def fix_market_status(self, raw, remove_price_limits=False, **kwargs):
        fixed = super().fix_market_status(
            raw, remove_price_limits=remove_price_limits, **kwargs
        )
        if not fixed:
            return fixed
        # hyperliquid min cost should be increased by 10% (a few cents above min cost is refused)
        limits = fixed[trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS.value]
        limits[trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS_COST.value][
            trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS_COST_MIN.value
        ] = (
            limits[
                trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS_COST.value
            ][trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS_COST_MIN.value]
            * 1.1
        )

        return fixed

    def parse_position(self, fixed, **kwargs):
        """
        Parse position with enhanced None symbol validation
        """
        try:
            # First check if fixed position data is valid
            if not fixed or not isinstance(fixed, dict):
                return {}
                
            # Extract symbol and validate
            symbol_key = trading_enums.ExchangeConstantsPositionColumns.SYMBOL.value
            symbol = fixed.get(symbol_key)
            
            if symbol is None:
                # Log warning for None symbol but don't crash
                self.logger.warning(f"parse_position received None symbol in position data: {fixed}")
                return {}
                
            # Call parent parse_position with validated data
            result = super().parse_position(fixed, **kwargs)
            
            # Ensure required fields are present
            if result and isinstance(result, dict):
                # Set default values for missing fields
                if symbol_key not in result:
                    result[symbol_key] = symbol
                    
            return result
            
        except Exception as e:
            self.logger.warning(f"Failed to parse position data: {e}. Data: {fixed}")
            return {}
