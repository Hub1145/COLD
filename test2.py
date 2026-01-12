#!/usr/bin/env python3
"""
Bybit WebSocket Test Script
Tests WebSocket connection for BTCUSDT 15-minute kline data
"""

from pybit.unified_trading import WebSocket
from time import sleep
import sys
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================
SYMBOL = "BTCUSDT"
INTERVAL = "15"  # 15-minute timeframe
TESTNET = False  # Set to True for testnet, False for mainnet
# ============================================================================

def handle_kline(message):
    """
    Handle incoming kline (candlestick) data
    
    Args:
        message (dict): WebSocket message containing kline data
    """
    try:
        if message.get('topic'):
            print(f"\n{'='*80}")
            print(f"📊 KLINE DATA RECEIVED - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*80}")
            
            # Extract symbol from topic (format: kline.15.BTCUSDT)
            topic = message.get('topic', '')
            symbol = topic.split('.')[-1] if '.' in topic else 'UNKNOWN'
            
            # Extract kline data
            data = message.get('data', [])
            
            for kline in data:
                # Parse timestamp
                start_time = datetime.fromtimestamp(int(kline['start']) / 1000)
                end_time = datetime.fromtimestamp(int(kline['end']) / 1000)
                
                print(f"\n📈 Symbol: {symbol}")
                print(f"⏰ Interval: {kline['interval']} minutes")
                print(f"🕐 Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"🕑 End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"📋 Type: {message.get('type', 'update')}")
                print(f"\n💰 Price Information:")
                print(f"   Open:    ${float(kline['open']):,.2f}")
                print(f"   High:    ${float(kline['high']):,.2f}")
                print(f"   Low:     ${float(kline['low']):,.2f}")
                print(f"   Close:   ${float(kline['close']):,.2f}")
                print(f"\n📊 Volume: {float(kline['volume']):,.4f}")
                print(f"💵 Turnover: ${float(kline['turnover']):,.2f}")
                print(f"🔄 Confirmed: {'Yes ✅' if kline['confirm'] else 'No (Still forming) ⏳'}")
                
                # Calculate price change
                price_change = float(kline['close']) - float(kline['open'])
                price_change_pct = (price_change / float(kline['open'])) * 100
                
                change_emoji = "📈" if price_change > 0 else "📉" if price_change < 0 else "➡️"
                print(f"\n{change_emoji} Change: ${price_change:,.2f} ({price_change_pct:+.2f}%)")
                
            print(f"\n{'='*80}\n")
                
    except Exception as e:
        print(f"❌ Error processing message: {e}")
        print(f"Raw message: {message}")

def handle_error(message):
    """Handle WebSocket errors"""
    print(f"\n❌ ERROR: {message}\n")

def test_websocket():
    """Test WebSocket connection for BTCUSDT kline data"""
    
    print("\n" + "="*80)
    print("BYBIT WEBSOCKET TESTER - KLINE DATA")
    print("="*80)
    print(f"\n📡 Configuration:")
    print(f"   Symbol: {SYMBOL}")
    print(f"   Timeframe: {INTERVAL} minutes")
    print(f"   Network: {'TESTNET' if TESTNET else 'MAINNET'}")
    print("\n" + "="*80)
    
    try:
        # Initialize WebSocket
        print("\n🔌 Connecting to WebSocket...")
        
        ws = WebSocket(
            testnet=TESTNET,
            channel_type="linear",  # For USDT perpetual contracts
        )
        
        print("✅ WebSocket connection established!")
        
        # Subscribe to kline stream
        topic = f"kline.{INTERVAL}.{SYMBOL}"
        print(f"\n📥 Subscribing to: {topic}")
        
        ws.kline_stream(
            interval=INTERVAL,
            symbol=SYMBOL,
            callback=handle_kline
        )
        
        print("✅ Subscription successful!")
        print("\n" + "="*80)
        print("🎧 Listening for kline updates...")
        print("Press Ctrl+C to stop")
        print("="*80 + "\n")
        
        # Keep the script running
        while True:
            sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n⏹️  Stopping WebSocket connection...")
        print("👋 Goodbye!\n")
        sys.exit(0)
        
    except Exception as e:
        print(f"\n❌ WebSocket Error: {e}")
        print("\n🔍 Common issues:")
        print("   - Check your internet connection")
        print("   - Verify the symbol is correct (BTCUSDT)")
        print("   - Ensure pybit is properly installed")
        print("   - Try switching between testnet/mainnet")
        sys.exit(1)

if __name__ == "__main__":
    test_websocket()