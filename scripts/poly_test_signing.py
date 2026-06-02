#!/usr/bin/env python3
"""
Polymarket CLOB Signing Test — BOT B Live Prep

Tests API key generation, market token fetching, and order signing
WITHOUT placing real orders (dry-run mode).

Usage:
  export POLYMARKET_PRIVATE_KEY="0x..."
  python3 poly_test_signing.py

Requirements:
  pip install py-clob-client web3
"""

import os
import sys
import json

# ---------------------------------------------------------------------------
# ENV CHECK
# ---------------------------------------------------------------------------

PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
if not PRIVATE_KEY:
    print("")
    print("❌ FATAL: POLYMARKET_PRIVATE_KEY not set!")
    print("   Set it: export POLYMARKET_PRIVATE_KEY='0xYOUR_KEY'")
    print("")
    sys.exit(1)

if not PRIVATE_KEY.startswith("0x"):
    print("⚠️  Adding 0x prefix to private key...")
    PRIVATE_KEY = "0x" + PRIVATE_KEY

# ---------------------------------------------------------------------------
# TEST 1: Import & API Key Generation
# ---------------------------------------------------------------------------

def test_api_key():
    print("\n" + "="*55)
    print("🔐 TEST 1: API Key Generation")
    print("="*55)

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON

        print("🔍 Initializing ClobClient...")
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=POLYGON,
            key=PRIVATE_KEY,
        )

        print("🔑 Generating API key...")
        api_creds = client.create_api_key()

        print("✅ API Key Generated!")
        print(f"   API Key:     {api_creds.api_key[:20]}...")
        print(f"   Secret:      {api_creds.api_secret[:20]}...")
        print(f"   Passphrase:  {api_creds.api_passphrase[:10]}...")
        return client, api_creds

    except ImportError:
        print("❌ py-clob-client not installed!")
        print("   Install: pip install py-clob-client")
        return None, None
    except Exception as e:
        print(f"❌ API key generation failed: {e}")
        return None, None


# ---------------------------------------------------------------------------
# TEST 2: Market Token IDs
# ---------------------------------------------------------------------------

def test_market_tokens(client):
    print("\n" + "="*55)
    print("🔍 TEST 2: Fetch BTC 5m Market Tokens")
    print("="*55)

    try:
        import urllib.request
        GAMMA_HOST = "https://gamma-api.polymarket.com"

        url = f"{GAMMA_HOST}/events?active=true&closed=false&limit=50"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            events = json.loads(resp.read())

        found = False
        for ev in events:
            title = ev.get("title", "").lower()
            if "btc" in title and ("up or down" in title or "updown" in title or "5m" in title or "5 minute" in title):
                print(f"\n🎯 Found Event: {ev.get('title')}")
                markets = ev.get("markets", [])
                for m in markets:
                    q = m.get("question", "").lower()
                    clob_raw = m.get("clobTokenIds", "[]")
                    try:
                        clob_ids = json.loads(clob_raw)
                        if len(clob_ids) >= 2:
                            print(f"   Market: {m.get('question')}")
                            print(f"   YES token: {clob_ids[0][:30]}...")
                            print(f"   NO token:  {clob_ids[1][:30]}...")
                            found = True
                            return clob_ids[0], clob_ids[1]
                    except:
                        continue

        if not found:
            print("⚠️  BTC 5m market not found. Events may have changed.")
            print("   Check manually at polymarket.com")
        return None, None

    except Exception as e:
        print(f"❌ Market fetch failed: {e}")
        return None, None


# ---------------------------------------------------------------------------
# TEST 3: Order Signing (Dry-Run)
# ---------------------------------------------------------------------------

