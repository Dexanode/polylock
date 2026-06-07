#!/usr/bin/env python3
"""Find deposit wallet address from Polymarket on-chain."""
import os, json, urllib.request

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
from web3 import Web3
eoa = Web3().eth.account.from_key(PK).address
print(f"🔑 EOA: {eoa}")

# 1. Cek pUSD balance (ini token V2)
RPC = "https://polygon-rpc.com"
def call(contract, data):
    payload = json.dumps({"jsonrpc":"2.0","method":"eth_call","params":[{"to":contract,"data":data},"latest"],"id":1}).encode()
    req = urllib.request.Request(RPC, data=payload, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def balance_of(token, addr):
    selector = "0x70a08231" + addr[2:].lower().rjust(64,"0")
    result = call(token, selector)
    return int(result.get("result","0x0"),16) / 1e6

# pUSD contract (Polymarket USD — new collateral)
# Cek address dari Polymarket docs
pUSD_ADDRESSES = [
    "0x9ecb7c4eCD45F6D55020b9E301bddAEbf475D7e7",  # Coba
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e
]

# Cek USDC di wallet
usdc = balance_of("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", eoa)
print(f"\n💰 Wallet balances:")
print(f"   USDC: ${usdc:,.2f}")

for p in pUSD_ADDRESSES:
    try:
        bal = balance_of(p, eoa)
        print(f"   {p[:10]}...: ${bal:,.2f}")
    except:
        print(f"   {p[:10]}...: (not found)")

# 2. Coba derive_api_key langsung (tanpa create_or_derive)
print(f"\n🔑 Deriving API key...")
import sys
sys.path.insert(0, os.path.expanduser("~/polymarket/venv/lib/python3.12/site-packages"))
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.constants import POLYGON

client = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON, key=PK)

try:
    creds = client.derive_api_key()
    print(f"✅ Derive OK: {creds.api_key[:12]}...")
    print(f"   (Simpan ini sebagai POLYMARKET_API_KEY di .env)")
except Exception as e:
    print(f"❌ Derive failed: {e}")
    print(f"   → Wallet belum pernah deposit di Polymarket.com")
    print(f"   → Buka polymarket.com, connect wallet, deposit USDC")

# 3. Coba cari deposit proxy address via Polymarket API
print(f"\n🔍 Mencari deposit info...")
API_KEY = ***"POLYMARKET_API_KEY","")
API_SECRET = ***"POLYMARKET_API_SECRET","")
API_PASSPHRASE = env.get("POLYMARKET_API_PASSPHRASE","")

if API_KEY:
    import hmac, hashlib, base64, time
    ts = str(int(time.time()*1000))
    msg = ts + "GET" + "/balance-allowance" + ""
    raw = API_SECRET
    if len(raw) % 4:
        raw += "=" * (4 - len(raw)%4)
    secret = base64.b64decode(raw)
    sig = base64.b64encode(hmac.new(secret, msg.encode(), hashlib.sha256).digest()).decode()
    headers = {"POLY-API-KEY": API_KEY, "POLY-TIMESTAMP": ts, "POLY-SIGNATURE": sig, "POLY-PASSPHRASE": API_PASSPHRASE}
    try:
        req = urllib.request.Request("https://clob.polymarket.com/balance-allowance?asset_type=COLLATERAL", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"   Balance response: {r.read().decode()[:300]}")
    except Exception as e:
        print(f"   ❌ {e}")
