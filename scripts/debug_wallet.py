#!/usr/bin/env python3
"""Find Polymarket deposit wallet address."""
import os, json

def load_env():
    env = {}
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env

env = load_env()
PK = env.get("POLYMARKET_PRIVATE_KEY", "")
if not PK:
    print("❌ POLYMARKET_PRIVATE_KEY not in .env")
    exit(1)

from web3 import Web3
acct = Web3().eth.account.from_key(PK)
eoa = acct.address
print(f"🔑 EOA: {eoa}")

# 1. Delete old creds (force re-derive)
creds_file = os.path.expanduser("~/polymarket/clob_creds.json")
import os as _os
if _os.path.exists(creds_file):
    _os.remove(creds_file)
    print(f"🗑️  Deleted old creds")

# 2. Derive new API key
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.constants import POLYGON

print("\n📡 Deriving API key...")
client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=POLYGON,
    key=PK,
)
creds = client.create_or_derive_api_key()
print(f"✅ API Key: {creds.api_key[:12]}...")

# 3. Check what address the API key is registered to
print(f"\n🔍 Checking signer computation...")
print(f"   EOA address: {eoa}")

# 4. Test balance
from py_clob_client_v2 import ApiCreds
api_creds = ApiCreds(api_key=creds.api_key, api_secret=creds.api_secret, api_passphrase=creds.api_passphrase)

client2 = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=POLYGON,
    key=PK,
    creds=api_creds,
)

print("\n💰 Testing balance...")
try:
    bal = client2.get_balance_allowance()
    print(f"   Balance: {json.dumps(bal, indent=2)}")
except Exception as e:
    print(f"   ❌ Balance error: {e}")

# 5. Try to fetch user profile / check wallet status
print("\n📋 Polymarket says:")
try:
    # Check if the API considers the wallet as EOA or deposit wallet
    from py_clob_client_v2 import SignatureTypeV2
    
    # Try with EOA
    for sig_type, sig_name in [(None, "EOA(default)"), (SignatureTypeV2.EOA, "EOA"), (SignatureTypeV2.POLY_1271, "POLY_1271")]:
        print(f"\n   Testing signature_type={sig_name}...")
        try:
            tc = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON, key=PK, creds=api_creds,
                          signature_type=sig_type, funder=eoa if sig_type else None)
            b = tc.get_balance_allowance()
            print(f"   ✅ balance OK: ${b.get('balance', 0)}")
        except Exception as e:
            err = str(e)[:100]
            print(f"   ❌ {err}")
except Exception as e:
    print(f"   Error: {e}")

# 6. Save new creds
with open(creds_file, "w") as f:
    json.dump({
        "api_key": creds.api_key,
        "api_secret": creds.api_secret,
        "api_passphrase": creds.api_passphrase,
    }, f)
print(f"\n💾 Saved new creds to {creds_file}")
