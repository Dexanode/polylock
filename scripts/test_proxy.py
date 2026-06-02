#!/usr/bin/env python3
"""
Proxy connectivity test for Polymarket bot.
Usage:
    # Test tanpa proxy
    python3 test_proxy.py

    # Test dengan proxy
    PROXY_URL="http://user:pass@gate.proxies.fo:7777" python3 test_proxy.py
"""

import json
import os
import sys
import urllib.request
import urllib.error

PROXY_URL = os.environ.get("PROXY_URL", "").strip()

TESTS = [
    ("Binance BTC Price",      "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"),
    ("Polymarket Gamma API",   "https://gamma-api.polymarket.com/events?limit=1"),
    ("Polymarket CLOB API",    "https://clob.polymarket.com/markets?limit=1"),
    ("Polymarket Data API",    "https://data-api.polymarket.com/trades?limit=1"),
    ("IP Check (ipinfo.io)",   "https://ipinfo.io/json"),
]


def build_opener(proxy_url: str):
    """Build urllib opener with optional proxy."""
    handlers = []
    if proxy_url:
        if proxy_url.startswith("socks"):
            # SOCKS5 needs PySocks
            try:
                import socks
                import socket
                proto, rest = proxy_url.split("://", 1)
                if "@" in rest:
                    auth, hostport = rest.rsplit("@", 1)
                    user, pw = auth.split(":", 1)
                else:
                    user, pw = None, None
                    hostport = rest
                host, port = hostport.rsplit(":", 1)
                socks_type = socks.SOCKS5 if proto == "socks5" else socks.SOCKS4
                socks.set_default_proxy(socks_type, host, int(port), username=user, password=pw)
                socket.socket = socks.socksocket
                print(f"🧦 SOCKS5 proxy configured: {host}:{port}")
            except ImportError:
                print("⚠️  PySocks not installed. SOCKS5 needs: apt-get install python3-socks")
                print("   Falling back to no proxy.")
        else:
            proxy_handler = urllib.request.ProxyHandler({
                "http":  proxy_url,
                "https": proxy_url,
            })
            handlers.append(proxy_handler)
            print(f"🌐 HTTP proxy configured: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")

    return urllib.request.build_opener(*handlers)


def run_tests(opener):
    print("\n" + "=" * 58)
    print("  POLYMARKET BOT — PROXY CONNECTIVITY TEST")
    print("=" * 58)

    all_ok = True
    for name, url in TESTS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=12) as resp:
                data = json.loads(resp.read())
                # Extract relevant field
                if "ipinfo" in url:
                    ip      = data.get("ip", "?")
                    country = data.get("country", "?")
                    city    = data.get("city", "?")
                    org     = data.get("org", "?")[:30]
                    detail  = f"IP: {ip} | {country} {city} | {org}"
                elif "binance" in url:
                    detail = f"BTC: ${float(data['price']):,.2f}"
                elif "gamma" in url:
                    detail = f"{len(data)} event(s) returned"
                elif "clob" in url:
                    detail = f"{len(data.get('data', []))} market(s) returned"
                elif "data-api" in url:
                    detail = f"{len(data)} trade(s) returned"
                else:
                    detail = "OK"
                print(f"  ✅  {name:<28} {detail}")
        except urllib.error.HTTPError as e:
            print(f"  ❌  {name:<28} HTTP {e.code} — {e.reason}")
            all_ok = False
        except Exception as e:
            print(f"  ❌  {name:<28} {e}")
            all_ok = False

    print("=" * 58)
    if all_ok:
        print("  ✅  ALL TESTS PASSED — Bot is ready to run")
    else:
        print("  ❌  SOME TESTS FAILED — Check proxy settings")
    print("=" * 58)
    return all_ok


if __name__ == "__main__":
    if PROXY_URL:
        print(f"\n📡 Using proxy: {PROXY_URL.split('@')[-1] if '@' in PROXY_URL else PROXY_URL}")
    else:
        print("\n📡 No proxy — testing direct connection")

    opener = build_opener(PROXY_URL)
    ok = run_tests(opener)
    sys.exit(0 if ok else 1)
