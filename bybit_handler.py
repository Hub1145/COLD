import time
import logging
import random
import pandas as pd
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from pybit.unified_trading import HTTP, WebSocket
import pybit.exceptions
import asyncio

from config import Config
from indicators import TechnicalIndicators

logger = logging.getLogger(__name__)

class BybitFuturesClient:
    def __init__(self, api_key, api_secret, testnet=False, config: Optional[Config] = None, get_active_symbols_callback=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.config = config
        self.ws_url = "wss://stream.bybit.com/v5/public/linear" if not testnet else "wss://stream-testnet.bybit.com/v5/public/linear"
        self.session = self._create_session()
        self.ws: Optional[WebSocket] = None
        self.ws_connected = False
        self.instrument_info_cache = {}
        self.kline_cache: Dict[str, pd.DataFrame] = {}
        self.cache_timestamps: Dict[str, datetime] = {}
        self.current_forming_klines: Dict[str, pd.DataFrame] = {}
        self.logger = logging.getLogger(__name__)
        self.indicators_calculator = TechnicalIndicators(self.logger)
        self.get_active_symbols_callback = get_active_symbols_callback
        self.last_ws_message_time: Optional[datetime] = None

    def get_interval_map(self):
        """Returns the interval mapping."""
        return {
            '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
            '1d': 'D', '1w': 'W', '1M': 'M'
        }

    def _create_session(self):
        """Creates a new pybit session for futures trading."""
        recv_window = 10000

        return HTTP(
            testnet=self.testnet,
            api_key=self.api_key,
            api_secret=self.api_secret,
            recv_window=recv_window
        )

    def _create_ws_session(self):
        """Creates a new pybit WebSocket session."""
        return WebSocket(
            testnet=self.testnet,
            channel_type="linear"
        )

    def _ws_message_handler(self, message):
        """Handles incoming WebSocket messages."""
        self.last_ws_message_time = datetime.now(timezone.utc)
        self.logger.debug(f"WS: Raw message received: {message}")
        try:
            if 'topic' in message and message['topic'].startswith('kline.'):
                topic = message['topic']
                symbol = topic.split('.')[-1]

                if symbol in self.kline_cache:
                    data = message.get('data', [])
                    for kline in data:
                        kline_df = pd.DataFrame([{
                            "Timestamp": int(kline['start']),
                            "Open": float(kline['open']),
                            "High": float(kline['high']),
                            "Low": float(kline['low']),
                            "Close": float(kline['close']),
                            "Volume": float(kline['volume']),
                            "Turnover": float(kline['turnover']),
                        }])
                        kline_df['Datetime'] = pd.to_datetime(kline_df['Timestamp'], unit='ms', utc=True)
                        kline_df = kline_df.set_index('Datetime')

                        cached_df = self.kline_cache[symbol]

                        # Check if the timestamp exists in the cache
                        if not kline_df.index.isin(cached_df.index).any():
                            # New candle, append and drop the oldest
                            self.kline_cache[symbol] = pd.concat([cached_df, kline_df]).iloc[1:]
                        else:
                            # Update existing candle
                            cached_df.update(kline_df)

                        self.cache_timestamps[symbol] = datetime.now(timezone.utc)
                        self.logger.debug(f"WS: Updated kline cache for {symbol}.")
            elif 'op' in message and message['op'] == 'subscribe':
                if message.get('success'):
                    self.logger.info(f"WS: Successfully subscribed to {message.get('args')}")
                else:
                    self.logger.error(f"WS: Failed to subscribe to {message.get('args')}: {message.get('ret_msg')}")
            else:
                self.logger.debug(f"WS: Unhandled message: {message}")
        except Exception as e:
            self.logger.error(f"Error in WebSocket message handler: {e}", exc_info=True)

    async def _run_websocket_watchdog(self):
        """Monitors the WebSocket connection and reconnects if necessary."""
        while self.ws_connected:
            if self.last_ws_message_time:
                time_since_last_message = (datetime.now(timezone.utc) - self.last_ws_message_time).total_seconds()
                if time_since_last_message > self.config.websocket.websocket_watchdog_seconds:
                    self.logger.warning(f"No WebSocket message received for {time_since_last_message} seconds. Reconnecting.")
                    await self._reconnect_websocket()
            await asyncio.sleep(self.config.websocket.heartbeat_seconds)

    async def _reconnect_websocket(self):
        """Handles the WebSocket reconnection logic."""
        self.ws_connected = False
        if self.ws:
            self.ws.exit()

        self.logger.info("Attempting to reconnect to WebSocket...")
        await self._ensure_ws_connected()

        # Resubscribe to all previously subscribed topics
        if self.get_active_symbols_callback:
            active_symbols = self.get_active_symbols_callback()
            self.logger.info(f"Resubscribing to kline data for active symbols: {active_symbols}")
            for symbol in active_symbols:
                await self.subscribe_to_kline(symbol, self.config.trading.timeframe)

    async def subscribe_to_kline(self, symbol: str, interval: str):
        """Subscribes to kline data for a single symbol."""
        # Map interval from '1m', '5m' to '1', '5'
        interval_map = {
            '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
            '1d': 'D', '1w': 'W', '1M': 'M'
        }
        bybit_interval = interval_map.get(interval, interval)
        topic = f"kline.{bybit_interval}.{symbol}"

        if self.ws is None:
            await self._ensure_ws_connected()

        if topic not in self.ws.subscriptions:
            try:
                self.ws.kline_stream(interval=bybit_interval, symbol=symbol, callback=self._ws_message_handler)
                self.logger.info(f"Subscribed to WebSocket topic: {topic} for {symbol}")
            except Exception as e:
                self.logger.error(f"Error subscribing to WebSocket topic {topic}: {e}", exc_info=True)
        else:
            self.logger.debug(f"Already subscribed to {topic}.")

    async def _ensure_ws_connected(self):
        """Ensures the WebSocket connection is active."""
        if not self.ws_connected or self.ws is None or not self.ws.is_connected():
            self.logger.info("Connecting to Bybit WebSocket...")
            self.ws = self._create_ws_session()
            self.ws_connected = True
            self.logger.info("Bybit WebSocket initialized. Connection will be established on first subscription.")

    async def unsubscribe_from_kline(self, symbol: str, interval: str):
        """Unsubscribes from kline data for a single symbol."""
        if self.ws_connected and self.ws:
            interval_map = {
                '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
                '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
                '1d': 'D', '1w': 'W', '1M': 'M'
            }
            bybit_interval = interval_map.get(interval, interval)
            topic = f"kline.{bybit_interval}.{symbol}"
            if topic in self.ws.subscriptions:
                unsubscribe_message = {
                    "op": "unsubscribe",
                    "args": [topic]
                }
                if hasattr(self.ws, 'ws') and self.ws.ws:
                    self.ws.ws.send(json.dumps(unsubscribe_message))
                else:
                    self.logger.warning(f"WebSocket connection not active for {symbol}, cannot send unsubscribe message.")

                self.ws.subscriptions.pop(topic, None)
                self.logger.info(f"Unsubscribed from WebSocket topic: {topic} for {symbol}")
            else:
                self.logger.debug(f"Not subscribed to {topic}, no need to unsubscribe.")
        else:
            self.logger.warning("WebSocket not connected, cannot unsubscribe.")

    async def stop_all_ws_listeners(self):
        """Stops all WebSocket listeners."""
        if self.ws_connected and self.ws:
            self.logger.info("Stopping all Bybit WebSocket listeners...")
            self.ws.exit()
            self.ws_connected = False
            self.logger.info("All Bybit WebSocket listeners stopped.")
        else:
            self.logger.info("WebSocket not connected, nothing to stop.")

    async def get_account_balance(self, asset="USDT", max_retries=3):
        """Retrieves the USDT account balance from the UNIFIED account with retry logic."""
        for attempt in range(max_retries):
            try:
                session = self._create_session()
                account_type = "UNIFIED"

                wallet_balance = session.get_wallet_balance(accountType=account_type, coin=asset)
                self.logger.debug(f"Response from get_wallet_balance: {wallet_balance}")

                if wallet_balance and wallet_balance.get("retCode") == 0:
                    result_list = wallet_balance.get("result", {}).get("list", [])
                    if not result_list:
                        self.logger.warning(f"No wallet data found for {asset} in UNIFIED account.")
                        return 0.0

                    account_info = result_list[0]
                    coins_list = account_info.get("coin", [])

                    for asset_info in coins_list:
                        if asset_info.get("coin") == asset:
                            balance_str = asset_info.get("walletBalance")
                            if balance_str:
                                balance = float(balance_str)
                                self.logger.info(f"Balance for {asset} found: {balance}")
                                return balance
                            else:
                                self.logger.warning(f"'walletBalance' field for {asset} not found.")
                                return 0.0

                    self.logger.warning(f"{asset} not found in the coin list for the UNIFIED account.")
                    return 0.0
                else:
                    ret_code = wallet_balance.get('retCode', 'N/A')
                    ret_msg = wallet_balance.get('retMsg', 'Unknown error')
                    self.logger.error(
                        f"Attempt {attempt + 1}: API error getting balance: {ret_msg} (Code: {ret_code})"
                    )

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1} failed getting balance: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying get_account_balance in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                 self.logger.error("Max retries reached for get_account_balance. Returning None.")
                 return None

        return None

    async def get_symbol_price(self, symbol, max_retries=3):
        """Retrieves the current market price for a futures symbol from the kline cache."""
        if symbol in self.kline_cache:
            # Get the last close price from the cached kline data
            last_close = self.kline_cache[symbol]['Close'].iloc[-1]
            self.logger.debug(f"Using cached kline price for {symbol}: {last_close}")
            return last_close
        else:
            self.logger.warning(f"No kline cache found for {symbol}. Cannot get price.")
            return None

    async def get_instruments_info(self, symbol, max_retries=3):
        """Retrieves instrument information for a futures symbol."""
        category = "linear"
        cache_key = f"{symbol}_{category}"
        if cache_key in self.instrument_info_cache:
            return self.instrument_info_cache[cache_key]

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                response = session.get_instruments_info(
                    category=category,
                    symbol=symbol
                )
                self.logger.debug(f"Response from get_instruments_info for {symbol}: {response}")

                if response and response.get("retCode") == 0:
                    result_list = response.get("result", {}).get("list", [])
                    if result_list:
                        instrument_info = result_list[0]
                        self.instrument_info_cache[cache_key] = instrument_info
                        self.logger.info(f"Instrument information for {symbol} obtained and cached")
                        return instrument_info
                    else:
                        self.logger.error(f"Empty results list when getting instrument information for {symbol}.")
                        return None
                else:
                    ret_code = response.get('retCode', 'N/A')
                    ret_msg = response.get('retMsg', 'Unknown error')
                    self.logger.error(f"Attempt {attempt + 1}: API error getting instrument information for {symbol}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1} failed getting instrument information for {symbol}: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying get_instruments_info in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error(f"Max retries reached for get_instruments_info({symbol}). Returning None.")
                return None
        return None

    async def get_qty_precision(self, symbol):
        """Retrieves the quantity precision (number of decimals) for a futures symbol."""
        try:
            instrument = await self.get_instruments_info(symbol)
            if instrument and "lotSizeFilter" in instrument:
                qty_step_str = instrument["lotSizeFilter"].get("qtyStep")
                if qty_step_str:
                    if '.' in qty_step_str:
                        precision = len(qty_step_str.split('.')[-1])
                        self.logger.debug(f"Quantity precision for {symbol} from API: {precision} (qtyStep: {qty_step_str})")
                        return precision
                    else:
                        self.logger.debug(f"Quantity precision for {symbol} from API: 0 (qtyStep: {qty_step_str})")
                        return 0
                else:
                    self.logger.warning(f"'qtyStep' not found in lotSizeFilter for {symbol}. Using default value.")
            else:
                self.logger.warning(f"Could not get instrument information or lotSizeFilter for {symbol}. Using default precision.")

            if "BTC" in symbol:
                return 3
            elif "ETH" in symbol:
                return 3
            else:
                return 2
        except Exception as e:
            self.logger.error(f"Error getting quantity precision for {symbol}: {e}. Using default value.")
            if "BTC" in symbol:
                return 3
            else:
                return 2
    async def get_min_order_qty(self, symbol):
        """Retrieves the minimum order quantity for a futures symbol."""
        try:
            instrument = await self.get_instruments_info(symbol)
            if instrument and "lotSizeFilter" in instrument:
                min_qty_str = instrument["lotSizeFilter"].get("minOrderQty")
                if min_qty_str:
                    min_qty = float(min_qty_str)
                    self.logger.debug(f"Minimum order quantity for {symbol} from API: {min_qty}")
                    return min_qty
                else:
                     self.logger.warning(f"'minOrderQty' not found in lotSizeFilter for {symbol}. Using default value.")
            else:
                self.logger.warning(f"Could not get instrument information or lotSizeFilter for {symbol}. Using default minimum quantity.")

            if "BTC" in symbol:
                return 0.001
            elif "ETH" in symbol:
                 return 0.01
            else:
                return 1.0
        except Exception as e:
            self.logger.error(f"Error getting minimum order quantity for {symbol}: {e}. Using default value.")
            if "BTC" in symbol:
                return 0.001
            else:
                return 0.01

    async def format_quantity(self, symbol, quantity):
        """Formats the quantity according to the symbol's precision rules."""
        precision = await self.get_qty_precision(symbol)
        min_qty = await self.get_min_order_qty(symbol)

        if quantity < min_qty:
            self.logger.warning(f"Calculated quantity {quantity} is less than minimum {min_qty}. Adjusting to minimum.")
            quantity = min_qty

        # Ensure quantity meets minOrderQty and is a multiple of qtyStep
        instrument_info = await self.get_instruments_info(symbol)
        if not instrument_info or "lotSizeFilter" not in instrument_info:
            self.logger.error(f"Missing instrument metadata for {symbol}. Cannot format quantity.")
            if self.config.risk_management.fail_fast_on_missing_instrument_metadata:
                raise ValueError(f"Missing instrument metadata for {symbol}.")
            # Fallback to simple rounding if metadata is critical and missing, but log a warning
            return str(round(quantity, precision))

        qty_step = float(instrument_info["lotSizeFilter"].get("qtyStep", "0.001"))

        # Snap quantity to the nearest qtyStep
        quantity = round(round(quantity / qty_step) * qty_step, precision)

        try:
            format_string = "{:." + str(precision) + "f}"
            formatted_qty_str = format_string.format(quantity)
            self.logger.debug(f"Formatting quantity: Original={quantity}, MinQty={min_qty}, QtyStep={qty_step}, Precision={precision}, Formatted={formatted_qty_str}")
            return formatted_qty_str
        except Exception as e:
            self.logger.error(f"Error formatting quantity {quantity} for {symbol} with precision {precision}: {e}")
            return str(round(quantity, precision))

    async def place_order(self, symbol, side, qty_str: Optional[str] = None, order_type="MARKET", trigger_price=None, reduce_only=False, position_idx=0, max_retries=3):
        """Places a futures order with TP/SL and retry logic.
        Supports conditional orders (Entry Price) via trigger_price.
        """
        category = "linear"

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                params = {
                    "category": category,
                    "symbol": symbol,
                    "side": side,
                    "orderType": order_type,
                    "reduceOnly": reduce_only,
                    "positionIdx": position_idx
                }

                if qty_str is not None:
                    params["qty"] = qty_str

                if order_type == "MARKET" and trigger_price is not None:
                    current_market_price = await self.get_symbol_price(symbol)
                    if current_market_price is None:
                        self.logger.warning(f"Could not get current market price for {symbol}. Cannot determine order type. Proceeding with conditional order.")
                        params["orderType"] = "MARKET"

                    params["triggerPrice"] = str(trigger_price)
                    params["triggerDirection"] = 1 if side == "Buy" else 2
                    params["triggerBy"] = "LastPrice"
                    self.logger.info(f"Conditional Market Order: Trigger Price {trigger_price}")
                elif order_type == "LIMIT" and trigger_price is not None:
                    params["triggerPrice"] = str(trigger_price)
                    params["triggerDirection"] = 1 if side == "Buy" else 2
                    params["triggerBy"] = "LastPrice"
                    params["price"] = str(trigger_price)
                    self.logger.info(f"Conditional Limit Order: Trigger Price {trigger_price}, Limit Price {trigger_price}")
                elif order_type == "LIMIT":
                    params["price"] = str(trigger_price) if trigger_price else str(await self.get_symbol_price(symbol))
                    self.logger.info(f"Regular Limit Order: Price {params['price']}")

                self.logger.info(f"Attempt {attempt + 1}: Placing order with parameters: {params}")

                response = session.place_order(**params)
                self.logger.debug(f"API response from place_order: {response}")

                if response and response.get("retCode") == 0:
                    order_id = response.get("result", {}).get("orderId")
                    if order_id:
                        self.logger.info(f"Order placement successful. Order ID: {order_id}")
                        return order_id
                    else:
                        self.logger.error("Order placement was successful (retCode 0) but no orderId found in response.")
                        return None
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    self.logger.error(f"Attempt {attempt + 1}: API error getting order status for {order_id}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception during order placement: {e}", exc_info=True)

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying place_order in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                 self.logger.error(f"Max retries reached for place_order({symbol}, {side}, {qty_str}). Order failed.")
                 return None
        return None

    async def set_leverage(self, symbol, leverage, max_retries=3):
        """Sets the leverage for a futures symbol."""
        category = "linear"
        leverage_str = str(leverage)

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                self.logger.info(f"Attempt {attempt + 1}: Setting leverage for {symbol} to {leverage_str}x")
                response = session.set_leverage(
                    category=category,
                    symbol=symbol,
                    buyLeverage=leverage_str,
                    sellLeverage=leverage_str
                )
                self.logger.debug(f"Response from set_leverage: {response}")

                if response and response.get("retCode") == 0:
                    self.logger.info(f"Leverage successfully set to {leverage_str}x for {symbol}")
                    return True
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    if ret_code in [110025, 110043] or "Leverage not modified" in ret_msg:
                         self.logger.info(f"Leverage for {symbol} is already set to {leverage_str}x or was not modified. Proceeding.")
                         return True
                    else:
                         self.logger.error(f"Attempt {attempt + 1}: Failed to set leverage for {symbol}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                if isinstance(e, pybit.exceptions.InvalidRequestError) and ("leverage not modified" in str(e) or "110043" in str(e)):
                    self.logger.info(f"Leverage for {symbol} is already set to {leverage_str}x. Proceeding.")
                    return True
                self.logger.error(f"Attempt {attempt + 1}: Exception setting leverage for {symbol}: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying set_leverage in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error(f"Max retries reached for set_leverage({symbol}, {leverage_str}). Failed.")
                return False
        return False

    async def set_margin_mode(self, symbol: str, margin_mode: str, leverage: float, max_retries=3):
        """Sets the margin mode (Cross or Isolated) for a futures symbol."""
        category = "linear"
        trade_mode = 0 if margin_mode.lower() == "cross" else 1
        leverage_str = str(leverage)

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                self.logger.info(f"Attempt {attempt + 1}: Switching margin mode for {symbol} to {margin_mode} with {leverage_str}x leverage")
                response = session.switch_margin_mode(
                    category=category,
                    symbol=symbol,
                    tradeMode=trade_mode,
                    buyLeverage=leverage_str,
                    sellLeverage=leverage_str
                )
                self.logger.debug(f"Response from switch_margin_mode: {response}")

                if response and response.get("retCode") == 0:
                    self.logger.info(f"Margin mode successfully set to {margin_mode} for {symbol}")
                    return True
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    if ret_code in [100028, 110043, 110073] or "not modified" in ret_msg.lower():
                        self.logger.info(f"Margin mode for {symbol} is already set to {margin_mode} or was not modified. Proceeding.")
                        return True
                    else:
                        self.logger.error(f"Attempt {attempt + 1}: Failed to set margin mode for {symbol}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception setting margin mode for {symbol}: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying set_margin_mode in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error(f"Max retries reached for set_margin_mode({symbol}, {margin_mode}). Failed.")
                return False
        return False

    async def get_order_status(self, symbol, order_id, max_retries=3):
        """Checks the status of a specific futures order by its ID."""
        category = "linear"

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                params = {
                    "category": category,
                    "symbol": symbol,
                    "orderId": order_id,
                    "limit": 1
                }
                self.logger.debug(f"Attempt {attempt+1}: Getting order history for orderId {order_id}...")
                response = session.get_order_history(**params)
                self.logger.debug(f"API response from get_order_status (history): {response}")

                if response and response.get("retCode") == 0:
                    result_list = response.get("result", {}).get("list", [])
                    if result_list:
                        order_info = result_list[0]
                        if order_info.get("orderId") == order_id:
                            status = order_info.get("orderStatus", "Unknown").lower()
                            self.logger.info(f"Order status {order_id}: {status}")
                            return status
                        else:
                            self.logger.warning(f"Order history returned order {order_info.get('orderId')} but expected {order_id}. Order likely not found.")
                            return "not_found"
                    else:
                        self.logger.warning(f"No order history found for orderId {order_id} on {symbol}. Unknown status.")
                        return "unknown"
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    self.logger.error(f"Attempt {attempt + 1}: API error getting order status for {order_id}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception getting order status for {order_id}: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying get_order_status in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                 self.logger.error(f"Max retries reached for get_order_status({order_id}). Returning 'unknown'.")
                 return "unknown"
        return "unknown"

    async def get_order_info(self, symbol, order_id, max_retries=3) -> Optional[Dict[str, Any]]:
        """
        Retrieves detailed information about a specific futures order by its ID.
        Returns the order info dictionary if found, None otherwise.
        """
        category = "linear"

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                params = {
                    "category": category,
                    "symbol": symbol,
                    "orderId": order_id,
                    "limit": 1
                }
                self.logger.debug(f"Attempt {attempt+1}: Getting order info for orderId {order_id}...")
                response = session.get_order_history(**params)
                self.logger.debug(f"API response from get_order_info: {response}")

                if response and response.get("retCode") == 0:
                    result_list = response.get("result", {}).get("list", [])
                    if result_list:
                        order_info = result_list[0]
                        if order_info.get("orderId") == order_id:
                            self.logger.info(f"Order info for {order_id} retrieved.")
                            return order_info
                        else:
                            self.logger.warning(f"Order history returned order {order_info.get('orderId')} but expected {order_id}. Order likely not found.")
                            return None
                    else:
                        self.logger.warning(f"No order history found for orderId {order_id} on {symbol}. Returning None.")
                        return None
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    self.logger.error(f"Attempt {attempt + 1}: API error getting order info for {order_id}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception getting order info for {order_id}: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying get_order_info in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error(f"Max retries reached for get_order_info({order_id}). Returning None.")
                return None
        return None

    async def _wait_for_order_fill(self, symbol: str, order_id: str, timeout: int = 120, poll_interval: int = 5) -> Optional[Dict[str, Any]]:
        """
        Polls for an order to be filled.
        Returns filled order info if successful, None otherwise.
        """
        self.logger.info(f"Waiting for order {order_id} for {symbol} to fill (timeout: {timeout}s)...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            order_info = await self.get_order_info(symbol, order_id)
            if order_info and order_info.get("orderStatus") == "Filled":
                self.logger.info(f"Order {order_id} for {symbol} is Filled.")
                return order_info
            elif order_info and order_info.get("orderStatus") == "PartiallyFilled":
                self.logger.info(f"Order {order_id} for {symbol} is PartiallyFilled. Continuing to wait for full fill.")
            elif order_info and order_info.get("orderStatus") in ["Cancelled", "Rejected"]:
                self.logger.warning(f"Order {order_id} for {symbol} was {order_info.get('orderStatus')}. Aborting wait.")
                return None

            await asyncio.sleep(poll_interval)

        self.logger.warning(f"Order {order_id} for {symbol} timed out after {timeout} seconds without filling.")
        return None

    async def get_position(self, symbol, max_retries=3):
        """Retrieves current position information for a futures symbol."""
        category = "linear"

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                params = {"category": category, "symbol": symbol}
                response = session.get_positions(**params)
                self.logger.debug(f"API response from get_position for {symbol}: {response}")

                if response and response.get("retCode") == 0:
                    position_list = response.get("result", {}).get("list", [])
                    if position_list:
                        position_info = position_list[0]
                        position_size_str = position_info.get("size", "0")
                        position_side = position_info.get("side", "None")

                        if position_size_str and float(position_size_str) > 0:
                            self.logger.info(f"Active position found for {symbol}: Side={position_side}, Size={position_size_str}")
                            return position_info
                        else:
                            self.logger.info(f"No active position found for {symbol} (Size is {position_size_str}).")
                            return None
                    else:
                        self.logger.info(f"Empty position list returned for {symbol}. Assuming no position.")
                        return None
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    self.logger.error(f"Attempt {attempt + 1}: API error getting position for {symbol}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception getting position for {symbol}: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying get_position in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error(f"Max retries reached for get_position({symbol}). Returning None.")
                return None
        return None

    async def get_kline(self, symbol, interval, limit=200, start_time=None, end_time=None, max_retries=3):
        """
        Retrieves kline/candlestick data for a futures symbol.
        Interval examples: '1', '3', '5', '15', '30', '60', '120', '240', '360', '720', 'D', 'W', 'M'
        """
        category = "linear"

        interval_map = {
            '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
            '1d': 'D', '1w': 'W', '1M': 'M'
        }
        bybit_interval = interval_map.get(interval, interval)

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                params = {
                    "category": category,
                    "symbol": symbol,
                    "interval": bybit_interval,
                    "limit": limit
                }
                if start_time:
                    params["start"] = int(start_time * 1000)
                if end_time:
                    params["end"] = int(end_time * 1000)

                self.logger.debug(f"Attempt {attempt + 1}: Getting kline data with parameters: {params}")
                response = session.get_kline(**params)

                if response and response.get("retCode") == 0:
                    kline_list = response.get("result", {}).get("list", [])
                    if kline_list:
                        kline_list.reverse()
                        df = pd.DataFrame(kline_list, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume", "Turnover"])
                        df['Timestamp'] = pd.to_numeric(df['Timestamp'])
                        df['Open'] = pd.to_numeric(df['Open'])
                        df['High'] = pd.to_numeric(df['High'])
                        df['Low'] = pd.to_numeric(df['Low'])
                        df['Close'] = pd.to_numeric(df['Close'])
                        df['Volume'] = pd.to_numeric(df['Volume'])
                        df['Datetime'] = pd.to_datetime(df['Timestamp'], unit='ms', utc=True)
                        df = df.set_index('Datetime')

                        self.logger.info(f"Successfully retrieved {len(df)} klines for {symbol} interval {bybit_interval}")
                        return df
                    else:
                        # Return an empty DataFrame without a warning for symbols that may not have kline data yet
                        return pd.DataFrame()
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    self.logger.error(f"Attempt {attempt + 1}: API error getting klines for {symbol}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception getting klines for {symbol}: {e}", exc_info=True)

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying get_kline in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error(f"Max retries reached for get_kline({symbol}, {bybit_interval}). Returning None.")
                return None
        return None

    async def _prune_expired_cache(self):
        """Removes expired entries from the kline cache."""
        now = datetime.now(timezone.utc)
        expiration_days = self.config.risk_management.signal_expiration_days
        expired_symbols = [
            symbol for symbol, timestamp in self.cache_timestamps.items()
            if (now - timestamp).days > expiration_days
        ]
        for symbol in expired_symbols:
            del self.kline_cache[symbol]
            del self.cache_timestamps[symbol]
            self.logger.info(f"Pruned expired kline cache for {symbol}.")

    async def get_and_calculate_indicators(self, symbol: str, interval: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """
        Fetches kline data and calculates all necessary technical indicators.
        """
        await self._prune_expired_cache()
        # Check if kline data is in cache
        if symbol not in self.kline_cache:
            df = await self.get_kline(symbol, interval, limit)
            if df is None or df.empty:
                self.logger.warning(f"Could not retrieve kline data for {symbol} with interval {interval}. Cannot calculate indicators.")
                return None
            self.kline_cache[symbol] = df
            self.cache_timestamps[symbol] = datetime.now(timezone.utc)
            await self.subscribe_to_kline(symbol, interval)
        else:
            df = self.kline_cache[symbol]

        # Calculate Indicators
        # EMA 50
        df[f'EMA_{self.config.indicators.ema_50}'] = self.indicators_calculator.calculate_ema(df, self.config.indicators.ema_50)
        # EMA 200
        df[f'EMA_{self.config.indicators.ema_200}'] = self.indicators_calculator.calculate_ema(df, self.config.indicators.ema_200)
        # RSI
        df['RSI'] = self.indicators_calculator.calculate_rsi(df, self.config.indicators.rsi_length)
        # MACD
        macd_data = self.indicators_calculator.calculate_macd(df, self.config.indicators.macd_fast, self.config.indicators.macd_slow, self.config.indicators.macd_signal)
        df = df.join(macd_data)
        # ATR
        df['ATR'] = self.indicators_calculator.calculate_atr(df, self.config.indicators.atr_length)
        # Bollinger Bands
        bb_data = self.indicators_calculator.calculate_bollinger_bands(df, self.config.indicators.bollinger_bands_length, self.config.indicators.bollinger_bands_std)
        df = df.join(bb_data)

        self.logger.info(f"Calculated indicators for {symbol} on {interval} timeframe.")
        return df

    async def get_server_time(self, max_retries=3):
        """Retrieves Bybit server time (UTC) as a Unix timestamp (seconds)."""
        for attempt in range(max_retries):
            try:
                session = self._create_session()
                response = session.get_server_time()
                self.logger.debug(f"Response from get_server_time: {response}")

                if response and response.get("retCode") == 0:
                    server_time_ms_str = response.get("result", {}).get("timeNano")
                    if not server_time_ms_str:
                          server_time_ms_str = response.get("result", {}).get("timeMilli")

                    if server_time_ms_str:
                        server_time_sec = int(server_time_ms_str) / 1_000_000_000 if "timeNano" in response.get("result", {}) else int(server_time_ms_str) / 1000
                        self.logger.info(f"Bybit Server Time: {datetime.fromtimestamp(server_time_sec, tz=timezone.utc)}")
                        return server_time_sec
                    else:
                          self.logger.error("Could not extract time from server time response.")
                          return None
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    self.logger.error(f"Attempt {attempt + 1}: API error getting server time: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception getting server time: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying get_server_time in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error("Max retries reached for get_server_time. Returning None.")
                return None
        return None

    async def get_max_leverage(self, symbol, max_retries=3):
        """Retrieves the maximum leverage for a futures symbol."""
        try:
            instrument = await self.get_instruments_info(symbol)
            if instrument and "leverageFilter" in instrument:
                max_leverage_str = instrument["leverageFilter"].get("maxLeverage")
                if max_leverage_str:
                    max_leverage = float(max_leverage_str)
                    self.logger.debug(f"Max leverage for {symbol} from API: {max_leverage}")
                    return max_leverage
                else:
                    self.logger.warning(f"'maxLeverage' not found in leverageFilter for {symbol}. Using 25 as default.")
            else:
                self.logger.warning(f"Could not get instrument information or leverageFilter for {symbol}. Using 25 as default.")
            return 25.0
        except Exception as e:
            self.logger.error(f"Error getting max leverage for {symbol}: {e}. Using 25 as default.")
            return 25.0

    async def set_position_mode(self, symbol, mode="MergedSingle", max_retries=3):
        """Sets the position mode (MergedSingle or BothSides) for a symbol."""
        # MergedSingle: one-way mode (long and short positions are merged)
        # BothSides: hedge mode (long and short positions are separate)
        for attempt in range(max_retries):
            try:
                session = self._create_session()
                self.logger.info(f"Attempt {attempt + 1}: Setting position mode for {symbol} to {mode}")
                response = session.set_position_mode(
                    category="linear",
                    symbol=symbol,
                    mode=mode
                )
                self.logger.debug(f"Response from set_position_mode: {response}")

                if response and response.get("retCode") == 0:
                    self.logger.info(f"Position mode successfully set to {mode} for {symbol}")
                    return True
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    self.logger.error(f"Attempt {attempt + 1}: Failed to set position mode for {symbol}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception setting position mode for {symbol}: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying set_position_mode in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error(f"Max retries reached for set_position_mode({symbol}, {mode}). Failed.")
                return False
        return False

    async def set_trading_stop(self, symbol: str, tp_price: Optional[float] = None, sl_price: Optional[float] = None, max_retries=3):
        """
        Sets or amends the Take Profit and/or Stop Loss for an existing position.
        This function assumes an existing open position for the symbol.
        """
        category = "linear"

        for attempt in range(max_retries):
            try:
                session = self._create_session()
                params = {
                    "category": category,
                    "symbol": symbol,
                    "tpslMode": "full"
                }
                if tp_price is not None:
                    params["takeProfit"] = str(tp_price)
                if sl_price is not None:
                    params["stopLoss"] = str(sl_price)

                if not tp_price and not sl_price:
                    self.logger.warning(f"No TP or SL price provided for {symbol}. No changes made.")
                    return True

                self.logger.info(f"Attempt {attempt + 1}: Setting trading stop for {symbol} with TP: {tp_price}, SL: {sl_price}")
                response = session.set_trading_stop(**params)
                self.logger.debug(f"Response from set_trading_stop: {response}")

                if response and response.get("retCode") == 0:
                    self.logger.info(f"Trading stop successfully set/amended for {symbol}. TP: {tp_price}, SL: {sl_price}")
                    return True
                else:
                    ret_code = response.get("retCode", "N/A")
                    ret_msg = response.get("retMsg", "Unknown error")
                    if "no new tpsl to set" in ret_msg.lower() or ret_code == 110043:
                        self.logger.info(f"TP/SL for {symbol} already set to specified values or no new tpsl to set. Proceeding.")
                        return True
                    else:
                        self.logger.error(f"Attempt {attempt + 1}: Failed to set trading stop for {symbol}: {ret_msg} (Code: {ret_code})")

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Exception setting trading stop for {symbol}: {e}")

            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                self.logger.info(f"Retrying set_trading_stop in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            else:
                self.logger.error(f"Max retries reached for set_trading_stop({symbol}). Failed.")
                return False
        return False

    async def verify_stop_loss(self, symbol: str, expected_sl_price: float, max_retries: int = 5, retry_delay: int = 5) -> bool:
        """
        Verifies that the exchange-side stop loss for a position is correctly set.
        """
        self.logger.info(f"Verifying Stop Loss for {symbol}. Expected: {expected_sl_price}")
        for attempt in range(max_retries):
            try:
                position_info = await self.get_position(symbol)
                if position_info and 'stopLoss' in position_info:
                    actual_sl_price = float(position_info['stopLoss'])
                    if abs(actual_sl_price - expected_sl_price) < 1e-8:
                        self.logger.info(f"Stop Loss for {symbol} verified. Actual: {actual_sl_price}, Expected: {expected_sl_price}")
                        return True
                    else:
                        self.logger.warning(f"Stop Loss for {symbol} mismatch. Actual: {actual_sl_price}, Expected: {expected_sl_price}. Retrying...")
                else:
                    self.logger.warning(f"Position or stopLoss not found for {symbol}. Retrying verification...")
            except Exception as e:
                self.logger.error(f"Error during SL verification for {symbol} (attempt {attempt + 1}): {e}")

            await asyncio.sleep(retry_delay)

        self.logger.critical(f"CRITICAL: Stop Loss verification failed for {symbol} after {max_retries} attempts. Expected SL: {expected_sl_price}. Manual check required!")
        return False

    async def get_usdt_balance(self) -> float:
        """Get the current USDT balance of the wallet."""
        balance = await self.client.get_account_balance(asset="USDT")
        if balance is None:
            self.logger.error("Failed to retrieve USDT balance.")
            return 0.0
        return balance

class BybitHandler:
    """Handles Bybit futures trading operations."""

    def __init__(self, config: Config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.client: Optional[BybitFuturesClient] = None
        self.is_connected = False
        self.tracked_positions: Dict[str, Dict[str, Any]] = {}

    async def connect(self, get_active_symbols_callback=None) -> bool:
        """Connect to Bybit and initialize client."""
        try:
            self.logger.info("Connecting to Bybit...")
            self.client = BybitFuturesClient(
                api_key=self.config.bybit.api_key,
                api_secret=self.config.bybit.api_secret,
                testnet=self.config.bybit.testnet,
                config=self.config,
                get_active_symbols_callback=get_active_symbols_callback
            )
            await self.client._ensure_ws_connected()

            asyncio.create_task(self.client._run_websocket_watchdog())
            self.logger.info("WebSocket watchdog started.")

            server_time = await self.client.get_server_time()
            if server_time:
                local_time = datetime.now(timezone.utc).timestamp()
                time_diff = abs(server_time - local_time)
                if time_diff > 1:
                    self.logger.warning(f"Time difference between local and Bybit server is {time_diff:.2f} seconds. This might cause API issues.")
            else:
                self.logger.warning("Could not retrieve Bybit server time. Time synchronization might be an issue.")

            balance = await self.client.get_account_balance()
            if balance is None:
                self.logger.error("Failed to connect to Bybit: Could not retrieve account balance.")
                await self.client.stop_all_ws_listeners()
                return False

            self.logger.info(f"Successfully connected to Bybit. Account balance: {balance} USDT")
            self.is_connected = True

            await self._recover_open_positions()

            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Bybit: {e}")
            return False

    async def place_trade(self, symbol: str, side: str, qty_str: str, entry_price: float, stop_loss: float, all_take_profits: Optional[list[float]] = None, atr: Optional[float] = None, is_to_the_moon: bool = False) -> Optional[str]:
        """
        Places a conditional trade on Bybit futures, waits for it to fill, and then sets an exchange-side stop loss.
        symbol: e.g., "ETHUSDT"
        side: "Buy" for long, "Sell" for short
        qty_str: Formatted quantity string (e.g., "0.01")
        entry_price: The price at which the order should trigger.
        stop_loss: The stop loss price (to be set exchange-side after entry).
        all_take_profits: List of all take profit levels (for tracking).
        atr: The Average True Range value for trailing stop calculation.
        is_to_the_moon: Flag indicating if the signal has a "To the moon" TP.
        """
        if not self.is_connected:
            self.logger.error("BybitHandler not connected.")
            return None

        self.logger.info(f"Preparing trade for {symbol}: Side={side}, Entry={entry_price}, Qty={qty_str}, SL={stop_loss}")

        # Place conditional market order without SL/TP
        order_id = await self.client.place_order(
            symbol=symbol,
            side=side,
            qty_str=qty_str,
            order_type="MARKET",
            trigger_price=entry_price
        )

        if not order_id:
            self.logger.error(f"Failed to place entry order for {symbol}.")
            return None

        self.logger.info(f"Entry order placed for {symbol}. Order ID: {order_id}")

        # Poll for order status until filled
        filled_order_info = await self._wait_for_order_fill(symbol, order_id)

        if not filled_order_info:
            self.logger.error(f"Entry order {order_id} for {symbol} did not fill. Aborting trade.")
            return None

        filled_qty = float(filled_order_info.get("cumExecQty"))
        avg_fill_price = float(filled_order_info.get("avgPrice"))
        self.logger.info(f"Entry order {order_id} for {symbol} filled. Quantity: {filled_qty}, Avg Price: {avg_fill_price}")

        # Set exchange-side Stop Loss
        self.logger.info(f"Attempting to set exchange-side Stop Loss for {symbol} at {stop_loss}...")
        sl_set_success = await self.client.set_trading_stop(symbol=symbol, sl_price=stop_loss)

        if not sl_set_success:
            self.logger.critical(f"CRITICAL: Failed to set exchange-side Stop Loss for {symbol} at {stop_loss}. Manual intervention required!")
        else:
            self.logger.info(f"Exchange-side Stop Loss for {symbol} set successfully at {stop_loss}.")
            sl_verified = await self.client.verify_stop_loss(symbol, stop_loss)
            if not sl_verified:
                self.logger.critical(f"CRITICAL: Stop Loss for {symbol} at {stop_loss} could not be verified on the exchange after setting. Manual intervention required!")
            else:
                self.logger.info(f"SL for {symbol} verified on exchange at {stop_loss}.")

        initial_usd_invested = filled_qty * avg_fill_price
        self.track_position(symbol, side, initial_usd_invested, avg_fill_price, stop_loss, all_take_profits[0] if all_take_profits else None, all_take_profits, atr, is_to_the_moon)
        return order_id

    async def _recover_open_positions(self):
        """
        Fetches all open positions on startup and ensures a Stop Loss is set for each.
        """
        self.logger.info("Starting open positions recovery process...")
        try:
            open_positions_response = self.client.session.get_positions(category="linear", settleCoin="USDT")
            if open_positions_response and open_positions_response.get("retCode") == 0:
                positions = open_positions_response.get("result", {}).get("list", [])
                for position in positions:
                    symbol = position.get("symbol")
                    position_size = float(position.get("size", "0"))
                    if position_size > 0:
                        current_sl = position.get("stopLoss")
                        if not current_sl or float(current_sl) == 0.0:
                            self.logger.warning(f"Found open position for {symbol} with no Stop Loss. Attempting to set a protective SL.")

                            tracked_position_data = self.tracked_positions.get(symbol)
                            protective_sl_price = None

                            if tracked_position_data and tracked_position_data.get("stop_loss"):
                                protective_sl_price = tracked_position_data["stop_loss"]
                                self.logger.info(f"Found original SL {protective_sl_price} for {symbol} from tracked positions.")
                            else:
                                self.logger.warning(f"No original SL found in tracked positions for {symbol}. Cannot automatically set protective SL.")

                            if protective_sl_price:
                                self.logger.info(f"Setting protective SL for {symbol} at {protective_sl_price}...")
                                sl_set_success = await self.client.set_trading_stop(symbol=symbol, sl_price=protective_sl_price)
                                if sl_set_success:
                                    sl_verified = await self.client.verify_stop_loss(symbol, protective_sl_price)
                                    if sl_verified:
                                        self.logger.info(f"Protective SL for {symbol} set and verified at {protective_sl_price}.")
                                    else:
                                        self.logger.critical(f"CRITICAL: Protective SL for {symbol} at {protective_sl_price} could not be verified after setting. Manual intervention required!")
                                else:
                                    self.logger.critical(f"CRITICAL: Failed to set protective SL for {symbol} at {protective_sl_price}. Manual intervention required!")
                            else:
                                self.logger.warning(f"Could not determine a protective SL for {symbol}. Manual intervention recommended.")
            else:
                self.logger.error(f"Failed to retrieve open positions during recovery: {open_positions_response.get('retMsg', 'Unknown error')}")
        except Exception as e:
            self.logger.error(f"Error during open positions recovery: {e}", exc_info=True)
        self.logger.info("Open positions recovery process completed.")

    async def get_current_price(self, symbol: str) -> Optional[float]:
        """Gets the current market price for a given symbol."""
        if not self.is_connected:
            self.logger.error("BybitHandler not connected.")
            return None
        return await self.client.get_symbol_price(symbol)

    async def disconnect(self):
        """Disconnects from Bybit."""
        if self.client:
            self.logger.info("Bybit client session closed.")
        self.logger.info("Bybit handler disconnected.")
        self.is_connected = False

    def track_position(self, symbol: str, side: str, initial_usd_invested: float, entry_price: float, stop_loss: Optional[float], take_profit: Optional[float], all_take_profits: Optional[list[float]], atr: Optional[float], is_to_the_moon: bool = False):
        """
        Tracks a newly opened position for sell strategy (TP/SL).
        initial_usd_invested should be in USDT.
        """
        self.tracked_positions[symbol] = {
            "side": side,
            "initial_usd_invested": initial_usd_invested,
            "entry_price": entry_price,
            "entry_time": datetime.now(),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "atr": atr,
            "take_profits": all_take_profits,
            "current_tp_index": 0,
            "is_to_the_moon": is_to_the_moon,
            "trailing_stop_active": False,
            "trailing_stop_price": None,
            "trailing_stop_distance": atr * self.config.risk_management.atr_trailing_stop_multiplier if atr else None
        }
        self.logger.info(f"Tracking new position for {symbol}: {side}, Invested {initial_usd_invested} USDT at {entry_price}, SL: {stop_loss}, TP: {take_profit}, ToTheMoon: {is_to_the_moon}")

    async def monitor_and_execute_sells(self, symbol: str):
        """
        Monitors a single tracked position and executes sell/close orders based on predefined targets (TP/SL).
        """
        position = self.tracked_positions.get(symbol)
        if not position:
            return

        try:
            current_price = await self.client.get_symbol_price(symbol)
            if current_price is None:
                self.logger.warning(f"Could not get current price for {symbol}. Skipping sell monitoring.")
                return

            position_info = await self.client.get_position(symbol)
            if not position_info or float(position_info.get("size", 0)) == 0:
                self.logger.info(f"Position for {symbol} is closed. Removing from tracking.")
                del self.tracked_positions[symbol]
                return

            # Refresh position data from tracking
            entry_price = position["entry_price"]
            side = position["side"]
            current_position_size = float(position_info.get("size"))
            current_stop_loss = position["stop_loss"]
            all_take_profits = position["take_profits"]
            current_tp_index = position["current_tp_index"]
            is_to_the_moon = position["is_to_the_moon"]
            atr = position["atr"]

            # Calculate PnL percentage (for logging/info)
            if side == "Buy":
                pnl_percentage = (current_price - entry_price) / entry_price
            else:
                pnl_percentage = (entry_price - current_price) / entry_price

            self.logger.info(f"Monitoring {symbol}: Current Price={current_price:.4f}, PnL: {pnl_percentage:.2%}")

            # --- Take Profit (TP) Logic ---
            if all_take_profits and current_tp_index < len(all_take_profits):
                next_tp = all_take_profits[current_tp_index]
                tp_hit = False
                if side == "Buy" and current_price >= next_tp:
                    tp_hit = True
                elif side == "Sell" and current_price <= next_tp:
                    tp_hit = True

                if tp_hit:
                    self.logger.info(f"TP {current_tp_index + 1} hit for {symbol} at {next_tp:.4f}.")
                    position["current_tp_index"] += 1

                    if position["current_tp_index"] == len(all_take_profits) and not is_to_the_moon:
                        self.logger.info(f"Final TP hit for {symbol}. Closing entire position.")
                        close_side = "Sell" if side == "Buy" else "Buy"
                        formatted_qty = await self.client.format_quantity(symbol, current_position_size)
                        order_id = await self.client.place_order(symbol=symbol, side=close_side, qty_str=formatted_qty, order_type="MARKET", reduce_only=True)
                        if order_id:
                            self.logger.info(f"Position for {symbol} closed by final TP. Order ID: {order_id}")
                            del self.tracked_positions[symbol]
                        else:
                            self.logger.error(f"Failed to close position for {symbol} by final TP.")
                        return
                    elif position["current_tp_index"] == len(all_take_profits) and is_to_the_moon:
                        self.logger.info(f"Final TP is 'To the moon' for {symbol}. Activating dynamic ATR-based trailing stop.")
                        position["trailing_stop_active"] = True
                        if side == "Buy":
                            position["trailing_stop_price"] = current_price - (atr * self.config.risk_management.atr_trailing_stop_multiplier)
                        else:
                            position["trailing_stop_price"] = current_price + (atr * self.config.risk_management.atr_trailing_stop_multiplier)
                        self.logger.info(f"Trailing stop activated for {symbol} (ToTheMoon). Initial trailing stop price: {position['trailing_stop_price']:.4f}")
                        await self.client.set_trading_stop(symbol=symbol, sl_price=position["trailing_stop_price"])
                    else:
                        new_sl = entry_price
                        if current_tp_index > 0:
                            new_sl = all_take_profits[current_tp_index - 1]

                        self.logger.info(f"Moving Stop Loss for {symbol} to {new_sl:.4f} (after hitting TP {current_tp_index}).")
                        position["stop_loss"] = new_sl
                        await self.client.set_trading_stop(symbol=symbol, sl_price=new_sl)

            # --- Trailing Stop Logic (for "To the moon" only) ---
            if is_to_the_moon and position["trailing_stop_active"] and atr and position["trailing_stop_price"] is not None:
                if side == "Buy":
                    new_trailing_stop_price = current_price - (atr * self.config.risk_management.atr_trailing_stop_multiplier)
                    if new_trailing_stop_price > position["trailing_stop_price"]:
                        position["trailing_stop_price"] = new_trailing_stop_price
                        self.logger.info(f"Trailing stop updated for {symbol} (ToTheMoon Long). New trailing stop price: {position['trailing_stop_price']:.4f}")
                        await self.client.set_trading_stop(symbol=symbol, sl_price=new_trailing_stop_price)
                else:
                    new_trailing_stop_price = current_price + (atr * self.config.risk_management.atr_trailing_stop_multiplier)
                    if new_trailing_stop_price < position["trailing_stop_price"]:
                        position["trailing_stop_price"] = new_trailing_stop_price
                        self.logger.info(f"Trailing stop updated for {symbol} (ToTheMoon Short). New trailing stop price: {position['trailing_stop_price']:.4f}")
                        await self.client.set_trading_stop(symbol=symbol, sl_price=new_trailing_stop_price)

                if (side == "Buy" and current_price <= position["trailing_stop_price"]) or \
                   (side == "Sell" and current_price >= position["trailing_stop_price"]):
                    self.logger.info(f"TRADE CLOSED: Trailing stop hit for {symbol} (ToTheMoon {side}). Current Price: {current_price:.4f}, Trailing Stop: {position['trailing_stop_price']:.4f}")
                    del self.tracked_positions[symbol]
                    return

            # --- Initial Stop Loss (SL) check (if trailing stop not active) ---
            if current_stop_loss is not None and not position["trailing_stop_active"]:
                sl_hit = False
                if side == "Buy" and current_price <= current_stop_loss:
                    sl_hit = True
                elif side == "Sell" and current_price >= current_stop_loss:
                    sl_hit = True

                if sl_hit:
                    self.logger.info(f"TRADE CLOSED: Stop loss hit for {symbol} ({side}). Current Price: {current_price:.4f}, SL: {current_stop_loss:.4f}")
                    del self.tracked_positions[symbol]
                    return

        except Exception as e:
            self.logger.error(f"Error monitoring {symbol}: {e}")