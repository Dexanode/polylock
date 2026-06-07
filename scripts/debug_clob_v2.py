#!/usr/bin/env python3
"""Debug script — test Polymarket CLOB V2 balance + simple order."""

import json, os, sys

PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
CREDS_FILE   = os.path.expanduser("~/polymarket/clob_creds.json")
TOKEN_ID     = None  # Will try a random active BTC token

print("="*60)
print(" DEBUG CLOB V2")
print("="*60)

# 1. Wallet
from web3 import Web3
from eth_account import Account
acct = Account.from_key(PRIVATE_KEY)
wallet = acct.address
print(f"\n🔑 Wallet: {wallet}")

# 2. Load creds
with open(CREDS_FILE) as f:
    creds_dict = json.load(f)
print(f"📁 Creds loaded: api_key={creds_dict['api_key'][:12]}...")

# 3. Create ApiCreds
from py_clob_client_v2 import ApiCreds
api_creds = ApiCreds(api_key=creds_dict["api_key"], api_secret=creds_dict["api_secret"], api_passphrase=creds_dict["api_passphrase"])
print(f"✅ ApiCreds created")

# 4. Create client
from py_clob_client_v2 import ClobClient, SignatureTypeV2
from py_clob_client_v2.constants import POLYGON

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=POLYGON,
    key=PRIVATE_KEY,
    creds=api_creds,
    signature_type=SignatureTypeV2.POLY_1271,
    funder=wallet,
)
print(f"✅ ClobClient created (type={SignatureTypeV2.POLY_1271})")

# 5. Test balance
print(f"\n{'='*40}")
print(" TEST 1: Balance")
print("="*40)
try:
    bal = client.get_balance_allowance()
    print(f"📊 Raw response: {json.dumps(bal, indent=2)}")
    print(f"💰 Balance: ${float(bal.get('balance', 0)):,.2f}")
except Exception as e:
    print(f"❌ Balance error: {e}")
    import traceback
    traceback.print_exc()

# 6. Test simple order (low price FOK — won't fill but should not error)
print(f"\n{'='*40}")
print(" TEST 2: Order (FOK test)")
print("="*40)

# Fetch a BTC market token
import urllib.request, time
from datetime import datetime, timezone

# Find next BTC 5m window
now = datetime.now(timezone.utc)
window_start = now.replace(second=0, microsecond=0)
window_ts = int(window_start.timestamp())

try:
    slug = f"btc-updown-5m-{window_ts}"
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    
    if data:
        markets = data[0].get("markets", [])
        yes_token = next((m["clobTokenIds"][0] for m in markets if m.get("outcome") == "Yes"), None)
        no_token  = next((m["clobTokenIds"][0] for m in markets if m.get("outcome") == "No"), None)
        TOKEN_ID = yes_token or no_token
        print(f"📌 BTC 5m window: {slug}")
        print(f"📌 YES token: {yes_token}")
except Exception as e:
    print(f"[WARN] Gamma API: {e}")

if TOKEN_ID:
    try:
        from py_clob_client_v2 import OrderArgs, OrderType
        from py_clob_client_v2.order_builder.constants import BUY

        order_args = OrderArgs(
            token_id=TOKEN_ID,
            price=0.50,
            size=1.0,
            side="BUY",
        )
        resp = client.create_and_post_order(order_args, order_type=OrderType.FOK)
        print(f"📊 Order response: {json.dumps(resp, indent=2) if isinstance(resp, dict) else resp}")
        print(f"✅ POST /order = OK (status={resp.get('status', '?')})")
    except Exception as e:
        print(f"❌ Order error: {e}")
        import traceback
        traceback.print_exc()
else:
    print("⚠️  Token not found — skip order test")

print(f"\n{'='*60}")
print(" DEBUG DONE")
print("="*60)
