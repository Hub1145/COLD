"""
Configuration management for the Bybit Telegram Trading Bot
"""

import json
import os
from dataclasses import dataclass
import logging
from typing import Optional


@dataclass
class BybitConfig:
    """Bybit configuration"""
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True # True for demo, False for live
    margin_mode: str = "Cross" # "Isolated" or "Cross"
    position_mode: str = "MergedSingle" # "MergedSingle" or "BothSides"
    enforce_leverage: bool = True # If true, bot will enforce leverage setting

@dataclass
class TelegramConfig:
    """Telegram configuration"""
    api_id: Optional[int] = None
    api_hash: str = ""
    channel_identifier: str = ""
    session_name: str = "bybit_bot_session"

@dataclass
class TradingConfig:
    """Trading configuration"""
    leverage: float = 12.5 # Default leverage
    timeframe: str = "15m" # Default timeframe for indicators
    symbols_allowlist: Optional[list[str]] = None # List of allowed symbols, None for all
    entry_tolerance_atr: float = 0.5 # ATR multiplier for entry tolerance when price passed
    use_conditional_market_entry: bool = True # Use conditional market order for entry
    kline_refresh_interval_seconds: int = 60 # New: Interval to refresh kline data for indicators

@dataclass
class IndicatorConfig:
    """Technical Indicator configuration"""
    ema_50: int = 50
    ema_200: int = 200
    rsi_length: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_length: int = 14
    bollinger_bands_length: int = 20
    bollinger_bands_std: float = 2.0

@dataclass
class SignalConfig:
    """Signal confirmation rules"""
    rsi_long_threshold: float = 40.0
    rsi_short_threshold: float = 60.0
    bollinger_band_confirmation: bool = True # Use BB for optional confirmation

@dataclass
class RiskManagementConfig:
    """Risk management configuration"""
    risk_per_trade_percentage: float = 2.5 # 2.5% of account equity
    atr_trailing_stop_multiplier: float = 1.0
    max_total_exposure_percentage: float = 6.0 # Max 3 concurrent trades (2.5% each)
    per_pair_cooldown_minutes: int = 10
    daily_drawdown_stop_percentage: float = 6.0
    entry_price_tolerance_percentage: float = 0.6 # New: 0.6% tolerance for entry price
    signal_expiration_days: int = 9 # New: Signals expire after 9 days
    fail_fast_on_missing_instrument_metadata: bool = True # If true, bot will not trade if instrument metadata is missing

@dataclass
class LoggingConfig:
    """Logging configuration"""
    log_level: str = "INFO"
    log_file: str = "bybit_bot.log"
    sanitize_secrets: bool = True # Mask API keys in logs

@dataclass
class WebSocketConfig:
    """WebSocket configuration for reliability"""
    heartbeat_seconds: int = 30
    reconnect_backoff_seconds: int = 5
    max_reconnect_attempts: int = 5
    websocket_watchdog_seconds: int = 60 # New: Max seconds without WS message before force reconnect
    # Add other WebSocket related configs here

