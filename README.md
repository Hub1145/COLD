# Bybit Telegram Trading Bot

This bot integrates with Telegram to receive trading signals and automatically executes trades on Bybit Futures. It features advanced risk management, dynamic stop-loss adjustments based on Take Profit levels, conditional ATR-based trailing stop for "To the moon" signals, **persistent signal monitoring across restarts, and real-time indicator recalculation using WebSocket data.**

## Features

*   **Telegram Signal Integration**: Parses trading signals from a specified Telegram channel.
*   **Bybit Futures Trading**: Executes market orders on Bybit Unified Trading Account (Linear Futures).
*   **Technical Indicator Confirmation**: Uses EMA, RSI, MACD, ATR, and Bollinger Bands to confirm signal validity before execution. The bot will wait and re-check indicators until they align with the signal's direction. **Indicators are now recalculated in real-time using live WebSocket price data.**
*   **Dynamic Stop-Loss Management**:
    *   Initial Stop-Loss (SL) is taken directly from the Telegram signal.
    *   As Take Profit (TP) levels from the signal are hit, the SL is adjusted to the entry price (after the first TP) or the previous TP level.
*   **Conditional Trailing Stop**: If the final TP in a signal is "To the moon", an ATR-based trailing stop is activated to maximize profits, tightening only when the price moves favorably.
*   **Risk Management**:
    *   Configurable risk per trade percentage (default 2.5% of current balance).
    *   Maximum total exposure limit to control concurrent trades.
    *   Per-pair cooldown to prevent overtrading.
    *   Daily drawdown stop to halt trading if losses exceed a threshold.
    *   **Entry price tolerance**: Trades are only placed if the current market price is within a configurable percentage of the signal's entry price.
    *   **Signal expiration**: Signals are ignored if they are older than a configurable number of days.
    *   Fail fast on missing instrument metadata.
*   **Persistent Signal Monitoring**: **The bot now saves pending signals to a JSON file and resumes monitoring them across restarts or disconnections. It also handles multiple signals concurrently.**
*   **Robust Error Handling**: Includes retry mechanisms with backoff for API calls and comprehensive logging.
*   **WebSocket Reliability**: Implements heartbeat, exponential reconnect, and re-subscribe flows for real-time price data. **Uses `pybit.unified_trading.WebSocket` for live ticker data.**
*   **Configurable**: All key parameters are configurable via `config.json` and loaded securely via `.env`.

## Setup

### Prerequisites

*   Python 3.9+
*   `pip` package installer

### Installation

1.  **Download the project files**:
    Obtain the project files (e.g., as a `.zip` archive) and extract them to your desired directory.
