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

    async def get_position(self, symbol, **kwargs):
        # Ensure 'user' is in params or wallet_address is set
        params = kwargs.get('params', {})
        
        # Get wallet address from connector credentials (API key is used as wallet address)
        wallet_address = None
        if hasattr(self.connector, 'credentials') and self.connector.credentials:
            # The wallet_address was set in the _keys_adapter method
            wallet_address = self.connector.credentials.wallet_address
        
        if 'user' not in params and wallet_address:
            params['user'] = wallet_address
            kwargs['params'] = params
        elif 'user' not in params and not wallet_address:
            # Log warning and return empty position dict instead of None to prevent crashes
            self.logger.warning(f"Hyperliquid fetchPositions() requires a user parameter inside 'params' or the wallet address set. Skipping position fetch for {symbol}")
            return {}
        
        return await super().get_position(symbol, **kwargs)

    @classmethod
    def get_name(cls):
        return "hyperliquid"

    def get_adapter_class(self):
        return HyperLiquidCCXTAdapter

    async def get_symbol_markets(self, symbols, reload=False, **kwargs):
        """Override to ensure both spot and futures markets are loaded"""
        return await super().get_symbol_markets(symbols, reload=reload, **kwargs)

    def _get_supported_symbols(self, symbols_list):
        """Override to check for symbol transformation matches"""
        supported_symbols = []
        
        for symbol in symbols_list:
            # Check if the symbol exists as-is
            if symbol in self.symbols:
                supported_symbols.append(symbol)
        
        return supported_symbols

    async def get_positions(self, symbols=None, **kwargs):
        """Override to ensure proper position fetching for futures"""
        # Only futures have positions, spot trading doesn't
        if not self.exchange_manager.is_future:
            return []
        
        # Ensure 'user' is in params for Hyperliquid
        params = kwargs.get('params', {})
        
        # Get wallet address from connector credentials (API key is used as wallet address)
        if 'user' not in params and hasattr(self.connector, 'credentials') and self.connector.credentials:
            wallet_address = self.connector.credentials.wallet_address
            if wallet_address:
                params['user'] = wallet_address
                kwargs['params'] = params
            else:
                self.logger.warning("Hyperliquid fetchPositions() requires a user parameter but wallet address not found")
                return []
        elif 'user' not in params:
            self.logger.warning("Hyperliquid fetchPositions() requires a user parameter but no wallet address available")
            return []
        
        try:
            positions = await super().get_positions(symbols, **kwargs)
            return positions if positions is not None else []
        except Exception as e:
            self.logger.warning(f"Error fetching positions from Hyperliquid: {e}")
            return []


class HyperLiquidCCXTAdapter(exchanges.CCXTAdapter):

    def fix_order(self, raw, **kwargs):
        """Fix order data"""
        fixed = super().fix_order(raw, **kwargs)
        return fixed

    def fix_trades(self, raw, **kwargs):
        """Fix trades data"""
        fixed = super().fix_trades(raw, **kwargs)
        return fixed

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