def test_order_signing(client, api_creds, yes_token, no_token):
    print("\n" + "="*55)
    print("✅ TEST 3: Order Signing (DRY-RUN)")
    print("="*55)
    print("⚠️  This test creates a signed order but does NOT submit it.")
    print("   No money will be spent.")
    print()

    try:
        from py_clob_client.order_builder.constants import BUY
        from py_clob_client.clob_types import ApiCreds, OrderArgs

        # Set API key on client
        client.set_api_creds(ApiCreds(
            api_key=api_creds.api_key,
            api_secret=api_creds.api_secret,
            api_passphrase=api_creds.api_passphrase,
        ))

        # Build a tiny order (0.1 share at 0.50)
        # This is a DRY RUN — not submitted
        order_args = OrderArgs(
            token_id=yes_token,
            price=0.50,
            size=0.1,
            side=BUY,
        )

        print("📝 Building order...")
        print(f"   Token: {yes_token[:30]}...")
        print(f"   Side:  BUY")
        print(f"   Price: 0.50")
        print(f"   Size:  0.1")
        print()

        signed_order = client.create_order(order_args)

        print("✅ Order signed successfully!")
        print(f"   Order ID: {signed_order.id[:30]}...")
        print(f"   Signature: {signed_order.signature[:40]}...")
        print()
        print("🚫 Order NOT submitted (dry-run).")
        print("   To submit for real, use: client.post_order(signed_order)")

        return True

    except Exception as e:
        print(f"❌ Order signing failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# TEST 4: Balance Check
# ---------------------------------------------------------------------------

def test_balance(client):
    print("\n" + "="*55)
    print("💰 TEST 4: Wallet Balance Check")
    print("="*55)

    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))

        from eth_account import Account
        acct = Account.from_key(PRIVATE_KEY)
        address = acct.address

        print(f"📌 Wallet Address: {address}")

        # MATIC balance
        matic = w3.eth.get_balance(address)
        print(f"   MATIC: {w3.from_wei(matic, 'ether'):.4f}")

        # USDC balance (Polygon USDC contract)
        USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        usdc_abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
        usdc = w3.eth.contract(address=USDC_CONTRACT, abi=usdc_abi)
        usdc_bal = usdc.functions.balanceOf(address).call()
        print(f"   USDC:  ${usdc_bal / 1e6:.2f}")

        if usdc_bal < 1_000_000:  # < $1
            print("\n⚠️  WARNING: USDC balance < $1. Deposit before live trading.")
        else:
            print("\n✅ USDC balance sufficient for testing.")

        return usdc_bal

    except Exception as e:
        print(f"❌ Balance check failed: {e}")
        return 0


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print("""
╔═════════════════════════════════════════════════╗
║  🧪 POLYMARKET CLOB SIGNING TEST — BOT B LIVE PREP               ║
╚═════════════════════════════════════════════════╝
""")

    # Install check
    try:
        import py_clob_client
        print("✅ py-clob-client installed.")
    except ImportError:
        print("❌ py-clob-client NOT installed!")
        print("   Run: pip install py-clob-client web3")
        print()
        cont = input("Continue anyway? (y/n): ")
        if cont.lower() != 'y':
            sys.exit(1)

    # Run tests
    client, api_creds = test_api_key()
    if not client:
        print("\n❌ Cannot continue without API key.")
        sys.exit(1)

    yes_token, no_token = test_market_tokens(client)
    if not yes_token:
        print("\n⚠️  Market tokens not found. Check if BTC 5m market is active.")

    if yes_token and api_creds:
        test_order_signing(client, api_creds, yes_token, no_token)

    test_balance(client)

    print("\n" + "="*55)
    print("📋 TEST COMPLETE")
    print("="*55)
    print("""
Results:
  ✅ If all tests passed → Bot is ready for LIVE integration
  ⚠️  If any test failed → Fix before depositing

Next steps:
  1. Deposit $1-2 USDC to test a REAL micro trade ($0.50)
  2. If micro trade works → Deposit $25 and go live
  3. Run bot with: python poly_btc_5m_lock_50_live.py
""")


if __name__ == "__main__":
    main()
