#!/usr/bin/env python3
"""
Bybit API Key Testing Script
Tests API keys and displays account balance for testnet or mainnet
"""

from pybit.unified_trading import HTTP
import sys

# ============================================================================
# CONFIGURATION - Set your API credentials here
# ============================================================================
API_KEY = "0xdNiMagbKUUFqcCp1"
API_SECRET = "tRcgIZghy5LLU3TScpPzyflyhDGi0jJvQ2DW"
TESTNET = False  # Set to False for mainnet, True for testnet
# ============================================================================

def test_api_keys(api_key, api_secret, testnet=True):
    """
    Test Bybit API keys and retrieve account balance
    
    Args:
        api_key (str): Your Bybit API key
        api_secret (str): Your Bybit API secret
        testnet (bool): True for testnet, False for mainnet
    
    Returns:
        dict: Account balance information or error details
    """
    try:
        # Initialize the client
        network = "testnet" if testnet else "mainnet"
        print(f"\n{'='*60}")
        print(f"Testing API keys on {network.upper()}...")
        print(f"{'='*60}\n")
        
        session = HTTP(
            testnet=testnet,
            api_key=api_key,
            api_secret=api_secret,
        )
        
        # Test connection by getting wallet balance
        response = session.get_wallet_balance(accountType="UNIFIED")
        
        if response['retCode'] == 0:
            print("✓ API Connection Successful!\n")
            
            # Extract balance information
            balance_info = response['result']
            
            if 'list' in balance_info and len(balance_info['list']) > 0:
                account = balance_info['list'][0]
                
                print(f"Account Type: {account.get('accountType', 'N/A')}")
                print(f"Total Equity: {account.get('totalEquity', '0')} USD")
                print(f"Total Wallet Balance: {account.get('totalWalletBalance', '0')} USD")
                print(f"Total Available Balance: {account.get('totalAvailableBalance', '0')} USD")
                print(f"Total Margin Balance: {account.get('totalMarginBalance', '0')} USD")
                print(f"Total Initial Margin: {account.get('totalInitialMargin', '0')} USD")
                print(f"Total Maintenance Margin: {account.get('totalMaintenanceMargin', '0')} USD")
                
                # Display individual coin balances
                if 'coin' in account and len(account['coin']) > 0:
                    print(f"\n{'─'*60}")
                    print("Individual Coin Balances:")
                    print(f"{'─'*60}")
                    
                    for coin in account['coin']:
                        wallet_balance = float(coin.get('walletBalance', 0))
                        if wallet_balance > 0:  # Only show coins with balance
                            print(f"\n  Coin: {coin.get('coin', 'N/A')}")
                            print(f"  Wallet Balance: {wallet_balance}")
                            print(f"  Available Balance: {coin.get('availableToWithdraw', '0')}")
                            print(f"  Equity: {coin.get('equity', '0')}")
                            print(f"  USD Value: {coin.get('usdValue', '0')}")
                else:
                    print("\nNo coin balances found.")
            else:
                print("No account information found.")
            
            return response
        else:
            print(f"✗ API Error: {response['retMsg']}")
            print(f"Error Code: {response['retCode']}")
            return response
            
    except Exception as e:
        print(f"✗ Connection Failed!")
        print(f"Error: {str(e)}\n")
        print("Common issues:")
        print("  - Invalid API key or secret")
        print("  - API key doesn't have required permissions")
        print("  - Using mainnet keys on testnet (or vice versa)")
        print("  - IP address not whitelisted (if IP restriction enabled)")
        return None

def main():
    """Main function to run the API key tester"""
    
    print("\n" + "="*60)
    print("BYBIT API KEY TESTER")
    print("="*60)
    
    # Use the configured credentials
    api_key = API_KEY
    api_secret = API_SECRET
    testnet = TESTNET
    
    # Validate that credentials have been set
    if api_key == "your_api_key_here" or api_secret == "your_api_secret_here":
        print("\n⚠ WARNING: Please set your API_KEY and API_SECRET in the script!")
        print("Edit the configuration section at the top of the script.\n")
        sys.exit(1)
    
    # Test the API keys
    result = test_api_keys(api_key, api_secret, testnet)
    
    print(f"\n{'='*60}")
    if result and result.get('retCode') == 0:
        print("STATUS: API keys are valid and working! ✓")
    else:
        print("STATUS: API key test failed. ✗")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nScript interrupted by user.")
        sys.exit(0)