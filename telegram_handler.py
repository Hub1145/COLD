"""
Complete Telegram integration for signal reception and parsing
"""

import asyncio
import logging
import re
import os
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from bybit_handler import BybitHandler # Import BybitHandler
import traceback # Import traceback for detailed error logging
from telethon import errors as telethon_errors # Import specific Telethon errors
from config import Config # Import Config
from indicators import TechnicalIndicators # Import TechnicalIndicators

# Correct the typo: rpc_error_list should be rpcerrorlist
# This is a placeholder, the actual fix will be in the try-except block below

class SignalParser:
    """Parse trading signals from Telegram messages"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
    def parse_signal(self, message_text: str, preceding_messages_texts: Optional[list[str]] = None) -> Optional[Dict[str, Any]]:
        """Parse a trading signal from message text based on the new format."""
        try:
            # Expected format:
            # 🪙 XPL/USDT
            # Exchanges: BYBIT - link
            #
            # 🔴 SHORT
            # Cross (75X)
            #
            # Entry Targets:
            # 1.2592
            #
            # 🎯 TP:
            # 1) 1.24661
            # 2) 1.23402
            # ...
            # ⛔️ SL:
            # 1.38512

            # Extract symbol
            symbol_match = re.search(r'(?:🪙\s*)?([A-Za-z0-9]+)/([A-Za-z0-9]+)', message_text)
            if not symbol_match:
                self.logger.warning(f"Could not parse symbol from message: '{message_text}'")
                return None
            base_asset = symbol_match.group(1).upper()
            quote_asset = symbol_match.group(2).upper()
            symbol = f"{base_asset}{quote_asset}"

            # Extract direction
            direction_match = re.search(r'(🔴\s*SHORT|🟢\s*LONG|\(LONG\)|\(SHORT\))', message_text, re.IGNORECASE)
            if not direction_match:
                self.logger.warning(f"Could not parse direction from message: '{message_text}'")
                return None
            matched_direction_text = direction_match.group(1).upper()
            if "SHORT" in matched_direction_text:
                direction = "SHORT"
            elif "LONG" in matched_direction_text:
                direction = "LONG"
            else:
                self.logger.warning(f"Unrecognized direction format: '{matched_direction_text}'")
                return None

            # Extract entry price (first entry target)
            entry_target_match = re.search(r'Entry Targets:\s*\n\s*(\d+(\.\d+)?)', message_text)
            if not entry_target_match:
                self.logger.warning(f"Could not parse entry price from message: '{message_text}'")
                return None
            entry_price = float(entry_target_match.group(1))

            # Extract Stop Loss
            sl_match = re.search(r'(?:⛔️\s*)?SL:\s*\n\s*(\d+(\.\d+)?)', message_text)
            stop_loss = float(sl_match.group(1)) if sl_match else None

            # Extract Take Profit levels
            tp_matches = re.findall(r'(?:🎯\s*)?TP:\s*\n(?:\s*\d+\)\s*(\d+(\.\d+)?)\s*\n?)+', message_text)
            take_profits = []
            if tp_matches:
                # The regex captures groups for each number, so we flatten the list
                for match_tuple in tp_matches:
                    for val in match_tuple:
                        if re.match(r'\d+(\.\d+)?', val): # Ensure it's a number
                            take_profits.append(float(val))
            
            # The client mentioned "as long as the bot's technical indicators feel the TP is good then let the bot move upon it"
            # For now, we will just pass all TPs and let the executor decide, or use the first one as a default if not decided by indicators.
            # Check for "To the moon"
            is_to_the_moon = "To the moon" in message_text

            self.logger.info(f"Parsed signal: Symbol={symbol}, Direction={direction}, EntryPrice={entry_price}, SL={stop_loss}, TPs={take_profits}, ToTheMoon={is_to_the_moon}")

            return {
                'type': 'trade_signal',
                'symbol': symbol,
                'direction': direction,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profits': take_profits,
                'raw_message': message_text,
                'parsed_at': datetime.now().isoformat(),
                'parsed_at_datetime': datetime.now(), # Add datetime object for comparison
                'is_to_the_moon': is_to_the_moon
            }
            
        except Exception as e:
            self.logger.error(f"Error parsing signal: {e}", exc_info=True) # Added exc_info for full traceback
            return None

class SignalExecutor:
    """Manages signal execution timing"""
    
    def __init__(self, config: Config, bybit_handler: BybitHandler): # Changed to accept full Config
        self.config = config # Store the full config
        self.bybit_handler = bybit_handler
        self.logger = logging.getLogger(__name__)
        self.pending_signals_file = "pending_signals.json"
        self.pending_signals: Dict[str, Dict[str, Any]] = {}
        self.monitoring_tasks: Dict[str, asyncio.Task] = {} # To hold tasks for price monitoring
        self.last_trade_time: Dict[str, datetime] = {} # To track last trade time for cooldown
        self.daily_pnl_percentage: float = 0.0 # Track daily PnL for drawdown
        self.last_pnl_reset_date: datetime = datetime.now().date() # Track date for daily PnL reset
        self.active_signals_per_symbol: Dict[str, int] = {} # To track the count of active signals per symbol

        self._load_pending_signals()
        # After loading, initialize active_signals_per_symbol counts
        for signal_id, signal_data in self.pending_signals.items():
            symbol = signal_data['symbol']
            self.active_signals_per_symbol[symbol] = self.active_signals_per_symbol.get(symbol, 0) + 1
        asyncio.create_task(self._start_all_pending_monitoring_tasks())

    def get_active_symbols(self) -> List[str]:
        """Returns a list of symbols for which there are active signals being monitored."""
        return list(self.active_signals_per_symbol.keys())

    def set_bybit_handler(self, bybit_handler: BybitHandler):
        """Sets the BybitHandler instance for the SignalExecutor."""
        self.bybit_handler = bybit_handler
        self.logger.info("BybitHandler set in SignalExecutor.")

    async def _check_daily_drawdown(self) -> bool:
        """Checks if the daily drawdown limit has been reached."""
        current_date = datetime.now().date()
        if current_date != self.last_pnl_reset_date:
            self.logger.info(f"New day detected. Resetting daily PnL from {self.daily_pnl_percentage:.2f}% to 0%.")
            self.daily_pnl_percentage = 0.0
            self.last_pnl_reset_date = current_date

        if self.daily_pnl_percentage <= -self.config.risk_management.daily_drawdown_stop_percentage:
            self.logger.warning(f"Daily drawdown limit ({self.config.risk_management.daily_drawdown_stop_percentage}%) reached. No new trades will be placed today. Current daily PnL: {self.daily_pnl_percentage:.2f}%.")
            return False
        return True
        
    async def schedule_signal(self, signal: Dict[str, Any]) -> bool:
        """Schedule a signal for execution. If a previous signal is pending, it will be cancelled."""
        try:
            if signal['type'] == 'trade_signal':
                symbol = signal['symbol']

                # Daily drawdown check
                if not await self._check_daily_drawdown():
                    return False

                # Per-pair cooldown check
                if symbol in self.last_trade_time:
                    time_since_last_trade = datetime.now() - self.last_trade_time[symbol]
                    if time_since_last_trade < timedelta(minutes=self.config.risk_management.per_pair_cooldown_minutes):
                        self.logger.warning(f"Signal for {symbol} ignored due to cooldown. Time remaining: {timedelta(minutes=self.config.risk_management.per_pair_cooldown_minutes) - time_since_last_trade}")
                        return False

                # Max concurrent trades check
                open_positions_count = len(self.bybit_handler.tracked_positions)
                max_concurrent_trades = int(self.config.risk_management.max_total_exposure_percentage / self.config.risk_management.risk_per_trade_percentage)
                
                if open_positions_count >= max_concurrent_trades:
                    self.logger.warning(f"Max concurrent trades ({max_concurrent_trades}) reached. Signal for {symbol} will be queued.")
                    # In a real scenario, you'd add it to a queue and process later
                    return False # For now, we just reject it to keep it simple

                signal_id = f"trade_{symbol}_{signal['direction']}_{signal['parsed_at']}"
                self.pending_signals[signal_id] = {
                    **signal,
                    'executed': False
                }
                self._save_pending_signals() # Save the new signal

                # Increment active signals count for this symbol
                self.active_signals_per_symbol[symbol] = self.active_signals_per_symbol.get(symbol, 0) + 1
                self.logger.info(f"Scheduled signal for {symbol}. Active signals for {symbol}: {self.active_signals_per_symbol[symbol]}")

                # Start monitoring task for this new signal
                await self._start_monitoring_task(signal_id)
                return True
            
            return False # Not a recognized signal type for scheduling
            
        except Exception as e:
            self.logger.error(f"Error scheduling signal: {e}", exc_info=True)
            return False
    
    async def _execute_trade_signal(self, signal_id: str):
        """Execute a scheduled trade signal"""
        try:
            if signal_id not in self.pending_signals:
                self.logger.debug(f"Signal {signal_id} not found in pending_signals.")
                return

            signal = self.pending_signals[signal_id]
            if signal.get('executed'):
                self.logger.info(f"Signal {signal_id} already executed.")
                return

            symbol = signal['symbol']
            entry_price = signal['entry_price']
            side = "Buy" if signal['direction'] == "LONG" else "Sell"

            self.logger.info(f"Processing trade signal: {symbol} {side} @ {entry_price}")

            # Set leverage as soon as the signal is received
            target_leverage = self.config.trading.leverage
            self.logger.info(f"Setting leverage for {symbol} to {target_leverage}x from config.json")
            if not await self.bybit_handler.client.set_leverage(symbol, int(target_leverage)):
                self.logger.error(f"Failed to set leverage for {symbol}. Aborting signal execution.")
                await self._cleanup_signal(signal_id)
                return

            # Subscribe to WebSocket kline for the symbol
            await self.bybit_handler.client.subscribe_to_kline(symbol, self.config.trading.timeframe)
            self.logger.info(f"Subscribed to WebSocket for {symbol} to monitor entry price.")
            await asyncio.sleep(2) # Add a small delay to allow initial WebSocket data to arrive

            # Start monitoring task
            monitoring_task = asyncio.create_task(
                self._monitor_entry_price(signal_id, symbol, side, entry_price, signal['parsed_at_datetime'])
            )
            self.monitoring_tasks[signal_id] = monitoring_task
            self.last_trade_time[symbol] = datetime.now() # Update last trade time for cooldown

        except Exception as e:
            self.logger.error(f"Error initiating trade signal {signal_id}: {e}", exc_info=True)
            await self._cleanup_signal(signal_id)

    async def _monitor_entry_price(self, signal_id: str, symbol: str, side: str, entry_price: float, parsed_at_datetime: datetime):
        """
        Continuously monitors the real-time price of a symbol via WebSocket,
        fetches kline data, calculates indicators, and places a trade when
        entry conditions and signal logic are met.
        """
        self.logger.info(f"Starting real-time price monitoring for {symbol} (Signal ID: {signal_id})")
        
        last_kline_fetch_time = None
        cached_df = None

        try:
            while signal_id in self.pending_signals and not self.pending_signals[signal_id].get('executed'):
                # Check for signal expiration
                time_since_parsed = datetime.now() - parsed_at_datetime
                if time_since_parsed > timedelta(days=self.config.risk_management.signal_expiration_days):
                    self.logger.warning(f"Signal for {symbol} (ID: {signal_id}) expired after {self.config.risk_management.signal_expiration_days} days. Stopping monitoring.")
                    break # Break the loop and cleanup

                # Determine if we need to fetch new kline data
                fetch_new_kline = False
                if cached_df is None or last_kline_fetch_time is None:
                    fetch_new_kline = True
                else:
                    time_since_last_fetch = (datetime.now() - last_kline_fetch_time).total_seconds()
                    if time_since_last_fetch >= self.config.trading.kline_refresh_interval_seconds:
                        fetch_new_kline = True

                if fetch_new_kline:
                    df = await self.bybit_handler.client.get_kline(
                        symbol,
                        self.config.trading.timeframe
                    )

                    if df is None or df.empty:
                        self.logger.debug(f"No kline data for {symbol}. Waiting for next refresh cycle.")
                        await asyncio.sleep(self.config.trading.kline_refresh_interval_seconds)
                        continue
                    
                    cached_df = df
                    last_kline_fetch_time = datetime.now()
                    self.logger.debug(f"Fetched new kline data for {symbol}.")
                else:
                    df = cached_df
                    self.logger.debug(f"Using cached kline data for {symbol}.")
                
                # Get the latest WebSocket price
                current_market_price = self.bybit_handler.client.realtime_prices.get(symbol)
                if current_market_price is None:
                    # Fallback to last kline close price if WS not available, but log a warning
                    current_market_price = df.iloc[-1]["Close"]
                    self.logger.warning(f"WebSocket price not available for {symbol}. Using kline close price: {current_market_price}")


                # Create a temporary DataFrame for indicator calculation with the latest price
                # This prevents modifying the historical kline data directly
                temp_df = df.copy()
                # Update the last candle's close price with the real-time price
                temp_df.loc[temp_df.index[-1], 'Close'] = current_market_price
                # Ensure High/Low also reflect the current price if it's an extreme
                temp_df.loc[temp_df.index[-1], 'High'] = max(temp_df.loc[temp_df.index[-1], 'High'], current_market_price)
                temp_df.loc[temp_df.index[-1], 'Low'] = min(temp_df.loc[temp_df.index[-1], 'Low'], current_market_price)


                # Calculate indicators on the temporary DataFrame
                df_with_indicators = self.bybit_handler.client.indicators_calculator.calculate_all_indicators(
                    temp_df,
                    self.config.indicators
                )

                if df_with_indicators is None or df_with_indicators.empty:
                    self.logger.warning(f"Could not calculate indicators for {symbol} with live price. Waiting for next refresh cycle.")
                    await asyncio.sleep(self.config.trading.kline_refresh_interval_seconds)
                    continue

                latest_kline_with_indicators = df_with_indicators.iloc[-1]

                # Evaluate signal conditions based on indicators
                signal_valid, confirmation_count = self._evaluate_signal_conditions(symbol, latest_kline_with_indicators, current_market_price, side)

                if not signal_valid:
                    self.logger.info(f"Signal conditions not met for {symbol} ({side}). Waiting for conditions to align.")
                    await asyncio.sleep(self.config.risk_management.per_pair_cooldown_minutes * 60) # Wait for a full interval before re-checking
                    continue # Continue monitoring
                
                # Check entry price condition within tolerance
                entry_price_tolerance = self.config.risk_management.entry_price_tolerance_percentage / 100 * entry_price
                lower_bound = entry_price - entry_price_tolerance
                upper_bound = entry_price + entry_price_tolerance

                entry_condition_met = False
                if side == "Buy": # Long position
                    # For long, current price should be at or below entry, but within tolerance
                    if lower_bound <= current_market_price <= entry_price:
                        entry_condition_met = True
                else: # Short position
                    # For short, current price should be at or above entry, but within tolerance
                    if entry_price <= current_market_price <= upper_bound:
                        entry_condition_met = True

                if entry_condition_met:
                    self.logger.info(f"Entry price ({entry_price}) and signal conditions met for {symbol} {side} at {current_market_price} (within tolerance). Attempting trade placement.")
                    await self._attempt_trade_placement(signal_id, symbol, side, entry_market_price=current_market_price, atr=latest_kline_with_indicators['ATR']) # Pass ATR
                    break # Exit loop after attempting trade
                else:
                    self.logger.info(f"Signal valid for {symbol} {side}, but current price {current_market_price} is not within entry range [{lower_bound:.4f}, {upper_bound:.4f}] of entry price ({entry_price}). Waiting...")
                    await asyncio.sleep(self.config.risk_management.per_pair_cooldown_minutes * 60) # Wait for a full interval before re-checking
                    continue # Continue monitoring
        except asyncio.CancelledError:
            self.logger.info(f"Price monitoring for {symbol} (Signal ID: {signal_id}) cancelled.")
        except Exception as e:
            self.logger.error(f"Error during price monitoring for {symbol} (Signal ID: {signal_id}): {e}", exc_info=True)
        finally:
            self.logger.info(f"Stopping real-time price monitoring for {symbol} (Signal ID: {signal_id}).")
            await self._cleanup_signal(signal_id)
            
    def _evaluate_signal_conditions(self, symbol: str, latest_kline: pd.Series, current_market_price: float, side: str) -> (bool, int):
        """
        Evaluates the mandatory and optional signal conditions based on the latest kline data.
        Returns a tuple: (signal_valid: bool, confirmation_count: int)
        """
        mandatory_conditions_met = False
        optional_confirmations = 0

        # Mandatory: Price >200 EMA + MACD bullish crossover for LONG
        # Mandatory: Price <200 EMA + MACD bearish crossover for SHORT

        ema_200 = latest_kline[f'EMA_{self.config.indicators.ema_200}']
        macd_line = latest_kline['MACD_Line']
        macd_signal = latest_kline['MACD_Signal']
        macd_hist = latest_kline['MACD_Hist']

        if side == "Buy": # LONG
            if current_market_price > ema_200:
                self.logger.debug(f"LONG: Price ({current_market_price}) > EMA_200 ({ema_200})")
                if macd_line > macd_signal and macd_hist > 0: # Bullish crossover and histogram above zero
                    self.logger.debug(f"LONG: MACD Bullish Crossover (MACD: {macd_line}, Signal: {macd_signal}, Hist: {macd_hist})")
                    mandatory_conditions_met = True
                else:
                    self.logger.debug(f"LONG: MACD not bullish crossover (MACD: {macd_line}, Signal: {macd_signal}, Hist: {macd_hist})")
            else:
                self.logger.debug(f"LONG: Price ({current_market_price}) not > EMA_200 ({ema_200})")
        else: # Sell (SHORT)
            if current_market_price < ema_200:
                self.logger.debug(f"SHORT: Price ({current_market_price}) < EMA_200 ({ema_200})")
                if macd_line < macd_signal and macd_hist < 0: # Bearish crossover and histogram below zero
                    self.logger.debug(f"SHORT: MACD Bearish Crossover (MACD: {macd_line}, Signal: {macd_signal}, Hist: {macd_hist})")
                    mandatory_conditions_met = True
                else:
                    self.logger.debug(f"SHORT: MACD not bearish crossover (MACD: {macd_line}, Signal: {macd_signal}, Hist: {macd_hist})")
            else:
                self.logger.debug(f"SHORT: Price ({current_market_price}) not < EMA_200 ({ema_200})")

        if not mandatory_conditions_met:
            return False, 0

        # Optional confirmations
        # 50 EMA > 200 EMA for LONG, 50 EMA < 200 EMA for SHORT
        ema_50 = latest_kline[f'EMA_{self.config.indicators.ema_50}']
        if side == "Buy":
            if ema_50 > ema_200:
                self.logger.debug(f"LONG Optional: EMA_50 ({ema_50}) > EMA_200 ({ema_200})")
                optional_confirmations += 1
        else:
            if ema_50 < ema_200:
                self.logger.debug(f"SHORT Optional: EMA_50 ({ema_50}) < EMA_200 ({ema_200})")
                optional_confirmations += 1

        # RSI > 40 for LONG, RSI < 60 for SHORT
        rsi = latest_kline['RSI']
        if side == "Buy":
            if rsi > self.config.signals.rsi_long_threshold:
                self.logger.debug(f"LONG Optional: RSI ({rsi}) > {self.config.signals.rsi_long_threshold}")
                optional_confirmations += 1
        else:
            if rsi < self.config.signals.rsi_short_threshold:
                self.logger.debug(f"SHORT Optional: RSI ({rsi}) < {self.config.signals.rsi_short_threshold}")
                optional_confirmations += 1

        # Bollinger Bands for optional confirmation
        if self.config.signals.bollinger_band_confirmation:
            lower_bb = latest_kline['BB_Lower']
            upper_bb = latest_kline['BB_Upper']
            mid_bb = latest_kline['BB_Mid']
            if side == "Buy": # Price touches/above middle BB
                if current_market_price > mid_bb:
                    self.logger.debug(f"LONG Optional: Price ({current_market_price}) > Middle BB ({mid_bb})")
                    optional_confirmations += 1
            else: # Price touches/below middle BB
                if current_market_price < mid_bb:
                    self.logger.debug(f"SHORT Optional: Price ({current_market_price}) < Middle BB ({mid_bb})")
                    optional_confirmations += 1
        
        # Trade valid when mandatory + >=1 optional condition is met.
        signal_valid = mandatory_conditions_met and optional_confirmations >= 1
        self.logger.info(f"Signal evaluation for {symbol} {side}: Mandatory={mandatory_conditions_met}, Optional={optional_confirmations}, Final Valid={signal_valid}")
        return signal_valid, optional_confirmations

    async def _attempt_trade_placement(self, signal_id: str, symbol: str, side: str, entry_market_price: float, atr: float):
        """Attempts to place the trade after entry conditions and signal logic are met."""
        signal = self.pending_signals.get(signal_id)
        if not signal or signal.get('executed'):
            return

        try:
            # Get account equity for risk calculation
            account_balance = await self.bybit_handler.get_usdt_balance()
            if account_balance is None:
                self.logger.error("Could not get account balance. Aborting trade placement.")
                return

            # Use SL from signal
            stop_loss_price = signal.get('stop_loss')
            if stop_loss_price is None:
                self.logger.error(f"Stop loss not provided in signal for {symbol}. Aborting trade placement.")
                return

            # Calculate risk amount per trade using the signal's stop loss
            # Position Size = (Risk Amount / Stop Loss Distance) * Leverage
            # Stop loss distance is the difference between entry price and stop loss price
            stop_loss_distance = abs(entry_market_price - stop_loss_price)

            if stop_loss_distance == 0:
                self.logger.warning("Stop loss distance is zero. Cannot calculate position size. Aborting trade.")
                return None
            
            risk_amount_usd = account_balance * (self.config.risk_management.risk_per_trade_percentage / 100)
            self.logger.info(f"Account Balance: {account_balance:.2f} USDT, Risk per trade: {risk_amount_usd:.2f} USDT")

            position_size_usd = (risk_amount_usd / stop_loss_distance) * self.config.trading.leverage
            quantity = position_size_usd / entry_market_price
            
            formatted_qty = await self.bybit_handler.client.format_quantity(symbol, quantity)
            self.logger.info(f"Calculated position size: {formatted_qty} {symbol} for {symbol} at entry {entry_market_price}")

            # Use TP from signal
            take_profits = signal.get('take_profits', [])
            initial_take_profit = take_profits[0] if take_profits else None # Use the first TP for the initial order

            order_id = await self.bybit_handler.place_trade(
                symbol=symbol,
                side=side,
                qty_str=formatted_qty,
                entry_price=entry_market_price, # Use the actual market price at entry
                stop_loss=stop_loss_price, # Pass calculated stop loss
                all_take_profits=take_profits, # Pass all TPs for tracking
                atr=atr, # Pass ATR for trailing stop calculation
                is_to_the_moon=signal.get('is_to_the_moon', False) # Pass "To the moon" flag
            )

            if order_id:
                signal['executed'] = True
                self.logger.info(f"Trade executed successfully. Order ID: {order_id}")
                trade_logger = logging.getLogger('trades')
                trade_logger.info(f"BYBIT_TRADE_EXECUTED | {json.dumps(signal)}")
            else:
                self.logger.error(f"Trade execution failed for signal {signal_id}.")
        except Exception as e:
            self.logger.error(f"Error during trade placement for signal {signal_id}: {e}", exc_info=True)
        finally:
            await self._cleanup_signal(signal_id)

    
    async def _cleanup_signal(self, signal_id: str):
        """Clean up executed signal and monitoring tasks."""
        try:
            if signal_id in self.pending_signals:
                symbol = self.pending_signals[signal_id]['symbol']
                del self.pending_signals[signal_id]
                self._save_pending_signals() # Save changes after cleanup

                # Decrement active signals count for this symbol
                if symbol in self.active_signals_per_symbol:
                    self.active_signals_per_symbol[symbol] -= 1
                    self.logger.info(f"Cleaned up signal for {symbol}. Active signals for {symbol}: {self.active_signals_per_symbol[symbol]}")
                    if self.active_signals_per_symbol[symbol] <= 0:
                        self.logger.info(f"No more active signals for {symbol}. Unsubscribing from WebSocket.")
                        await self.bybit_handler.client.unsubscribe_from_kline(symbol, self.config.trading.timeframe)
                        del self.active_signals_per_symbol[symbol] # Remove symbol if no longer needed

            if signal_id in self.monitoring_tasks:
                task = self.monitoring_tasks[signal_id]
                if not task.done():
                    task.cancel()
                del self.monitoring_tasks[signal_id]
        except Exception as e:
            self.logger.error(f"Error cleaning up signal {signal_id}: {e}", exc_info=True)

    def _save_pending_signals(self):
        """Saves the current state of pending signals to a JSON file."""
        try:
            # Convert datetime objects to ISO format strings for JSON serialization
            serializable_signals = {}
            for signal_id, signal_data in self.pending_signals.items():
                serializable_signal_data = signal_data.copy()
                if 'parsed_at_datetime' in serializable_signal_data:
                    serializable_signal_data['parsed_at_datetime'] = serializable_signal_data['parsed_at_datetime'].isoformat()
                serializable_signals[signal_id] = serializable_signal_data

            with open(self.pending_signals_file, 'w') as f:
                json.dump(serializable_signals, f, indent=4)
            self.logger.info("Pending signals saved to file.")
        except Exception as e:
            self.logger.error(f"Error saving pending signals: {e}", exc_info=True)

    def _load_pending_signals(self):
        """Loads pending signals from a JSON file."""
        try:
            if os.path.exists(self.pending_signals_file):
                with open(self.pending_signals_file, 'r') as f:
                    loaded_signals = json.load(f)
                    # Convert ISO format strings back to datetime objects
                    for signal_id, signal_data in loaded_signals.items():
                        if 'parsed_at_datetime' in signal_data and isinstance(signal_data['parsed_at_datetime'], str):
                            signal_data['parsed_at_datetime'] = datetime.fromisoformat(signal_data['parsed_at_datetime'])
                        self.pending_signals[signal_id] = signal_data
                self.logger.info(f"Loaded {len(self.pending_signals)} pending signals from file.")
        except Exception as e:
            self.logger.warning(f"Could not load pending signals file or file is empty: {e}. Starting with no pending signals.", exc_info=True)

    async def _start_monitoring_task(self, signal_id: str):
        """Starts a monitoring task for a given signal_id."""
        signal = self.pending_signals[signal_id]
        symbol = signal['symbol']
        entry_price = signal['entry_price']
        side = "Buy" if signal['direction'] == "LONG" else "Sell"
        parsed_at_datetime = signal['parsed_at_datetime']

        # Ensure WebSocket is subscribed for this symbol
        await self.bybit_handler.client.subscribe_to_kline(symbol, self.config.trading.timeframe)
        await asyncio.sleep(2) # Small delay for WS to connect and send data

        monitoring_task = asyncio.create_task(
            self._monitor_entry_price(signal_id, symbol, side, entry_price, parsed_at_datetime)
        )
        self.monitoring_tasks[signal_id] = monitoring_task
        self.last_trade_time[symbol] = datetime.now() # Update last trade time for cooldown
        self.logger.info(f"Started monitoring for signal: {symbol} (ID: {signal_id})")


    async def _start_all_pending_monitoring_tasks(self):
        """Starts monitoring tasks for all loaded pending signals."""
        for signal_id, signal in list(self.pending_signals.items()):
            if not signal.get('executed') and signal_id not in self.monitoring_tasks:
                await self._start_monitoring_task(signal_id)


class TelegramHandler:
    """Complete Telegram integration"""
    
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.client = None
        self.parser = SignalParser()
        self.executor = None
        self.signal_callback = None
        self.session_string = None
        
        # Telegram credentials
        self.api_id = self.config.telegram.api_id
        self.api_hash = self.config.telegram.api_hash
        self.phone_number = None

        
    async def initialize(self):
        """Initialize Telegram client and signal executor"""
        try:
            self.logger.info("Initializing Telegram client...")
            
            self._load_session()

            if not self.phone_number:
                print("\n" + "="*60)
                print("    TELEGRAM LOGIN REQUIRED")
                print("="*60)
                print("Get API credentials from: https://my.telegram.org/apps")
                print()
                self.phone_number = input("Enter your phone number (with country code): ").strip()
                self.logger.info(f"Phone number entered: {self.phone_number}")
            
            session = StringSession(self.session_string) if self.session_string else StringSession()
            
            self.client = TelegramClient(
                session,
                self.api_id,
                self.api_hash
            )
            
            # Attempt to start the client. This will try to use the session string if available.
            # If no session string, or if it's invalid, it will try to initiate login (send code).
            self.logger.info(f"Attempting to start Telegram client with phone number: {self.phone_number}")
            try:
                await self.client.start(phone=self.phone_number)
                self.logger.info("Telegram client started successfully.")
            except telethon_errors.PhoneCodeEmptyError:
                # This error means Telethon tried to send a code but didn't get a prompt from Telegram.
                # It means the initial start() failed to get a code.
                self.logger.warning("Telegram client start failed to receive a phone code. Initiating explicit login flow.")
                if not await self._perform_full_login():
                    self.logger.error("Failed to complete explicit Telegram login flow.")
                    return False
            except telethon_errors.SessionPasswordNeededError:
                self.logger.warning("Two-Factor Authentication is enabled. Initiating explicit login flow for 2FA.")
                if not await self._perform_full_login(): # _perform_full_login now also handles 2FA
                    self.logger.error("Failed to complete explicit Telegram login flow with 2FA.")
                    return False
            except Exception as e:
                self.logger.error(f"Error during Telegram client start: {e}", exc_info=True)
                return False

            # After successful start or explicit login, ensure session is saved
            self.session_string = self.client.session.save()
            self._save_session()
            
            self.executor = SignalExecutor(self.config, None) # Initialize with None for bybit_handler initially
            
            self.logger.info("Telegram client initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Telegram client: {e}", exc_info=True)
            return False

    def set_bybit_handler(self, bybit_handler: BybitHandler):
        """Sets the BybitHandler instance for the SignalExecutor."""
        if self.executor:
            self.executor.set_bybit_handler(bybit_handler)
        else:
            self.logger.error("SignalExecutor not initialized in TelegramHandler.")

    async def test_connection(self) -> bool:
        """Tests the Telegram client connection."""
        if not self.client:
            self.logger.error("Telegram client is not initialized.")
            return False
        try:
            me = await self.client.get_me()
            self.logger.info(f"Connected as: {me.first_name}")
            return True
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def _resolve_channel_entity(self):
        """Resolves the Telegram channel entity from the identifier."""
        try:
            # Try to get entity by username first
            if self.config.telegram.channel_identifier.startswith('@'):
                return await self.client.get_entity(self.config.telegram.channel_identifier)
            else:
                # Try by title. This is more complex and might require iterating over dialogs
                # Iterate through dialogs to find the channel by title
                async for dialog in self.client.iter_dialogs():
                    if dialog.title == self.config.telegram.channel_identifier:
                        return dialog.entity
            self.logger.error(f"Could not resolve channel entity for identifier: {self.config.telegram.channel_identifier}")
            return None
        except Exception as e:
            self.logger.error(f"Error resolving channel entity: {e}", exc_info=True)
            return None

    async def _perform_full_login(self) -> bool:
        """Handles the explicit Telegram login flow, including code request and 2FA."""
        self.logger.info("Initiating explicit Telegram login flow (code request and sign-in)...")
        try:
            # Request the code explicitly
            self.logger.info(f"Sending code request for phone number: {self.phone_number}")
            code_request = await self.client.send_code_request(self.phone_number)
            
            # Prompt for code
            print("\n" + "="*60)
            print("    TELEGRAM VERIFICATION CODE REQUIRED")
            print("="*60)
            code = input("Enter the code you received in your Telegram app: ").strip()
            
            # Sign in with code
            self.logger.info("Attempting to sign in with provided code...")
            await self.client.sign_in(self.phone_number, code_request.phone_code_hash, code)
            self.logger.info("Successfully signed in to Telegram.")
            return True
        
        except telethon_errors.SessionPasswordNeededError:
            self.logger.warning("Two-Factor Authentication is enabled. Please enter your password.")
            print("\n" + "="*60)
            print("    TELEGRAM TWO-FACTOR AUTHENTICATION")
            print("="*60)
            password = input("Enter your Telegram 2FA password: ").strip()
            try:
                await self.client.sign_in(password=password)
                self.logger.info("Successfully signed in with 2FA password.")
                return True
            except Exception as e:
                self.logger.error(f"Failed to sign in with 2FA password: {e}", exc_info=True)
                return False
        except telethon_errors.PhoneCodeExpiredError:
            self.logger.error("The Telegram login code has expired. Please restart the bot to try again.")
            return False
        except telethon_errors.PhoneCodeInvalidError:
            self.logger.error("The Telegram login code entered is invalid. Please restart the bot and try again.")
            return False
        except Exception as e:
            self.logger.error(f"An unexpected error occurred during explicit Telegram login: {e}", exc_info=True)
            return False
    
    def _load_session(self):
        """Load saved Telegram session"""
        try:
            session_file = f"{self.config.telegram.session_name}.json"
            if os.path.exists(session_file):
                with open(session_file, 'r') as f:
                    data = json.load(f)
                    self.session_string = data.get('session_string')
                    self.phone_number = data.get('phone_number')
                self.logger.info("Telegram session loaded")
        except Exception as e:
            self.logger.debug(f"Could not load session: {e}", exc_info=True)
    
    def _save_session(self):
        """Save Telegram session"""
        try:
            session_file = f"{self.config.telegram.session_name}.json"
            data = {
                'session_string': self.session_string,
                'phone_number': self.phone_number,
                'saved_at': datetime.now().isoformat()
            }
            with open(session_file, 'w') as f:
                json.dump(data, f)
            self.logger.info("Telegram session saved")
        except Exception as e:
            self.logger.error(f"Could not save session: {e}", exc_info=True)
    
    async def start_monitoring(self, signal_callback):
        """Start monitoring channel for signals"""
        try:
            if not self.client:
                raise Exception("Telegram client not initialized")
            
            self.signal_callback = signal_callback
            
            # Resolve channel identifier to entity
            try:
                channel = await self.client.get_entity(self.config.telegram.channel_identifier)
            except ValueError: # Handle cases where it might be a private chat ID or title
                try:
                    # Attempt to find by title if not found by username/ID directly
                    dialogs = await self.client.get_dialogs()
                    channel = next((d.entity for d in dialogs if d.title == self.config.telegram.channel_identifier), None)
                    if not channel:
                        raise ValueError(f"Channel or group '{self.config.telegram.channel_identifier}' not found.")
                except Exception as e:
                    self.logger.error(f"Error resolving channel by title: {e}", exc_info=True)
                    raise

            self.logger.info(f"Monitoring channel: {getattr(channel, 'title', 'Unknown Title')} (ID: {channel.id})")
            
            @self.client.on(events.NewMessage(chats=channel))
            async def handle_message(event):
                # Fetch up to 2 preceding messages from the channel history
                preceding_messages_texts = []
                try:
                    # Get messages just before the current one
                    # Using event.message.id as offset_id fetches messages *older* than this ID.
                    # We want messages that were sent *just before* the current one.
                    # Telethon's iter_messages with offset_id works by getting messages *before* the given ID.
                    async for msg in self.client.iter_messages(channel, limit=2, offset_id=event.message.id):
                        if msg.text:
                            preceding_messages_texts.append(msg.text)
                            self.logger.info(f"Fetched preceding message (ID: {msg.id}): '{msg.text}'")
                except Exception as e:
                    self.logger.warning(f"Could not fetch preceding messages: {e}", exc_info=True)

                await self._handle_message(event, preceding_messages_texts)
            
            # The client.run_until_disconnected() method already provides continuous monitoring
            # using a websocket-like connection, so no further changes are needed here.
            await self.client.run_until_disconnected()
            
        except Exception as e:
            self.logger.error(f"Error monitoring channel: {e}", exc_info=True)
    
    async def _handle_message(self, event, preceding_messages_texts: Optional[list[str]] = None):
        """Handle new messages"""
        try:
            message_text = event.message.text
            if not message_text:
                return
            
            self.logger.info(f"Processing new message (ID: {event.message.id}): '{message_text}'")
            if preceding_messages_texts:
                self.logger.info(f"Preceding messages: {preceding_messages_texts}")

            signal = self.parser.parse_signal(message_text, preceding_messages_texts)
            if signal and self.signal_callback:
                await self.signal_callback(signal)
            
        except Exception as e:
            self.logger.error(f"Error handling message: {e}", exc_info=True)
    
    async def disconnect(self):
        """Disconnect from Telegram"""
        try:
            if self.client:
                await self.client.disconnect()
                self.logger.info("Telegram client disconnected")
        except Exception as e:
            self.logger.error(f"Error disconnecting: {e}", exc_info=True)
    
    async def test_connection(self) -> bool:
        """Test Telegram connection"""
        try:
            if not self.client:
                return False
            me = await self.client.get_me()
            self.logger.info(f"Connected as: {me.first_name}")
            return True
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False