2.  **Install dependencies**:
    Navigate to the project directory in your terminal and install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```

### Configuration

The bot's configuration is managed through `config.json` and sensitive data is loaded from a `.env` file.

1.  **Create `.env` file**:
    Create a file named `.env` in the root directory and add your Bybit and Telegram API credentials:
    ```
    BYBIT_API_KEY="YOUR_BYBIT_API_KEY"
    BYBIT_API_SECRET="YOUR_BYBIT_API_SECRET"
    TELETHON_SESSION_STRING="YOUR_TELETHON_SESSION_STRING" # Generated after first run if not provided
    TELETHON_PHONE_NUMBER="YOUR_TELEGRAM_PHONE_NUMBER"     # Used for initial Telegram login
    ```
    *   `api_id` and `api_hash` for Telegram can be obtained from [my.telegram.org/apps](https://my.telegram.org/apps).
    *   `TELETHON_SESSION_STRING` will be generated on the first successful Telegram login if not already set.
    *   `TELETHON_PHONE_NUMBER` is required for the initial Telegram login.

2.  **Run interactive setup for `config.json`**:
    ```bash
    python config.py
    ```
    Follow the prompts to enter your Telegram details and desired trading parameters. This will create or update `config.json`.

    **Example `config.json` structure (default values):**
    ```json
    {
      "bybit": {
        "api_key": "YOUR_BYBIT_API_KEY",
        "api_secret": "YOUR_BYBIT_API_SECRET",
        "testnet": true,
        "margin_mode": "Cross",
        "position_mode": "MergedSingle",
        "enforce_leverage": true
      },
      "telegram": {
        "api_id": 1234567,
        "api_hash": "YOUR_TELEGRAM_API_HASH",
        "channel_identifier": "YOUR_CHANNEL_USERNAME_OR_TITLE",
        "session_name": "bybit_bot_session"
      },
      "trading": {
        "leverage": 12.5,
        "timeframe": "15m",
        "symbols_allowlist": null,
        "entry_tolerance_atr": 0.5,
        "use_conditional_market_entry": true
      },
      "indicators": {
        "ema_50": 50,
        "ema_200": 200,
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "bollinger_bands_length": 20,
        "bollinger_bands_std": 2.0
      },
      "signals": {
        "rsi_long_threshold": 40.0,
        "rsi_short_threshold": 60.0,
        "bollinger_band_confirmation": true
      },
      "risk_management": {
        "risk_per_trade_percentage": 2.5,
        "atr_trailing_stop_multiplier": 1.0,
        "max_total_exposure_percentage": 6.0,
        "per_pair_cooldown_minutes": 10,
        "daily_drawdown_stop_percentage": 6.0,
        "entry_price_tolerance_percentage": 0.6,
        "signal_expiration_days": 9,
        "fail_fast_on_missing_instrument_metadata": true
      },
      "logging": {
        "log_level": "INFO",
        "log_file": "bybit_bot.log",
        "sanitize_secrets": true
      },
      "websocket": {
        "heartbeat_seconds": 30,
        "reconnect_backoff_seconds": 5,
        "max_reconnect_attempts": 5
      }
    }
    ```

    **Important**:
    *   Set `testnet` to `true` for demo trading or `false` for live trading.
    *   `channel_identifier` can be the channel's username (e.g., `@my_trading_channel`) or its exact title if it's a private group.

## How to Run

After configuring `.env` and `config.json`, start the bot by running `main.py`:

```bash
python main.py
```

The bot will connect to Telegram and Bybit, perform initial health checks, and begin monitoring your specified Telegram channel for signals. **It will also load any pending signals from `pending_signals.json` and resume monitoring them.**

## Signal Format

The bot is designed to parse signals in a specific format. Here's an example:

```
🪙 XPL/USDT
Exchanges: BYBIT - link

🔴 SHORT
Cross (75X)

Entry Targets:
1.2592

🎯 TP:
1) 1.24661
2) 1.23402
3) 1.22142
4) 1.20883
5) 1.19624
6) 1.18365
7) To the moon 🌖

