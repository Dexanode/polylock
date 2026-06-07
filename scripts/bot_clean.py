#!/usr/bin/env python3
"""
Polymarket BTC 5m LOCK Strategy — $50 Threshold Bot

Usage:
  python3 poly_btc_5m_lock_50.py [--live] [--telegram-token TOKEN --chat-id ID]
  python3 poly_btc_5m_lock_50.py --bankroll 10 --spread 50 --daily-stop 5
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from enum import Enum


# ── Auto-load .env ──────────────────────────────────────────
_ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                _key, _val = _key.strip(), _val.strip()
                if _key and _val and _key not in os.environ:
                    os.environ[_key] = _val

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

CHECK_INTERVAL       = 5          # seconds between price fetches
SPREAD_THRESHOLD     = 100        # USD minimum spread — dinaikkan dari 50 ke 100 untuk sinyal lebih kuat
ALERT_WINDOW_START   = 3 * 60 + 30  # 210s — start of LOCK zone
ALERT_WINDOW_END     = 4 * 60 + 20  # 260s — end of LOCK zone (stop 40s sebelum close)
FEE_RATE             = 0.02       # Polymarket taker fee
MIN_ORDER_USDC       = 1.0        # Polymarket minimum order $1
BUY_PRICE_BUFFER     = 0.10       # Add to price to cross spread & ensure fill (min $0.10)
MAX_ENTRY_PRICE      = 0.72       # Harga real Polymarket max untuk entry — turun dari 0.80 ke 0.72

def fetch_polymarket_balance(private_key: str, clob_creds: Optional[Dict] = None) -> Optional[float]:
    """Fetch real Polymarket pUSD balance via CLOB SDK (POLY_1271)."""
    if not clob_creds:
        return None
    # Suppress SSL warnings for Polymarket CLOB API
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        _patch_httpx_proxy()
        from py_clob_client_v2 import ClobClient, ApiCreds, SignatureTypeV2
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        from py_clob_client_v2.constants import POLYGON

        api = ApiCreds(
            api_key=clob_creds["api_key"],
            api_secret=clob_creds["api_secret"],
            api_passphrase=clob_creds["api_passphrase"],
        )
        funder = compute_deposit_wallet_address(
            __import__('web3').Web3().eth.account.from_key(private_key).address
        )
        client = ClobClient(
            host=CLOB_HOST, chain_id=POLYGON,
            key=private_key, creds=api,
            signature_type=SignatureTypeV2.POLY_1271,
            funder=funder,
        )
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        bal_data = client.get_balance_allowance(params)
        raw = float(bal_data.get("balance", 0))
        balance = raw / 1_000_000 if raw > 1000 else raw
        print(f"[BALANCE] Polymarket: ${balance:,.2f}")
        return balance
    except Exception as e:
        print(f"[WARN] Polymarket balance: {e}")
        return None

def fetch_usdc_balance(wallet_address: str) -> float:
    USDC_CONTRACT = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
    BALANCE_SELECTOR = "0x70a08231" + wallet_address[2:].lower().rjust(64, "0")
    payload = json.dumps({"jsonrpc":"2.0","method":"eth_call","params":[{"to":USDC_CONTRACT,"data":BALANCE_SELECTOR},"latest"],"id":1}).encode()
    for rpc in POLYGON_RPCS:
        try:
            import httpx
            resp = httpx.post(rpc, content=payload, headers={"Content-Type":"application/json"}, timeout=8, verify=False)
            resp.raise_for_status()
            result = resp.json()
            hex_val = result.get("result", "0x0")
            if hex_val and hex_val != "0x":
                return int(hex_val, 16) / 1e6
        except Exception:
            continue
    return 0.0

# Signal filters
MIN_VOLUME_RATIO     = 0.25       # skip if volume < 0.25x 10-candle avg (diturunkan dari 0.5x — spike inflasi avg)
MOMENTUM_THRESHOLD   = 0.12       # max opposing 3-candle slope % allowed
EV_THRESHOLD         = 0.0        # min expected value per share
MAX_FILTER_ATTEMPTS  = 3          # max re-checks per window before giving up

# Slippage buffer added to estimated entry (realistic for live)
SLIPPAGE_BUFFER      = 0.02

# Price history: keep last N samples for boundary-accurate resolution
PRICE_HISTORY_SIZE   = 60         # 60 × 5s = 5 minutes coverage

# URLs
BINANCE_URL  = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
KLINES_URL   = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=20"
YAHOO_URL    = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?interval=1m&range=1d"

# Chainlink BTC/USD on Polygon — same feed Polymarket uses to resolve markets
CHAINLINK_CONTRACT = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
CHAINLINK_SELECTOR = "0x50d25bcd"   # latestAnswer()
POLYGON_RPCS = [
    "https://polygon.drpc.org",
    "https://rpc-mainnet.matic.quiknode.pro",
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
]

# Binance 5m kline — untuk ambil open price window yang akurat
KLINES_5M_URL = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=2"

# Polymarket CLOB
CLOB_HOST  = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
BUILDER_CODE = os.environ.get("POLYMARKET_BUILDER_CODE", "0x2111a204350f2c552401b7d34b7cb61021e32b68a17a15ef712b978fd991f55d")
DEPOSIT_WALLET_FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
DEPOSIT_WALLET_IMPLEMENTATION = "0x58CA52ebe0DadfdF531Cde7062e76746de4Db1eB"

# Persistent log file — dashboard reads ini, survive restart
LOG_DIR      = os.path.join(os.path.dirname(__file__), "..", "logs")
WINDOWS_LOG  = os.path.join(LOG_DIR, "windows.jsonl")
STATS_LOG    = os.path.join(LOG_DIR, "stats.json")
CLOB_CREDS_FILE = os.path.join(LOG_DIR, "clob_creds.json")

# ---------------------------------------------------------------------------
# PERSISTENT LOGGING — dashboard reads from these files
# ---------------------------------------------------------------------------

def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)

def log_window(window) -> None:
    """Append/update window result ke windows.jsonl (1 JSON object per line)."""
    _ensure_log_dir()
    record = {
        "start":       window.start.isoformat(),
        "ptb":         window.ptb,
        "direction":   window.direction.value,
        "traded":      window.traded,
        "entry_price": window.entry_price,
        "size":        window.size,
        "final_spread": window.final_spread,
        "result":      window.result,
        "ts":          datetime.now(timezone.utc).isoformat(),
    }
    with open(WINDOWS_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

def log_stats(stats, bankroll: float, mode: str = "paper") -> None:
    """Overwrite stats.json dengan daily stats terkini."""
    _ensure_log_dir()
    record = {
        "date":     stats.date,
        "mode":     mode,
        "trades":   stats.trades,
        "wins":     stats.wins,
        "losses":   stats.losses,
        "profit":   round(stats.profit, 4),
        "bankroll": round(bankroll, 4),
        "peak_bankroll": round(stats.peak_bankroll, 4),
        "ts":       datetime.now(timezone.utc).isoformat(),
    }
    with open(STATS_LOG, "w") as f:
        json.dump(record, f)

# ---------------------------------------------------------------------------
# LIVE TRADING — CLOB Integration
# ---------------------------------------------------------------------------

def compute_deposit_wallet_address(owner: str) -> str:
    """Compute UUPS deposit wallet address via CREATE2."""
    from eth_utils import to_bytes, to_checksum_address, keccak
    from eth_abi import encode
    CONST1 = "0xcc3735a920a3ca505d382bbc545af43d6000803e6038573d6000fd5b3d6000f3"
    CONST2 = "0x5155f3363d3d373d3d363d7f360894a13ba1a3210667c828492db98dca3e2076"
    PREFIX = 0x61003D3D8160233D3973
    factory = to_checksum_address(DEPOSIT_WALLET_FACTORY)
    impl = to_checksum_address(DEPOSIT_WALLET_IMPLEMENTATION)
    owner_bytes = to_bytes(hexstr=owner).rjust(32, b"\x00")
    args = encode(["address", "bytes32"], [factory, owner_bytes])
    salt = keccak(args)
    n = len(args)
    combined = PREFIX + (n << 56)
    init_code = (combined.to_bytes(10, "big") + to_bytes(hexstr=impl) +
                 to_bytes(hexstr="0x6009") + to_bytes(hexstr=CONST2) +
                 to_bytes(hexstr=CONST1) + args)
    code_hash = "0x" + keccak(init_code).hex()
    wallet = "0x" + keccak(b"\xff" + to_bytes(hexstr=factory) + salt + to_bytes(hexstr=code_hash)).hex()[-40:]
    return to_checksum_address(wallet)


def load_or_create_clob_creds(private_key: str) -> Optional[Dict]:
    """
    Load saved CLOB API creds dari file, atau generate baru dari wallet.
    Creds disimpan supaya tidak perlu generate ulang setiap restart.
    """
    _ensure_log_dir()

    # Coba load yang tersimpan
    if os.path.exists(CLOB_CREDS_FILE):
        try:
            with open(CLOB_CREDS_FILE) as f:
                creds = json.load(f)
            if all(k in creds for k in ("api_key", "api_secret", "api_passphrase")):
                print(f"✅ CLOB creds loaded from file")
                return creds
        except Exception:
            pass

    # Generate / derive creds with POLY_1271 + funder
    try:
        _patch_httpx_proxy()
        from py_clob_client_v2 import ClobClient, SignatureTypeV2
        from py_clob_client_v2.constants import POLYGON
        from web3 import Web3

        eoa = Web3().eth.account.from_key(private_key).address
        funder = compute_deposit_wallet_address(eoa)
        print(f"💳 EOA: {eoa}")
        print(f"🏦 Deposit Wallet: {funder}")

        client = ClobClient(
            host=CLOB_HOST, chain_id=POLYGON,
            key=private_key,
            signature_type=SignatureTypeV2.POLY_1271,
            funder=funder,
        )

        # Coba derive dulu (jika wallet sudah pernah terdaftar)
        try:
            print("🔑 Deriving existing CLOB API credentials...")
            creds_obj = client.derive_api_key()
            print(f"✅ Existing CLOB creds derived")
        except Exception:
            # Belum ada — buat baru
            print("🔑 Creating new CLOB API credentials...")
            creds_obj = client.create_api_key()
            print(f"✅ New CLOB creds created")

        creds = {
            "api_key":        creds_obj.api_key,
            "api_secret":     creds_obj.api_secret,
            "api_passphrase": creds_obj.api_passphrase,
        }
        with open(CLOB_CREDS_FILE, "w") as f:
            json.dump(creds, f)
        return creds
    except Exception as e:
        print(f"[ERROR] CLOB auth failed: {e}")
        return None


def fetch_polymarket_real_prices(window_start: datetime) -> Tuple[float, float]:
    """
    Fetch harga real UP/DOWN dari Polymarket gamma API untuk window saat ini.
    Returns (up_price, down_price) dalam range 0.0-1.0
    Returns (0.0, 0.0) jika gagal.
    """
    try:
        ts   = int(window_start.timestamp())
        slug = f"btc-updown-5m-{ts}"
        url  = f"{GAMMA_HOST}/markets?slug={slug}"
        import httpx
        resp = httpx.get(url, timeout=8, verify=False)
        resp.raise_for_status()
        markets = resp.json()
        if not markets:
            return 0.0, 0.0
        m      = markets[0]
        prices = json.loads(m.get("outcomePrices", "[0,0]")) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices", [0, 0])
        outcomes = json.loads(m.get("outcomes", '["Up","Down"]')) if isinstance(m.get("outcomes"), str) else m.get("outcomes", ["Up", "Down"])
        up_idx   = next((i for i, o in enumerate(outcomes) if "up" in str(o).lower()), 0)
        down_idx = next((i for i, o in enumerate(outcomes) if "down" in str(o).lower()), 1)
        return float(prices[up_idx]), float(prices[down_idx])
    except Exception as e:
        print(f"[WARN] fetch real prices: {e}")
        return 0.0, 0.0


def fetch_btc_5m_market_tokens(window_start: Optional[datetime] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Cari token YES/NO untuk BTC Up/Down 5m market yang sedang aktif.

    Setiap window 5 menit = market tersendiri dengan slug: btc-updown-5m-{timestamp}
    YES = Up (harga naik), NO = Down (harga turun).
    Returns (up_token_id, down_token_id).
    """
    # Method 1: Fetch by slug dengan timestamp window saat ini
    if window_start:
        ts = int(window_start.timestamp())
        slug = f"btc-updown-5m-{ts}"
        try:
            url = f"{GAMMA_HOST}/markets?slug={slug}"
            import httpx
            # Use patched httpx client which ignores proxy and SSL verify if needed
            resp = httpx.get(url, timeout=8, verify=False)
            resp.raise_for_status()
            markets = resp.json()
            if markets:
                tokens = _parse_tokens_with_outcomes(markets[0])
                if tokens[0]:
                    print(f"✅ Market by slug: {markets[0].get('question')}")
                    return tokens
        except Exception as e:
            print(f"[WARN] Slug fetch: {e}")

    # Method 2: Search public-search untuk "Bitcoin Up or Down" aktif terdekat
    try:
        from urllib.parse import quote
        url = f"{GAMMA_HOST}/public-search?q={quote('bitcoin up or down')}"
        import httpx
        resp = httpx.get(url, timeout=8, verify=False)
        resp.raise_for_status()
        data = resp.json()
        now_ts = datetime.now(timezone.utc).timestamp()
        best_ev = None
        best_diff = float("inf")
        for ev in data.get("events", []):
            slug = ev.get("slug", "")
            if slug.startswith("btc-updown-5m-"):
                try:
                    ev_ts = int(slug.split("-")[-1])
                    diff = abs(ev_ts - now_ts)
                    if diff < best_diff:
                        best_diff = diff
                        best_ev = ev
                except Exception:
                    pass
        if best_ev:
            for m in best_ev.get("markets", []):
                tokens = _parse_tokens_with_outcomes(m)
                if tokens[0]:
                    print(f"✅ Market found (Δ{best_diff:.0f}s): {best_ev.get('title')}")
                    return tokens
    except Exception as e:
        print(f"[WARN] Market search: {e}")

    print("[WARN] BTC 5m market not found — market mungkin belum dibuka")
    return None, None


