#!/usr/bin/env python3
"""Find the real API key for CLOB V2."""
import os, json, sys

def load_env():
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    env = {}
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k,v = line.split("=",1)
                    env[k.strip()] = v.strip()
    return env

env = load_env()
PK = env.get("POLYMARKET_PRIVATE_KEY","")
sys.path.insert(0, os.path.expanduser("~/polymarket/venv/lib/python3.12/site-packages"))

from web3 import Web3
from py_clob_client_v2 import ClobClient, ApiCreds, OrderArgs, OrderType
from py_clob_client_v2.constants import POLYGON

eoa = Web3().eth.account.from_key(PK).address
print(f"🔑 EOA: {eoa}")

# Test 1: derive_api_key() — this is the SDK way to get a regular API key
print("\n--- Test 1: derive_api_key() ---")
client = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON, key=PK)
try:
    creds = client.derive_api_key()
    print(f"✅ derive_api_key SUCCESS")
    print(f"   API Key:    {creds.api_key}")
    print(f"   API Secret: {creds.api_secret[:20]}...")
    print(f"   Passphrase: {creds.api_passphrase[:10]}...")
    print(f"\n   Update .env with these values!")
except Exception as e:
    print(f"❌ derive_api_key FAILED: {e}")

# Test 2: Use the manual API key from .env with set_api_creds
print("\n--- Test 2: Manual API key + set_api_creds() ---")
AK = env.get("POLYMARKET_API_KEY","")
ASecret = env.get("POLYMARKET_API_SECRET","")
APass = env.get("POLYMARKET_API_PASSPHRASE","")
if AK and ASecret:
    creds = ApiCreds(api_key=AK, api_secret=ASecret, api_passphrase=APass)
    client2 = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON, key=PK)
    client2.set_api_creds(creds)

    # Try balance first
    print("   Checking balance...")
    try:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        bal = client2.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        print(f"   ✅ Balance: {json.dumps(bal, indent=2)[:200]}")
    except Exception as e:
        err = str(e)
        print(f"   Balance err: {err[:200]}")
        if "signer" in err:
            print("   → Manual API key invalid — signer mismatch")
        elif "Invalid asset" in err:
            print("   → Asset type error — this is an SDK quirk")
        
    # Try order
    import urllib.request
    try:
        req = urllib.request.Request(
            "https://gamma-api.polymarket.com/events?slug=btc-updown-5m",
            headers={"User-Agent":"Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            events = json.loads(r.read())
        if events and events[0].get("markets"):
            token = events[0]["markets"][0]["clobTokenIds"][0]
            print(f"   Test token: {token[:15]}...")
            try:
                order_args = OrderArgs(token_id=token, price=0.50, size=1.0, side="BUY")
                resp = client2.create_and_post_order(order_args, order_type=OrderType.FOK)
                print(f"   ✅ Order: {resp}")
            except Exception as oe:
                print(f"   Order err: {str(oe)[:250]}")
    except Exception as fe:
        print(f"   Gamma API err: {fe}")

# Test 3: Create a fresh API key programmatically
print("\n--- Test 3: create_api_key() ---")
client3 = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON, key=PK)
try:
    creds3 = client3.create_api_key()
    print(f"✅ create_api_key SUCCESS")
    print(f"   API Key: {creds3.api_key[:20]}...")
except Exception as e:
    print(f"   Result: {str(e)[:200]}")
    if "Could not create" in str(e):
        print("   → Wallet needs manual deposit flow on polymarket.com first")
