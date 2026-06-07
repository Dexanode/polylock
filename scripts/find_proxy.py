#!/usr/bin/env python3
"""Find Polymarket deposit proxy for EOA 0x94A66..."""
import os, json, urllib.request
from web3 import Web3

env_file = os.path.join(os.path.dirname(__file__), ".env")
env = {}
with open(env_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k,v = line.split("=",1)
            env[k.strip()] = v.strip()

PK = env.get("POLYMARKET_PRIVATE_KEY","")
EOA = Web3().eth.account.from_key(PK).address
print(f"EOA: {EOA}")

# V2 Exchange contract  
EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
RPC = "https://polygon-rpc.com"

def eth_call(contract, data):
    payload = json.dumps({
        "jsonrpc":"2.0","method":"eth_call",
        "params":[{"to":contract,"data":data},"latest"],"id":1
    }).encode()
    req = urllib.request.Request(RPC, data=payload, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

# Try common proxy mapping selectors
w3 = Web3()
tests = {
    "proxies":      Web3.keccak(text="proxies(address)").hex()[:10],
    "userProxy":    Web3.keccak(text="userProxy(address)").hex()[:10],  
    "deposits":     Web3.keccak(text="deposits(address)").hex()[:10],
    "getProxy":     Web3.keccak(text="getProxy(address)").hex()[:10],
    "wallets":      Web3.keccak(text="wallets(address)").hex()[:10],
}

print("\nChecking proxy selectors on V2 exchange...")
EOA_PAD = EOA[2:].lower().rjust(64,"0")

for name, selector in tests.items():
    try:
        data = selector + EOA_PAD
        result = eth_call(EXCHANGE, data)
        val = result.get("result","0x")
        if val and val != "0x" and val != "0x0000000000000000000000000000000000000000000000000000000000000000":
            proxy = "0x" + val[-40:]
            print(f"  {name}: {proxy}")
        else:
            print(f"  {name}: (empty)")
    except Exception as e:
        print(f"  {name}: error - {e}")

# Also check pUSD balance
print("\nPUSD balance:")
PUSD = "0x9ecb7c4eCD45F6D55020b9E301bddAEbf475D7e7"
try:
    bal_data = "0x70a08231" + EOA_PAD
    result = eth_call(PUSD, bal_data)
    bal = int(result.get("result","0x0"),16) / 1e6
    print(f"  pUSD: ${bal:,.2f}")
except Exception as e:
    print(f"  Error: {e}")

print("\nDone. If no proxy found → deposit $1 via polymarket.com to create one.")