def _parse_tokens_with_outcomes(market: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse token IDs dan map ke UP/DOWN direction.
    Polymarket BTC Up/Down: outcome 'Up' = UP token, 'Down' = DOWN token.
    """
    try:
        raw      = market.get("clobTokenIds", "[]")
        outcomes_raw = market.get("outcomes", '["Up","Down"]')
        ids      = json.loads(raw)      if isinstance(raw, str)          else raw
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw

        if len(ids) < 2:
            return None, None

        # Cari index Up dan Down
        up_idx   = next((i for i, o in enumerate(outcomes) if "up" in str(o).lower()), 0)
        down_idx = next((i for i, o in enumerate(outcomes) if "down" in str(o).lower()), 1)

        return ids[up_idx], ids[down_idx]
    except Exception:
        return None, None


def _parse_tokens(market: dict) -> Optional[Tuple[str, str]]:
    """Parse clobTokenIds dari market object."""
    try:
        raw = market.get("clobTokenIds", "[]")
        ids = json.loads(raw) if isinstance(raw, str) else raw
        if len(ids) >= 2:
            return ids[0], ids[1]
    except Exception:
        pass
    return None


class _RequestsProxyClient:
    """
    Drop-in replacement untuk httpx.Client yang pakai `requests` library.
    requests handle proxy auth untuk HTTPS CONNECT jauh lebih reliable
    daripada httpx/httpcore yang punya bug untuk beberapa proxy provider.
    """
    def __init__(self, proxy_url: str):
        import requests as _req
        self._session = _req.Session()
        self._session.proxies = {"http": proxy_url, "https": proxy_url}
        self._session.verify = True

    def request(self, method: str, url: str, headers=None, content=None, json=None, **kw):
        import requests as _req
        import httpx as _httpx
        try:
            resp = self._session.request(
                method=method, url=url,
                headers=headers,
                data=content,
                json=json,
            )
            return _FakeHttpxResp(resp)
        except _req.exceptions.RequestException as e:
            raise _httpx.RequestError(str(e)) from e

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass


class _FakeHttpxResp:
    """Minimal httpx.Response shim agar py_clob_client bisa baca status_code + json."""
    def __init__(self, resp):
        self.status_code = resp.status_code
        self._resp = resp

    def json(self):
        return self._resp.json()

    @property
    def text(self):
        return self._resp.text


def _patch_httpx_proxy():
    """
    Ganti module-level _http_client singleton di py_clob_client dengan
    _RequestsProxyClient yang pakai requests library.

    py_clob_client membuat httpx.Client(http2=True) saat module di-import.
    requests handle proxy auth HTTPS CONNECT lebih reliable dari httpx.
    """
    proxy_url = (
        os.environ.get("HTTPS_PROXY") or
        os.environ.get("https_proxy") or
        os.environ.get("HTTP_PROXY") or
        os.environ.get("http_proxy", "")
    )
    if not proxy_url:
        return
    try:
        import py_clob_client_v2.http_helpers.helpers as _helpers

        if getattr(_helpers, "_proxy_patched", False):
            return

        try:
            _helpers._http_client.close()
        except Exception:
            pass

        _helpers._http_client = _RequestsProxyClient(proxy_url)
        _helpers._proxy_patched = True
        print(f"[PROXY] using requests proxy → {proxy_url.split('@')[-1]}")
    except Exception as e:
        print(f"[WARN] proxy patch failed: {e}")


def cancel_all_clob_orders(client) -> int:
    """Cancel all open orders on CLOB. Returns number cancelled."""
    try:
        resp = client.cancel_all()
        cancelled = resp.get("cancelled", []) if isinstance(resp, dict) else []
        count = len(cancelled) if isinstance(cancelled, list) else 0
        if count > 0:
            print(f"[CANCEL] Cancelled {count} stale orders")
        return count
    except Exception as e:
        print(f"[CANCEL] Error: {e}")
        return 0


def check_order_fill(client, order_id: str) -> Optional[float]:
    """Check how many shares of an order have been filled.
    Returns filled_amount (in shares) or None if order not found."""
    try:
        resp = client.get_order(order_id)
        if not resp:
            return None
        filled = float(resp.get("filled", 0) or resp.get("filled_size", 0) or resp.get("filledAmount", 0) or 0)
        return filled
    except Exception as e:
        print(f"[CHECK] Order {order_id[:15]}... error: {e}")
        return None


def _check_liquidity_once(client, token_id: str, min_size_usdc: float = 3.0) -> Tuple[bool, float]:
    """Single liquidity check attempt. Returns (has_liquidity, best_ask_price)."""
    try:
        book = client.get_order_book(token_id)
        if not isinstance(book, dict):
            return False, 0.0
        asks = book.get("asks", [])
        if not asks:
            return False, 0.0
        
        total_capacity = 0.0
        best_ask = 0.0
        for ask in asks:
            try:
                price = float(ask.get("price", 0))
                size = float(ask.get("size", 0))
                if best_ask == 0.0 and price > 0:
                    best_ask = price
                total_capacity += price * size
                if total_capacity >= min_size_usdc:
                    return True, best_ask
            except (ValueError, TypeError):
                continue
        
        print(f"[LIQUIDITY] Total ask depth: ${total_capacity:.2f} (need ${min_size_usdc:.2f})")
        return False, best_ask
    except Exception as e:
        err = str(e)
        if "404" in err or "No orderbook" in err:
            return False, 0.0
        print(f"[LIQUIDITY] Check error: {err[:100]}")
        return False, 0.0

def check_liquidity(client, token_id: str, min_size_usdc: float = 3.0, retries: int = 3, delay: float = 5.0) -> Tuple[bool, float]:
    """Check liquidity with retries. Retries up to `retries` times with `delay`s between.
    Returns (has_liquidity, best_ask_price)."""
    for attempt in range(1, retries + 1):
        has_liq, best_ask = _check_liquidity_once(client, token_id, min_size_usdc)
        if has_liq:
            return True, best_ask
        if attempt < retries:
            print(f"[LIQUIDITY] Attempt {attempt}/{retries} failed (best_ask={best_ask:.2f}) — retrying in {delay}s...")
            time.sleep(delay)
    print(f"[LIQUIDITY] All {retries} attempts failed — orderbook too thin")
    return False, best_ask


def place_live_order(
    private_key: str,
    creds: Dict,
    token_id: str,
    size_usdc: float,
    price: float,
    funder: str = None,
) -> Dict:
    """Place MARKET order via CLOB V2 (immediate fill at best price).
    Returns {"success": True, ...} or {"success": False, "error": "reason"}"""
    try:
        _patch_httpx_proxy()
        from py_clob_client_v2 import ClobClient, ApiCreds, SignatureTypeV2
        from py_clob_client_v2.clob_types import MarketOrderArgsV2
        from py_clob_client_v2.constants import POLYGON
        from web3 import Web3

        api_creds = ApiCreds(
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            api_passphrase=creds["api_passphrase"],
        )
        if funder is None:
            funder = compute_deposit_wallet_address(
                Web3().eth.account.from_key(private_key).address
            )

        client = ClobClient(
            host=CLOB_HOST, chain_id=POLYGON,
            key=private_key, creds=api_creds,
            signature_type=SignatureTypeV2.POLY_1271,
            funder=funder,
        )

        print(f"[MARKET ORDER] ${size_usdc:.2f} USDC → {token_id[:15]}...")
        
        # Pre-check: skip if not enough liquidity to fill size_usdc
        has_liq, best_ask = check_liquidity(client, token_id, min_size_usdc=size_usdc)
        if not has_liq:
            return {"success": False, "error": f"Thin orderbook — not enough asks for ${size_usdc:.2f} (best ask: {best_ask:.2f})"}
        
        # Log best ask drift for info only — don't block (market orders fill at best available price)
        if best_ask > 0 and abs(best_ask - price) > 0.15:
            print(f"[INFO] Best ask {best_ask:.2f} vs signal {price:.2f} (drift {abs(best_ask-price):.2f}) — market order will fill at best price, proceeding")
        
        order_args = MarketOrderArgsV2(
            token_id=token_id,
            amount=size_usdc,  # USD amount to spend
            side="BUY",
            builder_code=BUILDER_CODE,
        )
        
        resp = client.create_and_post_market_order(order_args)

        if not resp:
            return {"success": False, "error": "CLOB API empty response — network issue?"}

        order_id = resp.get("orderID") or resp.get("id", "?")
        status = resp.get("status", "?")
        print(f"[MARKET] ID: {order_id} | Status: {status}")

        if resp.get("success") or status == "matched":
            # Market order filled — get actual fill details
            # FIX 1: Jangan fallback ke estimated price untuk hitung shares.
            # Coba semua field response dulu sebelum fallback ke size_usdc/best_ask.
            filled = check_order_fill(client, order_id)
            if not filled or filled <= 0:
                maker_amount = float(resp.get("maker_amount", 0) or resp.get("taker_amount", 0) or 0)
                if maker_amount > 0:
                    filled = maker_amount
                # Jangan pakai size_usdc/price — price adalah estimasi, bukan fill nyata.
                # Biarkan filled = None, actual_price akan di-set dari best_ask.

            # Hitung actual avg fill price dari biaya & shares nyata.
            # Kalau filled tidak tersedia, gunakan best_ask dari orderbook sebagai proxy.
            if filled and filled > 0:
                actual_price = size_usdc / filled
            else:
                # Fallback terbaik: best_ask yang sudah dicek sebelum order
                actual_price = price  # price = real Polymarket price dari _execute_trade, bukan estimasi spread
                filled = size_usdc / actual_price if actual_price > 0 else 0
                print(f"[WARN] Fill amount unknown — estimating {filled:.2f} shares dari best_ask ${actual_price:.2f}")

            print(f"✅ MARKET FILLED! {filled:.2f} shares ~${actual_price:.3f}/share | Cost: ${size_usdc:.2f}")
            return {
                "success": True,
                "order_id": order_id, "status": "matched",
                "shares": filled, "price": actual_price,
                "cost": size_usdc, "token_id": token_id,
            }

        else:
            err_msg = resp.get("errorMsg", str(resp))
            print(f"[MARKET ERROR] {err_msg}")
            return {"success": False, "error": f"Market order rejected: {err_msg[:150]}"}

    except Exception as e:
        err = str(e)
        if "couldn't be fully filled" in err or "FOK" in err or "no match" in err.lower():
            return {"success": False, "error": f"Zero liquidity — no sell orders at any price near {price:.2f}"}
        elif "not enough" in err.lower() or "insufficient" in err.lower():
            return {"success": False, "error": "Insufficient pUSD in deposit wallet"}
        elif "timeout" in err.lower() or "timed out" in err.lower():
            return {"success": False, "error": "Polymarket API timeout — retry next window"}
        print(f"[MARKET ERROR] {err[:200]}")
        return {"success": False, "error": f"API error: {err[:120]}"}


# ---------------------------------------------------------------------------

class Mode(Enum):
    PAPER = "paper"
    LIVE  = "live"

class Direction(Enum):
    UP   = "UP"
    DOWN = "DOWN"
    NONE = "NONE"

@dataclass
class WindowState:
    start:           datetime
    ptb:             float
    alerted:         bool      = False   # True only after successful trade
    traded:          bool      = False
    direction:       Direction = Direction.NONE
    entry_price:     float     = 0.0
    size:            float     = 0.0
    final_spread:    float     = 0.0
    result:          str       = ""
    filter_attempts: int       = 0       # how many times filters were checked
    # Live order tracking (CLOB V2)
    _order_id:       str       = ""       # Polymarket order ID
    _is_live:        bool      = False    # True if real order placed
    _order_status:   str       = ""       # "matched" or "live"
    _actual_price:   float     = 0.0      # REAL entry price from live order
    _actual_shares:  float     = 0.0      # REAL shares from live order
    _actual_cost:    float     = 0.0      # REAL cost deducted

@dataclass
class DailyStats:
    date:           str
    trades:         int   = 0
    wins:           int   = 0
    losses:         int   = 0
    profit:         float = 0.0
    max_drawdown:   float = 0.0
    peak_bankroll:  float = 0.0

# ---------------------------------------------------------------------------
# PRICE FETCHERS
# ---------------------------------------------------------------------------

def fetch_btc_binance() -> float:
    try:
        req = urllib.request.Request(BINANCE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return float(json.loads(resp.read())["price"])
    except Exception as e:
        print(f"[WARN] Binance: {e}")
        return 0.0


def fetch_btc_yahoo() -> float:
    try:
        req = urllib.request.Request(YAHOO_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        for c in reversed(closes):
            if c is not None:
                return float(c)
        return 0.0
    except Exception as e:
        print(f"[WARN] Yahoo: {e}")
        return 0.0


def fetch_window_open_price() -> Tuple[float, datetime]:
    """
    Ambil harga BTC via Chainlink — feed yang sama dengan Polymarket.
    Dipakai saat bot restart di tengah window supaya PTB akurat.
    Returns (price, now_utc) atau (0.0, None) jika gagal.
    """
    price = fetch_btc_chainlink()
    if price > 0:
        return price, datetime.now(timezone.utc)
    return 0.0, None


def fetch_btc_chainlink() -> float:
    """
    Baca BTC/USD langsung dari Chainlink aggregator di Polygon via raw JSON-RPC.
    Ini feed yang sama yang Polymarket pakai untuk resolve market — paling akurat.
    Tidak butuh library tambahan, cukup urllib.
    """
    payload = json.dumps({
        "jsonrpc": "2.0", "method": "eth_call",
        "params": [{"to": CHAINLINK_CONTRACT, "data": CHAINLINK_SELECTOR}, "latest"],
        "id": 1,
    }).encode()
    for rpc in POLYGON_RPCS:
        try:
            req = urllib.request.Request(
                rpc, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                result = json.loads(resp.read())
            hex_val = result.get("result", "0x0")
            if not hex_val or hex_val == "0x":
                continue
            price = int(hex_val, 16) / 1e8   # Chainlink 8 decimal places
            if price > 1000:                  # sanity check
                return price
        except Exception as e:
            print(f"[WARN] Chainlink ({rpc}): {e}")
    return 0.0


def get_btc_price() -> float:
    """Chainlink (Polymarket feed) → Binance → Yahoo sebagai fallback."""
    p = fetch_btc_chainlink()
    if p > 0:
        return p
    print("[WARN] Chainlink failed, fallback to Binance")
    p = fetch_btc_binance()
    return p if p > 0 else fetch_btc_yahoo()


# FIX 3: momentum pakai 3-candle slope, bukan 1-candle diff
def fetch_btc_binance_signal() -> Dict:
    """
    Fetch BTC klines dan hitung:
    - volume_ratio  : avg 3 candle COMPLETE terbaru vs 10-candle avg
                      FIX: pakai volumes[-4:-1] bukan volumes[-1]
                      karena candle terakhir masih incomplete (selalu rendah)
    - momentum_pct  : slope 3-candle terbaru vs 3-candle sebelumnya
    """
    try:
        import httpx
        resp = httpx.get(KLINES_URL, timeout=10, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if not data or len(data) < 8:
            return {}

        closes  = [float(c[4]) for c in data]
        volumes = [float(c[5]) for c in data]

        # FIX: gunakan 3 candle complete terakhir (bukan candle[-1] yang masih jalan)
        # volumes[-1] = candle sekarang (incomplete, bisa 0.04x karena baru mulai)
        # volumes[-4:-1] = 3 candle 1m yang sudah tutup sempurna
        recent_complete_vols = volumes[-4:-1]
        current_vol  = sum(recent_complete_vols) / len(recent_complete_vols)
        avg_vol      = sum(volumes[-11:-1]) / 10 if len(volumes) >= 11 else sum(volumes[:-1]) / max(1, len(volumes) - 1)
        volume_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        # 3-candle slope pada candle complete (exclude candle sekarang)
        complete_closes = closes[:-1]  # buang candle terakhir yang masih jalan
        avg_recent = sum(complete_closes[-3:])  / 3
        avg_before = sum(complete_closes[-6:-3]) / 3
        momentum_pct = ((avg_recent - avg_before) / avg_before) * 100 if avg_before > 0 else 0.0

        return {
            "price":          closes[-1],
            "momentum_pct":   momentum_pct,
            "volume_ratio":   volume_ratio,
            "current_volume": current_vol,
            "avg_volume":     avg_vol,
        }
    except Exception as e:
        print(f"[WARN] Binance signal: {e}")
        return {}


# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def send_telegram(msg: str, token: str, chat_id: str):
    try:
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        msg_safe = msg.encode("utf-8", errors="replace").decode("utf-8")
        payload = json.dumps({"chat_id": chat_id, "text": msg_safe, "parse_mode": "HTML"}, ensure_ascii=False).encode("utf-8")
        req     = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        import traceback
        print(f"[WARN] Telegram: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# STRATEGY
# ---------------------------------------------------------------------------

def estimate_entry_price(abs_spread: float, mode: Mode = Mode.PAPER) -> float:
    """
    Estimasi harga entry dari tabel spread.
    FIX 2: tambah SLIPPAGE_BUFFER di live mode — harga real selalu
    lebih mahal dari estimasi karena orderbook tipis saat spread besar.
    """
    if   abs_spread < 50:  base = 0.50
    elif abs_spread < 70:  base = 0.65
    elif abs_spread < 90:  base = 0.70
    elif abs_spread < 110: base = 0.75
    elif abs_spread < 130: base = 0.78
    elif abs_spread < 160: base = 0.83
    elif abs_spread < 200: base = 0.87
    else:                  base = 0.91

    # Di live mode tambah slippage — jangan optimis
    if mode == Mode.LIVE:
        base = min(0.95, base + SLIPPAGE_BUFFER)
    return base


def estimate_win_probability(abs_spread: float) -> float:
    if   abs_spread < 50:  return 0.50
    elif abs_spread < 70:  return 0.72
    elif abs_spread < 90:  return 0.78
    elif abs_spread < 110: return 0.82
    elif abs_spread < 130: return 0.86
    elif abs_spread < 160: return 0.90
    elif abs_spread < 200: return 0.93
    else:                  return 0.95


def calculate_ev(entry_price: float, win_prob: float) -> float:
    return (win_prob * 1.0) - (entry_price * (1 + FEE_RATE))


def get_position_size(bankroll: float, entry_price: float) -> float:
    size     = min(bankroll * 0.20, 3.0)
    max_cost = size * entry_price
    if max_cost > bankroll * 0.85:
        size = (bankroll * 0.85) / entry_price
    # Enforce minimum $1 order
    min_shares = MIN_ORDER_USDC / entry_price
    size = max(size, min_shares)
    return round(size, 2)


# ---------------------------------------------------------------------------
# WINDOW HELPERS
# ---------------------------------------------------------------------------

def get_window_start(now: datetime) -> datetime:
    wn = now.minute // 5
    return now.replace(minute=wn * 5, second=0, microsecond=0)


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# AUTO TRADER
# ---------------------------------------------------------------------------

class AutoTrader:
    def __init__(self, args):
        self.live_mode          = getattr(args, 'live', False)
        self.signal_only        = getattr(args, 'signal', False)
        if self.signal_only:
            self.mode = Mode.PAPER
        elif self.live_mode:
            self.mode = Mode.LIVE
        else:
            self.mode = Mode.PAPER
        self.telegram_token     = args.telegram_token or os.environ.get("TELEGRAM_TOKEN", "")
        self.chat_id            = args.chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.spread_threshold   = args.spread
        self.bankroll           = args.bankroll
        self.initial_bankroll   = args.bankroll
        self.daily_stop         = args.daily_stop
        self.max_trades_per_day = args.max_trades

        self.current_window: Optional[WindowState] = None
        self._pending_live: List[WindowState] = []  # live trades waiting for Polymarket resolution

        # Load today's stats from file if exists (survives restarts)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _stats_file = "/tmp/polylock_daily_stats.json"
        _loaded_stats = None
        try:
            import json as _json
            with open(_stats_file) as _f:
                _saved = _json.load(_f)
            if _saved.get("date") == today_str:
                _loaded_stats = _saved
                print(f"[STATS] Loaded today's stats: {_saved['trades']} trades, P/L ${_saved['profit']:+.2f}")
        except Exception:
            pass

        if _loaded_stats:
            self.daily_stats = DailyStats(
                date=today_str,
                peak_bankroll=_loaded_stats.get("peak_bankroll", self.bankroll),
                trades=_loaded_stats.get("trades", 0),
                wins=_loaded_stats.get("wins", 0),
                losses=_loaded_stats.get("losses", 0),
                profit=_loaded_stats.get("profit", 0.0),
            )
        else:
            self.daily_stats = DailyStats(
                date=today_str,
                peak_bankroll=self.bankroll,
            )
        self.all_time_trades: List[WindowState] = []

        # FIX 1: price history untuk resolve akurat di boundary window
        self.price_history: deque = deque(maxlen=PRICE_HISTORY_SIZE)

        # Live trading state
        self.clob_creds: Optional[Dict]  = None
        self.yes_token:  Optional[str]   = None   # UP token
        self.no_token:   Optional[str]   = None   # DOWN token
        self._private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")

        # Init CLOB jika live mode
        if self.mode == Mode.LIVE:
            self._init_live()

        self._print_banner()

    # ── BANNER ─────────────────────────────────────────────────────────────

    def _print_banner(self):
        mode_label = "🔴 LIVE — REAL MONEY" if self.mode == Mode.LIVE else ("📡 SIGNAL — Telegram alerts + real prices" if self.signal_only else "📄 PAPER — no real money")
        # Tulis mode ke stats.json saat startup
        log_stats(self.daily_stats, self.bankroll, self.mode.value)
        self._notify(f"""
{'='*55}
  ₿ POLYMARKET BTC 5m LOCK BOT — $50 THRESHOLD
{'='*55}
  Mode:           {"SIGNAL" if self.signal_only else self.mode.value.upper()}
  Bankroll:       ${self.bankroll:.2f}
  Spread Target:  ${self.spread_threshold}
  Daily Stop:     ${self.daily_stop}
  Telegram:       {'ENABLED' if self.telegram_token else 'OFF'}
{'='*55}
  ⚠️  {mode_label}
{'='*55}
""")

    # ── LIVE INIT ──────────────────────────────────────────────────────────

    def _init_live(self):
        """Setup CLOB credentials dan fetch market tokens untuk live trading."""
        if self.signal_only:
            print("📡 SIGNAL-ONLY MODE — Telegram alerts with real Polymarket prices, no orders")
            return
        if not self._private_key:
            print("❌ POLYMARKET_PRIVATE_KEY not set! Falling back to PAPER.")
            self.mode = Mode.PAPER
            return

        print("🔐 Initializing live trading...")

        # 1. CLOB credentials — derive via SDK (ini satu2nya yg valid)
        self.clob_creds = load_or_create_clob_creds(self._private_key)
        if not self.clob_creds:
            print("⚠️  CLOB auth failed — falling back to PAPER mode")
            self.mode = Mode.PAPER
            return

        # Init reusable ClobClient
        from py_clob_client_v2 import ClobClient, ApiCreds
        from py_clob_client_v2.constants import POLYGON
        api_creds_obj = ApiCreds(
            api_key=self.clob_creds["api_key"],
            api_secret=self.clob_creds["api_secret"],
            api_passphrase=self.clob_creds["api_passphrase"],
        )
        self.clob_client = ClobClient(
            host=CLOB_HOST, chain_id=POLYGON,
            key=self._private_key, creds=api_creds_obj,
        )
        print("🤖 ClobClient initialized (EOA)")

        # 2. Market tokens — akan di-fetch ulang per window saat LOCK trigger
        print("✅ Live ready! Tokens akan di-fetch per window saat LOCK.")

        # 3. Compute deposit wallet & fetch balance via SDK (POLY_1271)
        from web3 import Web3
        import ssl, urllib3
        ssl._create_default_https_context = ssl._create_unverified_context
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        eoa = Web3().eth.account.from_key(self._private_key).address
        self.deposit_wallet = compute_deposit_wallet_address(eoa)
        print(f"🏦 Deposit Wallet: {self.deposit_wallet}")
        
        try:
            from py_clob_client_v2 import SignatureTypeV2
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
            
            # Create client with POLY_1271 for balance check
            bal_client = ClobClient(
                host=CLOB_HOST, chain_id=POLYGON,
                key=self._private_key, creds=api_creds_obj,
                signature_type=SignatureTypeV2.POLY_1271,
                funder=self.deposit_wallet,
            )
            
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            bal_data = bal_client.get_balance_allowance(params)
            raw_balance = float(bal_data.get("balance", 0))
            
            # Balance in pUSD smallest units (6 decimals)
            actual_balance = raw_balance / 1_000_000 if raw_balance > 1000 else raw_balance
            
            if actual_balance > 0:
                if self.bankroll > 0 and actual_balance < self.bankroll:
                    # --bankroll explicitly set and real balance is lower
                    # Likely open positions locking funds — use --bankroll as floor
                    print(f"💰 Polymarket pUSD: ${actual_balance:,.2f} (open positions may lock funds)")
                    print(f"    Using --bankroll ${self.bankroll:.2f} as floor")
                else:
                    # Default: always trust real Polymarket balance
                    print(f"💰 Polymarket pUSD: ${actual_balance:,.2f} — bankroll set")
                    self.bankroll = actual_balance
                self.initial_bankroll = self.bankroll
            else:
                if self.bankroll <= 0:
                    print(f"⚠️  No balance found and no --bankroll set — defaulting to $10")
                    self.bankroll = 10.0
                else:
                    print(f"⚠️  No balance on deposit wallet — using --bankroll ${self.bankroll:.2f}")
                self.initial_bankroll = self.bankroll
        except Exception as e:
            print(f"[WARN] SDK balance check: {e}")
            # Retry once after 5s
            import time as _time
            _time.sleep(5)
            try:
                bal_data = bal_client.get_balance_allowance(params)
                raw_balance = float(bal_data.get("balance", 0))
                actual_balance = raw_balance / 1_000_000 if raw_balance > 1000 else raw_balance
                if actual_balance > 0:
                    if self.bankroll <= 0 or actual_balance >= self.bankroll:
                        self.bankroll = actual_balance
                    self.initial_bankroll = self.bankroll
                    print(f"💰 Retry OK: Polymarket pUSD ${actual_balance:,.2f}")
            except Exception as e2:
                print(f"[WARN] Retry balance check failed: {e2}")
                if self.bankroll <= 0:
                    print(f"⚠️  Bankroll still 0 — defaulting to $10 (set --bankroll to override)")
                    self.bankroll = 10.0
                self.initial_bankroll = self.bankroll
            self.initial_bankroll = self.bankroll
    # ── NOTIFY ─────────────────────────────────────────────────────────────

    def _notify(self, msg: str):
        print(msg)
        if self.telegram_token and self.chat_id:
            send_telegram(msg, self.telegram_token, self.chat_id)

    # ── TELEGRAM COMMAND HANDLER ─────────────────────────────────
    def _poll_telegram_commands(self):
        """Poll Telegram for new commands every cycle."""
        if not self.telegram_token or not self.chat_id:
            return
        if not hasattr(self, '_tg_last_update'):
            self._tg_last_update = 0
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates"
            params = f"?offset={self._tg_last_update + 1}&timeout=1"
            req = urllib.request.Request(url + params)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            
            for update in data.get("result", []):
                self._tg_last_update = max(self._tg_last_update, update["update_id"])
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                from_id = str(msg.get("from", {}).get("id", ""))
                
                # Only respond to authorized chat
                if from_id != str(self.chat_id):
                    continue
                
                if text.startswith("/"):
                    self._handle_command(text)
        except Exception as e:
            pass  # silent fail, retry next cycle

    def _handle_command(self, cmd: str):
        """Handle Telegram commands."""
        cmd = cmd.lstrip("/").split("@")[0].split()[0] if cmd else ""
        
        if cmd in ("status", "start"):
            wr = self.daily_stats.wins * 100 / max(1, self.daily_stats.trades)
            uptime = (datetime.now(timezone.utc) - self._start_time).total_seconds() / 3600 if hasattr(self, '_start_time') else 0
            self._notify(
                f"🤖 <b>BOT STATUS</b>\n"
                f"Mode: <code>{self.mode.value}</code>\n"
                f"Uptime: <code>{uptime:.1f}h</code>\n"
                f"Bankroll: <code>${self.bankroll:.2f}</code>\n"
                f"Today Trades: <code>{self.daily_stats.trades}</code>\n"
                f"W/L: <code>{self.daily_stats.wins}/{self.daily_stats.losses}</code> ({wr:.0f}%)\n"
                f"P/L: <code>${self.daily_stats.profit:+.2f}</code>\n"
                f"Filter: spread≥<code>{self.spread_threshold}</code>, price <code>0.40-{MAX_ENTRY_PRICE:.2f}</code>"
            )
        
        elif cmd == "pnl":
            # Calculate PnL stats from all_time_trades
            resolved = [t for t in self.all_time_trades if t.result in ("WIN", "LOSS")]
            wins = [t for t in resolved if t.result == "WIN"]
            losses = [t for t in resolved if t.result == "LOSS"]
            total_pnl = sum(getattr(t, 'profit', 0) or 0 for t in resolved)
            wr = len(wins) * 100 / max(1, len(resolved))
            
            # Direction breakdown
            up_trades = [t for t in resolved if t.direction == Direction.UP]
            down_trades = [t for t in resolved if t.direction == Direction.DOWN]
            up_wr = sum(1 for t in up_trades if t.result=="WIN") * 100 / max(1, len(up_trades))
            down_wr = sum(1 for t in down_trades if t.result=="WIN") * 100 / max(1, len(down_trades))
            
            self._notify(
                f"💰 <b>PnL REPORT</b>\n"
                f"Bankroll: <code>${self.bankroll:.2f}</code>\n"
                f"Total Trades: <code>{len(resolved)}</code>\n"
                f"Wins: <code>{len(wins)}</code> | Losses: <code>{len(losses)}</code>\n"
                f"Win Rate: <code>{wr:.0f}%</code>\n"
                f"Total P/L: <code>${total_pnl:+.2f}</code>\n\n"
                f"📊 <b>By Direction:</b>\n"
                f"⬆️ UP: <code>{len(up_trades)}</code> trades ({up_wr:.0f}% WR)\n"
                f"⬇️ DOWN: <code>{len(down_trades)}</code> trades ({down_wr:.0f}% WR)\n\n"
                f"<b>Today:</b> <code>{self.daily_stats.trades}</code> trades, <code>${self.daily_stats.profit:+.2f}</code>"
            )
        
        elif cmd == "positions":
            pending = self._pending_live if hasattr(self, '_pending_live') else []
            current = self.current_window
            
            msg = f"📌 <b>POSITIONS</b>\n\n"
            
            if current and current.traded:
                spread = current.final_spread or 0
                msg += (
                    f"<b>Active:</b>\n"
                    f"  {current.start.strftime('%H:%M')} {current.direction.value}\n"
                    f"  Entry: ${current.entry_price:.2f} x {current.size} shares\n"
                    f"  Spread: {spread:+.2f}\n\n"
                )
            
            if pending:
                msg += f"<b>Pending ({len(pending)}):</b>\n"
                for pw in pending[-5:]:
                    msg += f"  {pw.start.strftime('%H:%M')} {pw.direction.value} @ ${pw.entry_price:.2f}\n"
            elif not (current and current.traded):
                msg += "<i>No active positions</i>\n"
            
            self._notify(msg)
        
        elif cmd == "balance":
            from_clob = None
            if self.mode == Mode.LIVE and self.clob_creds:
                try:
                    from_clob = fetch_polymarket_balance(self._private_key, self.clob_creds)
                except: pass
            
            msg = f"💵 <b>BALANCE</b>\nBot: <code>${self.bankroll:.2f}</code>"
            if from_clob is not None:
                drift = from_clob - self.bankroll
                msg += f"\nReal: <code>${from_clob:.2f}</code>\nDrift: <code>{drift:+.2f}</code>"
            self._notify(msg)
        
        elif cmd == "trades":
            resolved = [t for t in self.all_time_trades if t.result in ("WIN", "LOSS")]
            last_n = resolved[-10:]
            
            msg = f"📜 <b>LAST {len(last_n)} TRADES</b>\n\n"
            for t in last_n:
                emoji = "✅" if t.result == "WIN" else "❌"
                profit = getattr(t, 'profit', 0) or 0
                msg += f"{emoji} {t.start.strftime('%H:%M')} {t.direction.value} @ ${t.entry_price:.2f} | {profit:+.2f}\n"
            
            self._notify(msg or "No trades yet")
        
        elif cmd == "pause":
            self._manual_pause = True
            self._notify("⏸️ <b>BOT PAUSED</b>\nUse /resume to continue trading")
        
        elif cmd == "resume":
            self._manual_pause = False
            self._notify("▶️ <b>BOT RESUMED</b>")
        
        elif cmd == "help":
            self._notify(
                "📖 <b>COMMANDS</b>\n"
                "/status - Bot health & today stats\n"
                "/pnl - Full PnL breakdown\n"
                "/positions - Active & pending trades\n"
                "/balance - Bot vs real balance\n"
                "/trades - Last 10 trades\n"
                "/pause - Stop trading\n"
                "/resume - Resume trading\n"
                "/help - This menu"
            )

    def _send_hourly_report(self):
        """Send auto report every hour."""
        now = datetime.now(timezone.utc)
        if not hasattr(self, '_last_hourly_report'):
            self._last_hourly_report = now
            return
        
        # Check if 1 hour passed
        elapsed = (now - self._last_hourly_report).total_seconds()
        if elapsed < 3600:
            return
        
        self._last_hourly_report = now
        
        wr = self.daily_stats.wins * 100 / max(1, self.daily_stats.trades)
        recent = self._recent_results[-5:] if hasattr(self, '_recent_results') else []
        recent_str = " ".join(["✅" if r=="WIN" else "❌" for r in recent]) or "none"
        
        self._notify(
            f"⏰ <b>HOURLY REPORT</b>\n"
            f"Bankroll: <code>${self.bankroll:.2f}</code>\n"
            f"Today: <code>{self.daily_stats.trades}</code> trades, "
            f"<code>{self.daily_stats.wins}W/{self.daily_stats.losses}L</code> ({wr:.0f}%)\n"
            f"P/L: <code>${self.daily_stats.profit:+.2f}</code>\n"
            f"Last 5: {recent_str}"
        )

    # ── DAILY RESET ────────────────────────────────────────────────────────

    def _check_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.daily_stats.date:
            wr = self.daily_stats.wins*100/max(1,self.daily_stats.trades)
            summary = (
                f"📅 *Day Summary:* {self.daily_stats.date}\n"
                f"   Trades: `{self.daily_stats.trades}` | W/L: `{self.daily_stats.wins}/{self.daily_stats.losses}`\n"
                f"   Win Rate: `{wr:.0f}%`\n"
                f"   P/L: `${self.daily_stats.profit:+.2f}` | Peak: `${self.daily_stats.peak_bankroll:.2f}`"
            )
            self._notify(summary)
            self.daily_stats = DailyStats(date=today, peak_bankroll=self.bankroll)

    def _check_anomaly(self):
        """Alert if 3 losses in row, pause if 5."""
        if not hasattr(self, '_recent_results'):
            self._recent_results = []
        # Keep last 5
        self._recent_results = self._recent_results[-5:]
        
        if len(self._recent_results) >= 3 and all(r == 'LOSS' for r in self._recent_results[-3:]):
            self._notify(f"⚠️ <b>3 LOSSES IN ROW</b>\nBankroll: ${self.bankroll:.2f}\nConsider review or adjustment.")
        
        if len(self._recent_results) >= 5 and all(r == 'LOSS' for r in self._recent_results[-5:]):
            self._notify(f"🚨 <b>5 LOSSES IN ROW — AUTO PAUSE</b>\nBankroll: ${self.bankroll:.2f}")
            return True
        return False

    # ── RISK CONTROLS ──────────────────────────────────────────────────────

    def _should_stop_trading(self) -> bool:
        if self.daily_stats.profit <= -self.daily_stop:
            return True
        if self.bankroll < 1.0:
            return True
        # Check anomaly (3+ losses in row)
        if self._check_anomaly():
            return True
        return False

    # ── FIX 1: BOUNDARY RESOLVE ────────────────────────────────────────────

    def _get_boundary_price(self, window_end: datetime) -> Optional[float]:
        """
        Cari harga terdekat dengan waktu window_end dari history.
        Ini menghindari error resolve karena harga fetch yang telat 5 detik.
        """
        if not self.price_history:
            return None
        best_price = None
        best_delta = float("inf")
        for ts, price in self.price_history:
            delta = abs((ts - window_end).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best_price = price
        return best_price

    # ── FIX 3: SIGNAL FILTERS (3-CANDLE MOMENTUM) ──────────────────────────

    def _check_signal_filters(self, direction: Direction, abs_spread: float, entry_price: float) -> Tuple[bool, str]:
        signal = fetch_btc_binance_signal()
        if not signal:
            print("[SIGNAL] No data — allowing trade (fallback)")
            return True, "fallback"

        price     = signal.get("price", 0.0)
        vol_ratio = signal.get("volume_ratio", 1.0)
        momentum  = signal.get("momentum_pct", 0.0)  # now 3-candle slope

        print(f"[SIGNAL] ${price:,.2f} | Vol: {vol_ratio:.2f}x | Mom(3c): {momentum:+.4f}%")

        if vol_ratio < MIN_VOLUME_RATIO:
            return False, f"LOW_VOLUME ({vol_ratio:.2f}x)"

        # ── ANTI-TREND FILTER (avoid betting against established trend) ──
        # If momentum > 0.05 and bot wants UP → OK (with trend)
        # If momentum > 0.05 and bot wants DOWN → BLOCK (counter-trend)
        # Same for opposite
        if direction == Direction.UP and momentum < -0.05:
            return False, f"COUNTER_TREND (BTC dropping {momentum:.4f}%, but UP signal)"

        if direction == Direction.DOWN and momentum > 0.05:
            return False, f"COUNTER_TREND (BTC rising +{momentum:.4f}%, but DOWN signal)"

        if direction == Direction.DOWN and momentum > MOMENTUM_THRESHOLD:
            return False, f"COUNTER_MOMENTUM_UP (+{momentum:.4f}%)"

        if direction == Direction.UP and momentum < -MOMENTUM_THRESHOLD:
            return False, f"COUNTER_MOMENTUM_DOWN ({momentum:.4f}%)"

        win_prob = estimate_win_probability(abs_spread)
        # NOTE: EV recalculated with real price in _execute_trade()
        # This is preliminary EV using signal estimate
        ev       = calculate_ev(entry_price, win_prob)
        print(f"[SIGNAL] WinProb: {win_prob:.0%} | EV(est): ${ev:.4f}/share")

        if ev < EV_THRESHOLD:
            return False, f"NEGATIVE_EV (${ev:.4f})"

        return True, "ok"

    # ── EXECUTE ────────────────────────────────────────────────────────────

    def _execute_trade(self, direction: Direction, window: WindowState, entry_price: float, size: float, abs_spread: float = 0.0) -> bool:
        cost       = size * entry_price
        fee        = cost * FEE_RATE
        total_cost = cost + fee

        if total_cost > self.bankroll:
            print(f"[WARN] Insufficient funds: need ${total_cost:.2f}, have ${self.bankroll:.2f}")
            return False

        # ── SIGNAL NOTIFY ──────────────────────────────────────────────────
        if self.mode == Mode.LIVE or self.signal_only:
            spread = abs_spread or abs(window.final_spread or 0)

            # Fetch harga REAL dari Polymarket sebelum eksekusi order
            up_price, down_price = fetch_polymarket_real_prices(window.start)
            real_price = up_price if direction == Direction.UP else down_price
            print(f"[PRICE CHECK] UP={up_price:.2f} DOWN={down_price:.2f} | Signal={direction.value}")

            MIN_PRICE = 0.40  # terlalu murah = market tidak yakin direction ini

            # Kalau harga real tidak bisa di-fetch (0.0), BLOK eksekusi.
            # Tanpa verifikasi harga, bot bisa fill di 93-99¢ tanpa sadar.
            if real_price <= 0:
                msg_noprice = (
                    f"⚠️ <b>SIGNAL SKIPPED</b>\n"
                    f"Direction: <b>{direction.value}</b>\n"
                    f"Reason: <i>Harga real Polymarket tidak tersedia — skip untuk hindari fill mahal</i>"
                )
                print(f"[SKIP] Cannot verify real price — aborting to avoid expensive fill")
                if self.telegram_token and self.chat_id:
                    send_telegram(msg_noprice, self.telegram_token, self.chat_id)
                return False

            if real_price < MIN_PRICE:
                msg_skip = (
                    f"⚠️ <b>SIGNAL SKIPPED</b>\n"
                    f"Direction {direction.value} tapi harga real hanya <code>{real_price:.2f}</code> ({int(real_price*100)}¢)\n"
                    f"Market tidak confident → skip"
                )
                print(f"[SKIP] {direction.value} price too low: {real_price:.2f}")
                if self.telegram_token and self.chat_id:
                    send_telegram(msg_skip, self.telegram_token, self.chat_id)
                return False

            if real_price > MAX_ENTRY_PRICE:
                if self.telegram_token and self.chat_id:
                    send_telegram(
                        f"⚠️ <b>SIGNAL SKIPPED</b>\n"
                        f"Direction: <b>{direction.value}</b> | Price: <code>{real_price:.2f}</code> ({int(real_price*100)}¢)\n"
                        f"Reason: <i>Price >{int(MAX_ENTRY_PRICE*100)}¢ — profit margin terlalu tipis</i>\n"
                        f"Risk/reward: win <code>+${(1-real_price):.2f}</code> vs loss <code>-${real_price:.2f}</code>",
                        self.telegram_token, self.chat_id
                    )
                print(f"[SKIP] {direction.value} price too high: {real_price:.2f} > MAX {MAX_ENTRY_PRICE:.2f}")
                return False

            # Pakai harga real yang sudah diverifikasi
            actual_price = real_price
            bet_usdc     = max(cost, MIN_ORDER_USDC)
            profit_est   = round(bet_usdc / actual_price * (1 - FEE_RATE) - bet_usdc, 2)
            win_prob     = estimate_win_probability(abs(spread))
            real_ev      = calculate_ev(actual_price, win_prob)
            print(f"[EV] Real price {actual_price:.2f} → EV: ${real_ev:.4f}/share (was est: ${calculate_ev(entry_price, win_prob):.4f})")
            emoji        = "🟢" if direction == Direction.UP else "🔴"
            action       = "BUY YES" if direction == Direction.UP else "BUY NO"
            ts           = int(window.start.timestamp())
            slug         = f"btc-updown-5m-{ts}"
            market_url   = f"https://polymarket.com/event/{slug}"

            # Warning kalau harga agak rendah (30-45¢)
            price_warn = " ⚠️ harga rendah, pertimbangkan skip" if real_price < 0.45 else ""

            msg = (
                f"{emoji} <b>LOCK SIGNAL — BTC 5m</b>\n"
                f"Action    : <b>{action}</b>\n"
                f"─────────────────\n"
                f"Harga Real : <code>{actual_price:.2f}</code> ({int(actual_price*100)}¢){price_warn}\n"
                f"Bet        : <code>${bet_usdc:.2f} USDC</code>\n"
                f"Est. Profit: <code>+${profit_est:.2f}</code> jika menang\n"
                f"Win Prob   : <code>{int(win_prob*100)}%</code>\n"
                f"Spread     : <code>${spread:.0f}</code>\n"
                f"Window     : <code>{format_time(window.start)}</code>\n"
                f"─────────────────\n"
                f"<a href=\"{market_url}\">🔗 Buka Market</a>"
            )
            print(f"\n{'='*50}")
            print(f"[SIGNAL] {direction.value} | real price {actual_price:.2f} | spread ${spread:.0f}")
            print(f"{'='*50}")
            if self.telegram_token and self.chat_id:
                send_telegram(msg, self.telegram_token, self.chat_id)
                print("[TELEGRAM] Notif terkirim ✅")

            # ── LIVE ORDER ────────────────────────────────────────────
            if self.mode == Mode.LIVE and not self.signal_only:
                print(f"\n{'='*50}")
                print(f"[LIVE ORDER] Fetching token + placing order...")
                print(f"{'='*50}")
                self.yes_token, self.no_token = fetch_btc_5m_market_tokens(window.start)
                token = self.yes_token if direction == Direction.UP else self.no_token
                if token:
                    print(f"[LIVE ORDER] Token: {token[:20]}... | {direction.value}")
                    print(f"[LIVE ORDER] MARKET BUY ${bet_usdc:.2f} | Signal: {actual_price:.2f}")
                    order_result = place_live_order(self._private_key, self.clob_creds, token, bet_usdc, actual_price, self.deposit_wallet)
                    if order_result.get("success"):
                        # ✅ MARKET ORDER FILLED — use real fill details
                        real_shares = order_result["shares"]
                        real_cost = order_result["cost"]
                        fill_price = order_result.get("price", actual_price)
                        window._order_id = order_result["order_id"]
                        window._is_live = True
                        window._actual_price = fill_price
                        window._actual_shares = real_shares
                        window._actual_cost = real_cost
                        window.entry_price = fill_price
                        window.size = real_shares

                        msg_live = (
                            f"🤖 <b>AUTO-TRADE EXECUTED</b>\n"
                            f"Type    : <b>MARKET ORDER</b>\n"
                            f"Action  : <b>{action}</b>\n"
                            f"Amount  : <code>${real_cost:.2f} USDC</code>\n"
                            f"Filled  : <code>{real_shares:.1f} shares</code> @ ~<code>{fill_price:.2f}</code>/share\n"
                            f"ID  : <code>{order_result['order_id'][:20]}...</code>\n"
                            f"<a href=\"{market_url}\">🔗 Buka Market</a>"
                        )
                        if self.telegram_token and self.chat_id:
                            send_telegram(msg_live, self.telegram_token, self.chat_id)
                        print("[LIVE ORDER] ✅ Order submitted!")
                    else:
                        error_reason = order_result.get("error", "Unknown error")
                        if self.telegram_token and self.chat_id:
                            send_telegram(
                                f"❌ <b>AUTO-TRADE FAILED</b>\n"
                                f"Action: <b>{action}</b> @ <code>{actual_price:.2f}</code>\n"
                                f"Bet: <code>${bet_usdc:.2f} USDC</code>\n"
                                f"Reason: <i>{error_reason}</i>",
                                self.telegram_token, self.chat_id
                            )
                        print(f"[LIVE ORDER] ❌ FAILED — {error_reason}")
                        return False  # ← Don't track failed orders!
                else:
                    print("[LIVE ORDER] ❌ Token not found")
                    return False

        # ── PAPER / Record ─────────────────────────────────────────────────
        else:
            print(f"\n{'='*50}")
            print(f"[PAPER TRADE] BUY {size} shares @ {entry_price:.2f}")
            print(f"  Direction: {direction.value} | Cost: ${total_cost:.2f} | Fee: ${fee:.2f}")
            print(f"{'='*50}\n")
            window.entry_price = entry_price
            window.size = size

        window.traded      = True
        window.direction   = direction
        self.daily_stats.trades += 1
        # Market orders: always deduct (order is filled immediately)
        deduct = window._actual_cost if window._is_live else total_cost
        self.bankroll -= deduct
        print(f"[TRADE] Bankroll: ${self.bankroll:.2f}")
        window.result = "PENDING"
        log_window(window)
        log_stats(self.daily_stats, self.bankroll, self.mode.value)
        return True

    # ── FIX 1: RESOLVE WITH BOUNDARY PRICE ─────────────────────────────────

    def _check_polymarket_resolution(self, window: WindowState) -> Optional[bool]:
        """Check actual Polymarket market resolution for a live trade.
        Returns True if trade won, False if lost, None if still unresolved.
        Uses outcomePrices field: ["1","0"] = YES/UP won, ["0","1"] = NO/DOWN won."""
        try:
            ts = int(window.start.timestamp())
            slug = f"btc-updown-5m-{ts}"
            # CORRECT endpoint: /markets/slug/{slug} (NOT /markets?slug=)
            url = f"{GAMMA_HOST}/markets/slug/{slug}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            if not data or not data.get("conditionId"):
                return None  # Market not found yet

            closed = data.get("closed", False)
            outcome_prices = data.get("outcomePrices", [])

            if not closed or not outcome_prices or len(outcome_prices) < 2:
                return None  # Still pending resolution

            # outcomePrices[0] = YES/UP token payout, outcomePrices[1] = NO/DOWN token payout
            # e.g. ["1","0"] = UP won, ["0","1"] = DOWN won
            up_won = (str(outcome_prices[0]) == "1")

            if up_won:
                return window.direction == Direction.UP
            else:
                return window.direction == Direction.DOWN

        except Exception as e:
            # 404 = market not found / slug format wrong
            if "404" in str(e) or "not found" in str(e).lower():
                return None
            print(f"[RESOLVE] API check error: {e}")
            return None

    def _resolve_window(self, window: WindowState, fallback_price: float):
        # Cari harga closest ke detik :00 dari window berikutnya
        window_end   = window.start.replace(
            minute=(window.start.minute // 5) * 5,
            second=0, microsecond=0
        )
        # window_end = start + 5 menit
        from datetime import timedelta
        window_end = window.start + timedelta(minutes=5)
        boundary_price = self._get_boundary_price(window_end) or fallback_price

        window.final_spread = boundary_price - window.ptb

        if not window.traded:
            if not window.result:
                window.result = "SKIP"
            log_window(window)
            return

        # ── LIVE ORDER: Polymarket API resolution ──────────────────
        if window._is_live and window._order_id:
            pm_won = self._check_polymarket_resolution(window)
            if pm_won is not None:
                won = pm_won  # ✅ Use Polymarket resolution
            else:
                # ⚠️ API not available yet — defer resolution, do NOT fallback to Chainlink!
                print(f"[RESOLVE] Market {window.start.strftime('%H:%M')} not yet resolved on Polymarket — deferring")
                window.result = "PENDING"
                log_window(window)
                if window not in self._pending_live:
                    self._pending_live.append(window)
                return
        else:
            # Paper / fallback: use Chainlink boundary price
            won = (
                (window.direction == Direction.DOWN and window.final_spread < 0) or
                (window.direction == Direction.UP   and window.final_spread > 0)
            )

        # Use last known BTC price as fallback if boundary_price is 0
        if boundary_price == 0.0 and fallback_price > 0:
            boundary_price = fallback_price
        self._finalize_window_result(window, won, boundary_price)

    def _finalize_window_result(self, window: WindowState, won: bool, boundary_price: float = 0.0):
        """Apply win/loss to bankroll and log results."""
        # Use REAL numbers for live trades, estimated for paper
        cost       = window._actual_cost if window._is_live else window.size * window.entry_price
        fee        = cost * FEE_RATE
        total_cost = cost + fee

        if won:
            # FIX 2: Hitung payout dari actual cost & actual price, bukan dari window.size
            # yang bisa salah kalau fill amount tidak tersedia saat order.
            if window._is_live and window._actual_price > 0 and window._actual_cost > 0:
                real_shares = window._actual_cost / window._actual_price
            else:
                real_shares = window.size
            payout = real_shares * 1.0
            profit = payout - total_cost
            window.profit = profit  # ← UPDATE LOG
            self.daily_stats.wins   += 1
            self.daily_stats.profit += profit
            self.bankroll           += payout
            window.result = "WIN"
            if hasattr(self, '_recent_results'): self._recent_results.append('WIN')
            else: self._recent_results = ['WIN']
            source_tag = "[Polymarket]" if window._is_live else ""
            msg = (
                f"✅ *LOCK WIN* 🎉 {source_tag}\n"
                f"Window: `{window.start.strftime('%H:%M:%S')}` | Dir: *{window.direction.value}*\n"
                f"PTB: `${window.ptb:,.2f}` → Final: `${boundary_price:,.2f}`\n"
                f"Entry: `{window.entry_price:.2f}` | Size: `{window.size}` shares\n"
                f"Profit: `+${profit:.2f}` | Bankroll: `${self.bankroll:.2f}`"
            )
        else:
            loss = total_cost
            window.profit = -loss  # ← UPDATE LOG
            self.daily_stats.losses += 1
            self.daily_stats.profit -= loss
            window.result = "LOSS"
            if hasattr(self, '_recent_results'): self._recent_results.append('LOSS')
            else: self._recent_results = ['LOSS']
            source_tag = "[Polymarket]" if window._is_live else ""
            msg = (
                f"❌ *LOCK LOSS* {source_tag}\n"
                f"Window: `{window.start.strftime('%H:%M:%S')}` | Dir: *{window.direction.value}*\n"
                f"PTB: `${window.ptb:,.2f}` → Final: `${boundary_price:,.2f}`\n"
                f"Entry: `{window.entry_price:.2f}` | Size: `{window.size}` shares\n"
                f"Lost: `-${loss:.2f}` | Bankroll: `${self.bankroll:.2f}`"
            )

        self.daily_stats.peak_bankroll = max(self.daily_stats.peak_bankroll, self.bankroll)
        print(f"\n{'='*50}")
        print(msg.replace('*', '').replace('`', ''))
        print(f"{'='*50}\n")
        self._notify(msg)

        # Save daily stats to file (survives restart)
        try:
            import json as _json
            with open("/tmp/polylock_daily_stats.json", "w") as _f:
                _json.dump({
                    "date":         self.daily_stats.date,
                    "trades":       self.daily_stats.trades,
                    "wins":         self.daily_stats.wins,
                    "losses":       self.daily_stats.losses,
                    "profit":       round(self.daily_stats.profit, 4),
                    "peak_bankroll": round(self.daily_stats.peak_bankroll, 4),
                }, _f)
        except Exception as _e:
            print(f"[WARN] Could not save daily stats: {_e}")

        # ── Balance sync — only when NO open positions ──
        has_open = bool(getattr(self, '_pending_live', []))
        # Also check if current window has an unresolved live trade
        current = getattr(self, 'current_window', None)
        if current and current.traded and current.result == "PENDING":
            has_open = True
        if self.mode == Mode.LIVE and self.clob_creds and not has_open:
            real = fetch_polymarket_balance(self._private_key, self.clob_creds)
            if real and real > 0:
                old_bankroll = self.bankroll
                diff = real - old_bankroll
                if abs(diff) > 0.05:  # Only sync if difference > 5 cents
                    print(f"[SYNC] Bankroll drift: ${old_bankroll:.2f} → ${real:.2f} (Δ={diff:+.2f})")
                    self.bankroll = real
                    self._notify(
                        f"🔄 <b>BALANCE SYNC</b>\n"
                        f"Bot: <code>${old_bankroll:.2f}</code> → Real: <code>${real:.2f}</code>\n"
                        f"Drift: <code>{diff:+.2f}</code>"
                    )
        elif has_open:
            print(f"[SYNC] Skipped — {len(getattr(self, '_pending_live', []))} open position(s)")
        # Persist hasil final ke file — overwrite baris PENDING
        log_window(window)
        log_stats(self.daily_stats, self.bankroll, self.mode.value)

    # ── MAIN LOOP ──────────────────────────────────────────────────────────

    def run(self):
        print("💡 PolyLock Bot running. Press Ctrl+C to stop.\n")
        self._start_time = datetime.now(timezone.utc)
        self._notify("🤖 <b>Bot ONLINE</b>\nType /help for commands")

        while True:
            now = datetime.now(timezone.utc)
            self._check_new_day()
            self._poll_telegram_commands()
            self._send_hourly_report()

            # Manual pause check
            if getattr(self, '_manual_pause', False):
                time.sleep(10)
                continue

            if self._should_stop_trading():
                if not getattr(self, '_stopped_notified', False):
                    self._notify(
                        f"🚫 *STOPPED* — Daily limit hit.\n"
                        f"Trades: {self.daily_stats.trades} | P/L: ${self.daily_stats.profit:+.2f}"
                    )
                    self._stopped_notified = True
                time.sleep(60)
                continue
            else:
                self._stopped_notified = False

            btc_price = get_btc_price()
            if btc_price == 0:
                print(f"[{format_time(now)}] Price fetch failed. Retrying...")
                time.sleep(CHECK_INTERVAL)
                continue

            # FIX 1: simpan setiap sample ke history
            self.price_history.append((now, btc_price))

            window_start  = get_window_start(now)
            seconds_into  = (now - window_start).total_seconds()

            # ── New window ─────────────────────────────────────────────────
            if self.current_window is None or self.current_window.start != window_start:
                if self.current_window:
                    self._resolve_window(self.current_window, btc_price)
                    self.all_time_trades.append(self.current_window)

                # ── Retry pending live resolutions ────────────────────
                if self._pending_live:
                    resolved_now = []
                    now_ts = time.time()
                    for pw in self._pending_live:
                        # Try to resolve from Polymarket
                        result = self._check_polymarket_resolution(pw)
                        if result is not None:
                            pw.final_spread = btc_price - pw.ptb
                            self._finalize_window_result(pw, result)
                            # FIX 3: Cek duplikat sebelum append — window PENDING sudah
                            # di-append saat pertama kali diproses di main loop.
                            if pw not in self.all_time_trades:
                                self.all_time_trades.append(pw)
                            resolved_now.append(pw)
                            print(f"[PENDING] Resolved deferred window {pw.start.strftime('%H:%M')}: {'WIN ✅' if result else 'LOSS ❌'}")
                        # Force resolve if >30min pending (stale)
                        elif (now_ts - pw.start.timestamp()) > 1800:
                            pw.final_spread = btc_price - pw.ptb
                            fallback_result = (
                                (pw.direction == Direction.DOWN and pw.final_spread < 0) or
                                (pw.direction == Direction.UP and pw.final_spread > 0)
                            )
                            self._finalize_window_result(pw, fallback_result)
                            if pw not in self.all_time_trades:
                                self.all_time_trades.append(pw)
                            resolved_now.append(pw)
                            print(f"[PENDING] Stale trade {pw.start.strftime('%H:%M')} — force resolved (Chainlink): {'WIN ✅' if fallback_result else 'LOSS ❌'}")
                    for pw in resolved_now:
                        self._pending_live.remove(pw)

                ptb = btc_price
                if seconds_into > 5:
                    # Bot restart di tengah window — PTB tidak bisa diambil dari harga sekarang
                    # Pakai Chainlink (feed Polymarket) sebagai best-effort, tandai sebagai estimasi
                    cl_price, _ = fetch_window_open_price()
                    if cl_price > 0:
                        ptb = cl_price
                    print(f"\n🌅 New window: {format_time(window_start)} | PTB: ${ptb:,.2f} (chainlink est., bot joined +{int(seconds_into)}s late)")
                else:
                    print(f"\n🌅 New window: {format_time(window_start)} | PTB: ${ptb:,.2f}")

                self.current_window = WindowState(start=window_start, ptb=ptb)

            w          = self.current_window
            spread     = btc_price - w.ptb
            abs_spread = abs(spread)
            direction  = Direction.UP if spread > 0 else Direction.DOWN
            # Only update direction if not yet traded — preserve direction after execution
            if not w.traded:
                w.direction = direction

            # Log every minute
            if int(seconds_into) % 60 == 0:
                print(f"[{format_time(now)}] BTC: ${btc_price:,.2f} | Spread: {spread:+7.2f} | Min: {int(seconds_into//60)}")

            # ── LOCK ZONE ──────────────────────────────────────────────────
            in_lock_zone = ALERT_WINDOW_START <= seconds_into <= ALERT_WINDOW_END

            # FIX 4: re-entry — jangan blokir dengan alerted kecuali sudah trade
            # Cukup cek: belum traded, masih di lock zone, filter_attempts < MAX
            can_trade = (
                in_lock_zone
                and not w.traded
                and not w.alerted              # alerted = True hanya setelah trade berhasil
                and w.filter_attempts < MAX_FILTER_ATTEMPTS
            )

            if can_trade and abs_spread >= self.spread_threshold:
                entry_price = estimate_entry_price(abs_spread, self.mode)
                size        = get_position_size(self.bankroll, entry_price)

                passed, reason = self._check_signal_filters(direction, abs_spread, entry_price)

                if not passed:
                    w.filter_attempts += 1
                    attempts_left = MAX_FILTER_ATTEMPTS - w.filter_attempts
                    skip_msg = (
                        f"⏸️ FILTERED ({w.filter_attempts}/{MAX_FILTER_ATTEMPTS}): {reason} "
                        f"| Spread: {spread:+.0f} | Rechecking in {CHECK_INTERVAL}s "
                        f"({'done' if attempts_left == 0 else f'{attempts_left} left'})"
                    )
                    print(f"\n{skip_msg}\n")
                    if w.filter_attempts >= MAX_FILTER_ATTEMPTS:
                        w.result = f"SKIP_{reason.split('(')[0].strip()}"
                        self._notify(f"⏸️ *LOCK SKIPPED* — {reason}\nWindow: `{format_time(w.start)}`")
                else:
                    # All filters passed
                    ev_est = calculate_ev(entry_price, estimate_win_probability(abs_spread))
                    alert_msg = (
                        f"🚨 *LOCK SETUP* 🚨\n"
                        f"Window: `{format_time(w.start)}`\n"
                        f"PTB: `${w.ptb:,.2f}` | BTC: `${btc_price:,.2f}`\n"
                        f"Spread: `{spread:+.2f}` | Dir: *{direction.value}*\n"
                        f"Entry: `{entry_price:.2f}` | Size: `{size}` | EV(est): `${ev_est:.4f}`\n"
                        f"Mode: *{self.mode.value.upper()}*"
                    )
                    self._notify(alert_msg)
                    w.alerted = True  # set sekarang — cegah spam re-trigger tiap 5s

                    if size > 0:
                        self._execute_trade(direction, w, entry_price, size, abs_spread)

            time.sleep(CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Polymarket BTC 5m LOCK Bot")
    parser.add_argument("--signal",        action="store_true", help="Signal-only: Telegram alerts + Polymarket price validation (no wallet needed)")
    parser.add_argument("--live",          action="store_true", help="LIVE mode (default: paper)")
    parser.add_argument("--telegram-token", default="",         help="Telegram Bot Token")
    parser.add_argument("--chat-id",        default="",         help="Telegram Chat ID")
    parser.add_argument("--spread",         type=int,   default=50,   help="Spread threshold USD")
    parser.add_argument("--bankroll",       type=float, default=0.0,  help="Starting bankroll USD (0 = auto-fetch from Polymarket)")
    parser.add_argument("--daily-stop",     type=float, default=5.0,  help="Daily stop loss USD")
    parser.add_argument("--max-trades",     type=int,   default=20,   help="Max trades per day")
    args = parser.parse_args()

    if args.live and not args.signal and not os.environ.get("POLYMARKET_PRIVATE_KEY"):
        print("❌ --live requires POLYMARKET_PRIVATE_KEY env var (use --signal for signal-only mode).")
        sys.exit(1)

    try:
        AutoTrader(args).run()
    except KeyboardInterrupt:
        print("\n\n👋 Bot stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
