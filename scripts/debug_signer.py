#!/usr/bin/env python3
"""Debug: check what address the SDK signs orders with."""
import os, json, hmac, hashlib, base64, time, urllib.request

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
API_KEY = env.get("POLYMARKET_API_KEY","")
API_SECRET = env.get("POLYMARKET_API_SECRET","")
API_PASSPHRASE = env.get("POLYMARKET_API_PASSPHRASE","")

# Fund wallet address from PK
from web3 import Web3
acct = Web3().eth.account.from_key(PK)
eoa = acct.address
print(f"🔑 EOA from private key: {eoa}")

# Create ClobClient exactly as the bot does
from py_clob_client_v2 import ClobClient, ApiCreds
from py_clob_client_v2.constants import POLYGON

creds = ApiCreds(api_key=API_KEY, api_secret=API_SECRET, api_passphrase=API_PASSPHRASE)
client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=POLYGON,
    key=PK,
    creds=creds,
)

# What signer address does the SDK use?
signer_addr = client.signer.address() if hasattr(client, 'signer') else "???"
print(f"✍️  SDK signer address:   {signer_addr}")
print(f"🏦 API key registered to: {eoa}")
print(f"   Match? {'✅' if signer_addr.lower() == eoa.lower() else '❌'}")

# Also check builder
if hasattr(client, 'builder'):
    funder = client.builder.funder if hasattr(client.builder, 'funder') else "N/A"
    sig_type = client.builder.signature_type if hasattr(client.builder, 'signature_type') else "N/A"
    print(f"\n📦 Builder info:")
    print(f"   funder: {funder}")
    print(f"   signature_type: {sig_type}")

# Check what the API server knows about this API key
print(f"\n📡 Checking API key info...")
ts = str(int(time.time() * 1000))
message = ts + "GET" + "/auth/api-keys" + ""
raw_secret = API_SECRET
missing = len(raw_secret) % 4
if missing:
    raw_secret += "=" * (4-missing)
secret = base64.b64decode(raw_secret)
sig = base64.b64encode(hmac.new(secret, message.encode(), hashlib.sha256).digest()).decode()
headers = {
    "POLY-API-KEY": API_KEY,
    "POLY-TIMESTAMP": ts,
    "POLY-SIGNATURE": sig,
    "POLY-PASSPHRASE": API_PASSPHRASE,
}
try:
    req = urllib.request.Request("https://clob.polymarket.com/auth/api-keys", headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    print(f"   API keys: {json.dumps(data, indent=2)[:500]}")
except Exception as e:
    print(f"   ❌ {e}")

# Now try a minimal order with a test token
print(f"\n📦 Attempting minimal order...")
try:
    # Use a token from any active market
    from py_clob_client_v2 import OrderArgs, OrderType
    token = "31691179189694423868280796823578943857381975642534418186479226012315446853632"  # random BTC YES
    order_args = OrderArgs(token_id=token, price=0.01, size=1.0, side="BUY")
    resp = client.create_and_post_order(order_args, order_type=OrderType.FOK)
    print(f"✅ Order response: {resp}")
except Exception as e:
    err = str(e)
    print(f"❌ Error: {err[:300]}")
    # Try to extract what address the API expects
    if "signer address" in err.lower():
        print(f"\n🔍 The API wants a different signer than {eoa}")
        print(f"   Possible causes:")
        print(f"   1. API key was created with different wallet")
        print(f"   2. Need deposit proxy address as signer")