class Config:
    """Main configuration class"""
    
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.logger = logging.getLogger(__name__)
        self._load_config()
        
    def _load_config(self):
        """Load configuration from JSON file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config_data = json.load(f)
                    
                self.bybit = BybitConfig(**config_data.get('bybit', {}))
                self.telegram = TelegramConfig(**config_data.get('telegram', {}))
                self.trading = TradingConfig(**config_data.get('trading', {}))
                self.indicators = IndicatorConfig(**config_data.get('indicators', {}))
                self.signals = SignalConfig(**config_data.get('signals', {}))
                self.risk_management = RiskManagementConfig(**config_data.get('risk_management', {}))
                self.logging = LoggingConfig(**config_data.get('logging', {}))
                self.websocket = WebSocketConfig(**config_data.get('websocket', {}))
                
                self.logger.info(f"Configuration loaded from {self.config_file}")
            else:
                self._create_default_config()
                self.logger.warning(f"Created default config file: {self.config_file}")
                
        except json.JSONDecodeError:
            self.logger.error(f"Error: config.json is not a valid JSON file. Creating default config.")
            self._create_default_config()
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            self._create_default_config()
    
    def _create_default_config(self):
        """Create default configuration"""
        self.bybit = BybitConfig(
            testnet=True # Default to testnet
        )
        self.telegram = TelegramConfig()
        self.trading = TradingConfig(
            leverage=12.5, # Default leverage
            entry_tolerance_atr=0.5,
            use_conditional_market_entry=True
        )
        self.indicators = IndicatorConfig()
        self.signals = SignalConfig()
        self.risk_management = RiskManagementConfig(
            risk_per_trade_percentage=2.5, # Default risk
            atr_trailing_stop_multiplier=1.0,
            max_total_exposure_percentage=6.0,
            per_pair_cooldown_minutes=10,
            daily_drawdown_stop_percentage=6.0,
            entry_price_tolerance_percentage=0.6,
            signal_expiration_days=9,
            fail_fast_on_missing_instrument_metadata=True
        )
        self.logging = LoggingConfig(
            sanitize_secrets=True
        )
        self.websocket = WebSocketConfig()
        self.save_config()
    
    def save_config(self):
        """Save configuration to JSON file"""
        try:
            config_data = {
                'bybit': self.bybit.__dict__,
                'telegram': self.telegram.__dict__,
                'trading': self.trading.__dict__,
                'indicators': self.indicators.__dict__,
                'signals': self.signals.__dict__,
                'risk_management': self.risk_management.__dict__,
                'logging': self.logging.__dict__,
                'websocket': self.websocket.__dict__,
            }
            
            # Sanitize sensitive data before saving to file for security
            if self.logging.sanitize_secrets:
                bybit_data = self.bybit.__dict__.copy()
                bybit_data['api_key'] = '***'
                bybit_data['api_secret'] = '***'
                config_data['bybit'] = bybit_data

                telegram_data = self.telegram.__dict__.copy()
                telegram_data['api_hash'] = '***'
                config_data['telegram'] = telegram_data

            with open(self.config_file, 'w') as f:
                json.dump(config_data, f, indent=2)
                
            self.logger.info(f"Configuration saved to {self.config_file}")
            
        except Exception as e:
            self.logger.error(f"Error saving configuration: {e}")
    
    def reload(self):
        """Reload configuration from file."""
        self.logger.info("Reloading configuration...")
        self._load_config()
        self.validate()

    def validate(self) -> bool:
        """Validate configuration"""
        errors = []
        
        # Check Telegram configuration
        if self.telegram.api_id is None:
            errors.append("Telegram API ID is required")
        if not self.telegram.api_hash:
            errors.append("Telegram API Hash is required")
        if not self.telegram.channel_identifier:
            errors.append("Telegram channel identifier is required")
        
        # Check Bybit configuration
        if not self.bybit.api_key:
            errors.append("Bybit API Key is required")
        if not self.bybit.api_secret:
            errors.append("Bybit API Secret is required")
        
        # Validate leverage
        if not (0 < self.trading.leverage <= 100):
            errors.append("Leverage must be between 0 and 100.")
        
        # Validate risk percentage
        if not (0 < self.risk_management.risk_per_trade_percentage <= 5):
            errors.append("Risk per trade percentage must be between 0 and 5%.")

        # Validate ATR trailing stop multiplier
        if not (0.5 <= self.risk_management.atr_trailing_stop_multiplier <= 5):
            errors.append("ATR trailing stop multiplier must be between 0.5 and 5.")

        # Log errors
        for error in errors:
            self.logger.error(f"Configuration error: {error}")
        
        return len(errors) == 0

def interactive_setup():
    """Interactive setup function for first-time configuration"""
    try:
        print("="*60)
        print("    BYBIT TELEGRAM TRADING BOT SETUP")
        print("="*60)
        print()
        
        config_data = {}
        
        # Telegram configuration
        print("\n=== TELEGRAM CONFIGURATION ===")
        config_data['telegram'] = {
            'api_id': int(input("Enter your Telegram API ID: ").strip()),
            'api_hash': input("Enter your Telegram API Hash: ").strip(),
            'channel_identifier': input("Channel Username or Title (e.g., @channel_name or 'My Private Group'): ") or "",
            'session_name': 'bybit_bot_session'
        }

        # Bybit configuration
        print("\n=== BYBIT CONFIGURATION ===")
        config_data['bybit'] = {
            'api_key': input("Enter your Bybit API Key: ").strip(),
            'api_secret': input("Enter your Bybit API Secret: ").strip(),
            'testnet': input("Use Bybit Testnet? (y/n) [y]: ").strip().lower() != 'n',
            'margin_mode': input("Margin Mode (Cross/Isolated) [Cross]: ").strip() or "Cross",
            'position_mode': input("Position Mode (MergedSingle/BothSides) [MergedSingle]: ").strip() or "MergedSingle",
            'enforce_leverage': input("Enforce leverage setting (y/n) [y]: ").strip().lower() != 'n'
        }

        
        # Trading configuration
        print("\n=== TRADING CONFIGURATION ===")
        config_data['trading'] = {
            'leverage': float(input("Default leverage to use (e.g., 12.5 for 12.5x) [12.5]: ") or "12.5"),
            'timeframe': input("Default timeframe for indicators (e.g., 15m) [15m]: ") or "15m",
            'symbols_allowlist': [s.strip() for s in input("Comma-separated list of allowed symbols (e.g., BTCUSDT,ETHUSDT), leave empty for all: ").split(',')] if input("Specify allowed symbols? (y/n) [n]: ").strip().lower() == 'y' else None,
            'entry_tolerance_atr': float(input("ATR multiplier for entry tolerance when price passed (e.g., 0.5): ") or "0.5"),
            'use_conditional_market_entry': input("Use conditional MARKET order for entry if price not reached (y/n) [y]: ").strip().lower() != 'n'
        }

        # Indicator configuration
        print("\n=== INDICATOR CONFIGURATION ===")
        config_data['indicators'] = {
            'ema_50': int(input("EMA 50 length: ") or "50"),
            'ema_200': int(input("EMA 200 length: ") or "200"),
            'rsi_length': int(input("RSI length: ") or "14"),
            'macd_fast': int(input("MACD fast length: ") or "12"),
            'macd_slow': int(input("MACD slow length: ") or "26"),
            'macd_signal': int(input("MACD signal length: ") or "9"),
            'atr_length': int(input("ATR length: ") or "14"),
            'bollinger_bands_length': int(input("Bollinger Bands length: ") or "20"),
            'bollinger_bands_std': float(input("Bollinger Bands standard deviation: ") or "2.0")
        }

        # Signal configuration
        print("\n=== SIGNAL CONFIGURATION ===")
        config_data['signals'] = {
            'rsi_long_threshold': float(input("RSI Long Threshold (e.g., 40.0): ") or "40.0"),
            'rsi_short_threshold': float(input("RSI Short Threshold (e.g., 60.0): ") or "60.0"),
            'bollinger_band_confirmation': input("Use Bollinger Bands for optional confirmation? (y/n) [y]: ").strip().lower() != 'n'
        }

        # Risk Management configuration
        print("\n=== RISK MANAGEMENT CONFIGURATION ===")
        config_data['risk_management'] = {
            'risk_per_trade_percentage': float(input("Risk per trade as percentage of equity (e.g., 2.5 for 2.5%): ") or "2.5"),
            'atr_trailing_stop_multiplier': float(input("ATR Trailing Stop Multiplier (e.g., 1.0): ") or "1.0"),
            'max_total_exposure_percentage': float(input("Maximum total exposure as percentage of equity (e.g., 6.0 for 6%): ") or "6.0"),
            'per_pair_cooldown_minutes': int(input("Per-pair cooldown in minutes: ") or "10"),
            'daily_drawdown_stop_percentage': float(input("Daily drawdown stop as percentage of equity (e.g., 6.0 for 6%): ") or "6.0"),
            'entry_price_tolerance_percentage': float(input("Entry price tolerance percentage (e.g., 0.6 for 0.6%): ") or "0.6"),
            'signal_expiration_days': int(input("Signal expiration days (e.g., 9 for 9 days): ") or "9"),
            'fail_fast_on_missing_instrument_metadata': input("Fail fast on missing instrument metadata? (y/n) [y]: ").strip().lower() != 'n'
        }

        # WebSocket configuration
        print("\n=== WEBSOCKET CONFIGURATION ===")
        config_data['websocket'] = {
            'heartbeat_seconds': int(input("WebSocket heartbeat interval in seconds [30]: ") or "30"),
            'reconnect_backoff_seconds': int(input("WebSocket reconnect backoff in seconds [5]: ") or "5"),
            'max_reconnect_attempts': int(input("WebSocket max reconnect attempts [5]: ") or "5")
        }
        
        # Logging configuration
        print("\n=== LOGGING CONFIGURATION ===")
        config_data['logging'] = {
            'log_level': input("Log Level (e.g., INFO, DEBUG): ") or "INFO",
            'log_file': input("Log File Name (e.g., bybit_bot.log): ") or "bybit_bot.log",
            'sanitize_secrets': input("Sanitize API keys and secrets in logs? (y/n) [y]: ").strip().lower() != 'n'
        }
        
        # Save configuration
        config_file_path = 'config.json'
        if os.path.exists(config_file_path):
            confirm = input(f"config.json already exists. Overwrite? (y/n) [n]: ").strip().lower()
            if confirm != 'y':
                print("Setup cancelled. Existing config.json retained.")
                return

        with open(config_file_path, 'w') as f:
            json.dump(config_data, f, indent=2)
        
        print("\n✅ Configuration saved to config.json")
        print("Now run: python main.py")
        
    except KeyboardInterrupt:
        print("\nSetup cancelled")
    except Exception as e:
        print(f"Setup error: {e}")

if __name__ == "__main__":
    interactive_setup()