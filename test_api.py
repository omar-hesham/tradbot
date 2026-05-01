#!/usr/bin/env python3
"""
Test script to verify API endpoints are working correctly
"""
import requests
import json
import sys

BASE_URL = "http://127.0.0.1:8000"

def test_endpoint(endpoint, description):
    """Test an API endpoint and return success/failure"""
    try:
        response = requests.get(f"{BASE_URL}{endpoint}", timeout=5)
        if response.status_code == 200:
            print(f"[PASS] {endpoint} - {description}")
            return True
        else:
            print(f"[FAIL] {endpoint} - {description} (Status: {response.status_code})")
            return False
    except Exception as e:
        print(f"[ERROR] {endpoint} - {description} (Error: {str(e)})")
        return False

def main():
    print("Testing API endpoints...")
    print("=" * 50)
    
    # Test basic connectivity
    success = test_endpoint("/", "Root endpoint")
    
    # Test market routes
    success &= test_endpoint("/api/market/symbols", "Market symbols")
    success &= test_endpoint("/api/market/stats/BTCUSDT", "BTCUSDT 24h stats")
    success &= test_endpoint("/api/market/orderbook/BTCUSDT", "BTCUSDT order book")
    
    # Test trading routes
    success &= test_endpoint("/api/trading/wallet/spot", "Spot wallet")
    success &= test_endpoint("/api/trading/wallet/funding", "Funding wallet")
    
    # Test settings routes
    success &= test_endpoint("/api/settings/ollama/models", "Ollama models")
    success &= test_endpoint("/api/settings/providers", "Available providers")
    
    print("=" * 50)
    if success:
        print("All tests passed!")
        return 0
    else:
        print("Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())