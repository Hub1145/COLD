"""
Main entry point for the Bybit Telegram Trading Bot
"""

import asyncio
import sys
import signal
import logging
import os
from datetime import datetime
from typing import Optional
from config import Config
from telegram_handler import TelegramHandler
from bybit_handler import BybitHandler
from logging.handlers import RotatingFileHandler
 
# For Windows: set the event loop policy to fix "WinError 6: The handle is invalid"
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
 
class TradingBot:
    """Main trading bot with Telegram signal integration"""
    
    def __init__(self):
        self.config: Optional[Config] = None
        self.logger: Optional[logging.Logger] = None
        self.telegram_handler: Optional[TelegramHandler] = None
        self.bybit_handler: Optional[BybitHandler] = None
        self.running = False
        self.health_check_task: Optional[asyncio.Task] = None
        self.sell_monitor_task: Optional[asyncio.Task] = None
        
    async def start(self):
        """Initialize and start the trading bot"""
        try:
            # Load configuration
            self.config = Config()
            self._setup_logging()
            self.logger = logging.getLogger(__name__)
            
            self.logger.info("=== Bybit Telegram Trading Bot Starting ===")
            self._log_startup_summary()
            
            if not self.config.validate():
                self.logger.error("Configuration validation failed. Please run config.py to reconfigure.")
                return False
            
            # Initialize Telegram handler and SignalExecutor first
            self.telegram_handler = TelegramHandler(self.config)
            self.logger.info("Initializing Telegram client...")
            if not await self.telegram_handler.initialize():
                self.logger.critical("Failed to initialize Telegram client. Exiting.")
                return False
            self.logger.info("Successfully initialized Telegram client.")
            
            # Now initialize BybitHandler, passing the get_active_symbols callback from SignalExecutor
            self.bybit_handler = BybitHandler(self.config)
            self.logger.info("Connecting to Bybit platform...")
            if not await self.bybit_handler.connect(get_active_symbols_callback=self.telegram_handler.executor.get_active_symbols):
                self.logger.critical("Failed to connect to Bybit platform. Exiting.")
                return False
            self.logger.info("Successfully connected to Bybit platform.")

            # Now that bybit_handler is connected, set it in telegram_handler's executor
            self.telegram_handler.set_bybit_handler(self.bybit_handler)
            
            # Test Telegram connection
            if not await self.telegram_handler.test_connection():
                self.logger.critical("Telegram connection test failed. Exiting.")
                return False
            self.logger.info("Telegram connection test successful.")
 
            self.running = True
            self._setup_signal_handlers()
            
            self.logger.info("Bot successfully connected to both platforms")
            self.logger.info("Starting Telegram signal monitoring...")
            
            # Set the signal callback for the TelegramHandler
            self.telegram_handler.signal_callback = self._handle_signal
            
            # Start monitoring Telegram channel
            await self.telegram_handler.start_monitoring(self._handle_signal)
 
            # Start background tasks
            self.sell_monitor_task = asyncio.create_task(self._run_sell_monitor())
            self.health_check_task = asyncio.create_task(self._run_health_checks())
            
            self.logger.info("Bot fully operational.")
            return True
            
        except KeyboardInterrupt:
            self.logger.info("Shutdown requested by user")
            await self.shutdown()
            return True
        except Exception as e:
            if self.logger:
                self.logger.critical(f"Critical error during bot startup: {e}", exc_info=True)
            else:
                print(f"Critical error during bot startup: {e}")
            return False
        
    async def _handle_signal(self, signal):
        """Handle incoming Telegram signal"""
        try:
            self.logger.info(f"Received signal: {signal['type']} for {signal.get('symbol')}")
            
            await self.telegram_handler.executor.schedule_signal(signal)
            
        except Exception as e:
            self.logger.error(f"Error handling signal: {e}")
    
    async def _run_sell_monitor(self):
        """Background task to continuously monitor positions for sell targets."""
        while self.running:
            try:
                # Iterate over a copy of tracked_positions to allow modification during iteration
                for symbol in list(self.bybit_handler.tracked_positions.keys()):
                    await self.bybit_handler.monitor_and_execute_sells(symbol)
                    await asyncio.sleep(1) # Small delay between symbols
                
                await asyncio.sleep(self.config.risk_management.per_pair_cooldown_minutes * 60) # Wait before next full cycle
            except asyncio.CancelledError:
                self.logger.info("Sell monitor task cancelled.")
                break
            except Exception as e:
                self.logger.error(f"Error in sell monitor task: {e}", exc_info=True)
                await asyncio.sleep(self.config.risk_management.per_pair_cooldown_minutes * 60) # Wait before retrying
 
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}, shutting down...")
            asyncio.create_task(self.shutdown())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def shutdown(self):
        """Graceful shutdown"""
        if self.running:
            self.running = False
            self.logger.info("Shutting down trading bot...")
            
            if self.sell_monitor_task:
                self.sell_monitor_task.cancel()
                try:
                    await self.sell_monitor_task
                except asyncio.CancelledError:
                    pass
                self.logger.info("Sell monitor task stopped.")

            if self.health_check_task:
                self.health_check_task.cancel()
                try:
                    await self.health_check_task
                except asyncio.CancelledError:
                    pass
                self.logger.info("Health check task stopped.")
 
            if self.bybit_handler:
                await self.bybit_handler.disconnect()
            
            if self.telegram_handler:
                await self.telegram_handler.disconnect()
            
            self.logger.info("Bot shutdown complete")
    
    def _setup_logging(self):
        """Setup logging configuration"""
        os.makedirs('logs', exist_ok=True)
        
        log_level = getattr(logging, self.config.logging.log_level.upper(), logging.INFO)
        log_file = f"logs/{self.config.logging.log_file}"
        
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.handlers.clear() # Clear existing handlers
        
        console_formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S')
        file_formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
 
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(log_level)
        root_logger.addHandler(console_handler)
        
        # File handler
        file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)
        
        # Trade logger
        trade_logger = logging.getLogger('trades')
        trade_logger.setLevel(logging.INFO)
        trade_handler = RotatingFileHandler(f"logs/trades_{datetime.now().strftime('%Y-%m-%d')}.log", maxBytes=5*1024*1024, backupCount=10)
        trade_handler.setFormatter(file_formatter)
        trade_logger.addHandler(trade_handler)
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("============================================================")
        self.logger.info("BYBIT TELEGRAM TRADING BOT - LOGGING INITIALIZED")
        self.logger.info(f"Log Level: {self.config.logging.log_level}")
        self.logger.info(f"Log File: {log_file}")
        self.logger.info("============================================================")
 
    def _log_startup_summary(self):
        """Logs a summary of the bot's configuration at startup."""
        self.logger.info("--- Configuration Summary ---")
        self.logger.info(f"Bybit Testnet: {self.config.bybit.testnet}")
        self.logger.info(f"Bybit Margin Mode: {self.config.bybit.margin_mode}")
        self.logger.info(f"Bybit Position Mode: {self.config.bybit.position_mode}")
        self.logger.info(f"Enforce Leverage: {self.config.bybit.enforce_leverage}")
        self.logger.info(f"Default Leverage: {self.config.trading.leverage}x")
        self.logger.info(f"Risk Per Trade: {self.config.risk_management.risk_per_trade_percentage}%")
        self.logger.info(f"Max Total Exposure: {self.config.risk_management.max_total_exposure_percentage}%")
        self.logger.info(f"ATR Trailing Stop Multiplier: {self.config.risk_management.atr_trailing_stop_multiplier}")
        self.logger.info(f"Entry Price Tolerance: {self.config.risk_management.entry_price_tolerance_percentage}%")
        self.logger.info(f"Signal Expiration: {self.config.risk_management.signal_expiration_days} days")
        self.logger.info(f"Telegram Channel: {self.config.telegram.channel_identifier}")
        self.logger.info("-----------------------------")
 
    async def _run_health_checks(self):
        """Periodically checks connection health."""
        while self.running:
            await asyncio.sleep(600) # Check every 10 minutes
            self.logger.info("Performing periodic health check...")
            if not self.bybit_handler.is_connected:
                self.logger.warning("Bybit connection lost. Attempting to reconnect...")
                if not await self.bybit_handler.connect():
                    self.logger.error("Failed to re-establish Bybit connection.")
            # No WebSocket for Telegram, so no is_connected check needed
            
            self.logger.info(f"Bot running normally – active positions: {len(self.bybit_handler.tracked_positions)}")

async def main():
    """Main entry point"""
    bot = TradingBot()
    success = await bot.start()
    
    if not success:
        sys.exit(1)
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)