⛔️ SL:
1.38512
```

Key elements the bot extracts:
*   `🪙 SYMBOL/USDT`: The trading pair.
*   `🔴 SHORT` or `🟢 LONG`: The trade direction.
*   `Entry Targets: \nPRICE`: The entry price.
*   `🎯 TP: \n1) PRICE\n2) PRICE...`: A list of Take Profit levels. If the last TP is "To the moon 🌖", it triggers a special trailing stop logic.
*   `⛔️ SL: \nPRICE`: The initial Stop Loss price (supports `⛔ SL:` and `⛔️ SL:`).

## Trading Logic

### Signal Validation (Indicator-Gated Execution)

Upon receiving a signal, the bot continuously checks technical indicators until they confirm the signal's direction. **This validation now uses real-time price data from WebSocket and recalculates indicators dynamically.** The bot polls every 1-3 seconds with jitter and applies backoff on API errors. The bot will wait indefinitely (`max_indicator_wait_minutes = -1`) until conditions align.

*   **Mandatory Conditions**:
    *   **LONG**: Price must be above the 200 EMA, and there must be a MACD bullish crossover (MACD Line > Signal Line, Histogram > 0).
    *   **SHORT**: Price must be below the 200 EMA, and there must be a MACD bearish crossover (MACD Line < Signal Line, Histogram < 0).
*   **Optional Confirmations**: At least one of the following must also be true:
    *   **LONG**: 50 EMA > 200 EMA, RSI < `rsi_long_threshold`, or Close price above middle Bollinger Band.
    *   **SHORT**: 50 EMA < 200 EMA, RSI > `rsi_short_threshold`, or Close price below middle Bollinger Band.

A trade is only placed if all mandatory conditions and at least one optional condition are met.

### Orders & Entry Policy

*   **Default Entry**: By default, a conditional MARKET order is placed at the entry trigger price.
*   **Price Passed Tolerance**: **The bot now uses `entry_price_tolerance_percentage` from `config.json` to determine if the current market price is within an acceptable range of the signal's entry price.** If the price is within tolerance, a conditional MARKET order is placed. Otherwise, it continues to monitor.
*   All decisions regarding entry are logged.

### Stop-Loss and Take-Profit Management

*   **Initial Order**: A market order is placed with the initial SL and first TP level directly from the signal.
*   **Step-wise SL Adjustment**: As subsequent TP levels from the signal are hit, the Stop Loss is dynamically moved:
    *   After the first TP is hit, the SL is moved to the entry price.
    *   After each subsequent TP is hit, the SL is moved to the previous TP level.
*   **"To the moon" Trailing Stop**: If the final TP in the signal is "To the moon", once all numerical TPs have been hit, the bot activates an ATR-based trailing stop:
    *   The trailing stop price is set at a distance from the current price determined by the `atr_trailing_stop_multiplier` (configured in `config.json`).
    *   This trailing stop only moves in the direction favorable to the trade, locking in profits as the price continues to move.
    *   Updates to the trailing stop are retried if they fail on the exchange.
*   **Position Closure**:
    *   If the final numerical TP is hit (and it's not a "To the moon" signal), the entire position is closed.
    *   If the initial SL or the dynamically adjusted SL/trailing stop is hit, the entire position is closed.
    *   The bot verifies that the position is truly closed before removing it from tracking.

## Risk Management

The bot incorporates several risk management features:

*   **Leverage**: Uses configurable leverage from `config.json` (e.g., 12.5x Cross leverage and "MergedSingle" position mode). These settings are enforced and verified on startup.
*   **Risk Per Trade**: Defines the percentage of your account equity risked on a single trade (`risk_per_trade_percentage`, default 2.5%). Position quantity is calculated from the stop-loss distance and then snapped to Bybit's `qtyStep` and `minOrderQty` rules.
*   **Maximum Total Exposure**: Limits the total percentage of account equity that can be allocated across all open trades (`max_total_exposure_percentage`).
*   **Per-Pair Cooldown**: Prevents rapid re-entry into trades for the same symbol within a specified timeframe (`per_pair_cooldown_minutes`).
*   **Daily Drawdown Stop**: Halts new trade entries if the account's daily loss percentage (`daily_drawdown_stop_percentage`) is reached.
*   **Signal Expiration**: Signals are ignored if they are older than `signal_expiration_days` (configured in `config.json`). **Expired signals are automatically removed from monitoring.**
*   **Missing Instrument Metadata**: The bot will fail fast (not trade) if critical instrument metadata (like `tickSize`, `qtyStep`, `minOrderQty`) is missing, and will retry metadata fetching.

## WebSocket Reliability

The bot uses the ticker WebSocket stream as the canonical price source for decision-making. It implements:
*   Heartbeat mechanisms (`heartbeat_seconds`).
*   Exponential backoff for reconnect attempts (`reconnect_backoff_seconds`, `max_reconnect_attempts`).
*   Subscription ACK (acknowledgment) tracking to ensure successful subscriptions.
*   **Real-time price updates are used to continuously re-evaluate entry conditions and indicators.**

## Logging & Testing

The bot provides comprehensive logging to `bybit_bot.log` and a separate `trades_YYYY-MM-DD.log` for executed trades, helping you monitor its activity and performance. Sensitive information like API keys is sanitized from logs.

It is recommended to test the bot thoroughly on Bybit testnet to verify:
*   Quantity snapping across different symbols.
*   Conditional trigger behavior when the price is passed.
*   Leverage and margin mode read-back and enforcement.
*   TP/SL placement and amendments.
*   ATR trailing stop functionality.
*   WebSocket reconnect and re-subscribe behavior.
*   **Persistent signal monitoring and concurrent signal handling.**
*   **Correct application of entry price tolerance and signal expiration.**

## Health Checks & Graceful Shutdown

*   **Startup Checks**: Validates configuration, Bybit server time, and account balance before starting.
*   **Periodic Health Checks**: Runs every 10 minutes to confirm Bybit and Telegram connections are alive, attempting reconnection if necessary.
*   **Graceful Shutdown**: On `SIGINT` or `SIGTERM` (e.g., Ctrl+C), all active tasks are cancelled, and connections to Bybit and Telegram are closed cleanly. **Pending signals are saved to `pending_signals.json` on shutdown and reloaded on startup.**