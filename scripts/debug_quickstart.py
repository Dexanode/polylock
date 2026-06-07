#!/usr/bin/env python3
"""Test: derive key → create client → post order. All in one flow."""
import os, sys, json
sys.path.insert(0, os.path.expanduser("~/polymarket/venv/lib/python3.12/site-packages"))

env_file = os.path.join(os.path.dirname(__file__), ".env")
env = {}
with open(env_file) as f:
    for l in f:
        l = l.strip()
        if l and not l.startswith("#") and "=" in l:
            k,v = l.split("=",1)
            env[k.strip()] = v.strip()

PK = env["POLYMARKET_PRIVATE_KEY"]

from web3 import Web3
from py_clob_client_v2 import ClobClient, ApiCreds, OrderArgs, OrderType
from py_clob_client_v2.constants import POLYGON

eoa = Web3().eth.account.from_key(PK).address
print(f"EOA: {eoa}")

# Step 1: Derive API key (EXACT quickstart flow)
print("\n1. Deriving API key...")
client1 = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON, key=PK)
creds = client1.derive_api_key()
print(f"   Signer1: {client1.signer.address()}")
print(f"   Key: {creds.api_key[:20]}...")

# Step 2: New client with creds + set_api_creds (EXACT quickstart)
print("\n2. Creating trading client...")
client2 = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON, key=PK)
client2.set_api_creds(creds)
print(f"   Signer2: {client2.signer.address()}")
print(f"   Mode: {client2.mode}")

# Step 3: Get a valid token
print("\n3. Fetching token...")
import urllib.request
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
ts = int(now.replace(second=0, microsecond=0).timestamp())

# Try multiple slugs - sometimes the market hasn't been created yet
for offset in range(6):
    t = ts - (offset * 300)
    slug = f"btc-updown-5m-{t}"
    try:
        req = urllib.request.Request(
            f"https://gamma-api.polymarket.com/events?slug={slug}",
            headers={"User-Agent":"Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            events = json.loads(r.read())
        if events:
            markets = events[0].get("markets", [])
            if markets:
                yes_tok = next((m["clobTokenIds"][0] for m in markets if m.get("outcome") == "Yes"), None)
                no_tok = next((m["clobTokenIds"][0] for m in markets if m.get("outcome") == "No"), None)
                tok = yes_tok or no_tok
                if tok:
                    print(f"   Token: {tok[:20]}... (slug={slug})")
                    break
    except:
        continue
else:
    print("   ❌ No valid token found")
    tok = None

# Step 4: Post order
if tok:
    print("\n4. Placing order...")
    try:
        oa = OrderArgs(token_id=tok, price=0.50, size=1.0, side="BUY")
        resp = client2.create_and_post_order(oa, order_type=OrderType.FOK)
        print(f"   ✅ Response: {resp}")
    except Exception as e:
        err = str(e)
        print(f"   ❌ {err[:300]}")
        if "signer" in err:
            print(f"\n   ⚠️  Still signer mismatch even with QuickStart flow!")
            print(f"   This means the wallet {eoa} cannot place orders at all.")
            print(f"   CLOB V2 requires a deposit wallet proxy address.")
else:
    print("   Skipping order test")
