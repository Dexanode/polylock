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
SPREAD_THRESHOLD     = 50         # USD minimum spread to consider a trade
ALERT_WINDOW_START   = 3 * 60 + 30  # 210s — start of LOCK zone (lebih awal, beri waktu manual order)
ALERT_WINDOW_END     = 4 * 60 + 20  # 260s — end of LOCK zone (stop 40s sebelum close)
FEE_RATE             = 0.02       # Polymarket taker fee
MIN_ORDER_USDC       = 1.0        # Polymarket minimum order $1
BUY_PRICE_BUFFER     = 0.10       # Add to price to cross spread & ensure fill (min $0.10)

def fetch_polymarket_balance(private_key: str, clob_creds: Optional[Dict] = None) -> Optional[float]:
    if not clob_creds:
        return None
    try:
        import hmac, hashlib, base64
        ts = str(int(time.time() * 1000))
        message = ts + "GET" + "/balance-allowance" + ""
        raw_secret = clob_creds["api_secret"]
        # Auto-pad jika secret belum base64-padded
        missing_padding = len(raw_secret) % 4
        if missing_padding:
            raw_secret += "=" * (4 - missing_padding)
        secret = base64.b64decode(raw_secret)
        sig = base64.b64encode(hmac.new(secret, message.encode(), hashlib.sha256).digest()).decode()
        headers = {"POLY-API-KEY": clob_creds["api_key"], "POLY-TIMESTAMP": ts, "POLY-SIGNATURE": sig, "POLY-PASSPHRASE": clob_creds["api_passphrase"]}
        req = urllib.request.Request(f"{CLOB_HOST}/balance-allowance", headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        # V2 returns {"balance": x, "allowance": y} or just {"balance": x}
        balance = float(data.get("balance", 0))
        allowance = float(data.get("allowance", 0))
        print(f"[BALANCE] Polymarket: ${balance:,.2f} (allowance: ${allowance:,.2f})")
        return balance if balance > 0 else allowance
    except Exception as e:
        print(f"[WARN] Polymarket balance: {e}")
        return None

def fetch_usdc_balance(wallet_address: str) -> float:
    USDC_CONTRACT = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
    BALANCE_SELECTOR = "0x70a08231" + wallet_address[2:].lower().rjust(64, "0")
    payload = json.dumps({"jsonrpc":"2.0","method":"eth_call","params":[{"to":USDC_CONTRACT,"data":BALANCE_SELECTOR},"latest"],"id":1}).encode()
    for rpc in POLYGON_RPCS:
        try:
            req = urllib.request.Request(rpc, data=payload, headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                result = json.loads(resp.read())
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
        req  = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            markets = json.loads(resp.read())
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
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                markets = json.loads(resp.read())
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
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
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


def place_live_order(
    private_key: str,
    creds: Dict,
    token_id: str,
    size_usdc: float,
    price: float,
    funder: str = None,
) -> Dict:
    """Place live order via CLOB V2 with POLY_1271 + deposit wallet.
    Returns {"success": True, ...} or {"success": False, "error": "reason"}"""
    try:
        _patch_httpx_proxy()
        from py_clob_client_v2 import ClobClient, ApiCreds, OrderArgs, OrderType, SignatureTypeV2
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

        # DO NOT cancel previous orders — GTC needs time to fill!
        # cancel_all_clob_orders(client)  # ← removed: was killing orders before they could match

        # Fix: size = shares (bukan USDC). Min 5 shares.
        shares = max(5.0, size_usdc / price) if price > 0 else 5.0

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=shares,
            side="BUY",
            builder_code=BUILDER_CODE,
        )

        print(f"[LIVE] ${size_usdc:.2f} USDC = {shares:.1f} shares @ {price:.2f} | {token_id[:15]}...")
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC)

        if not resp:
            return {"success": False, "error": "CLOB API empty response — network issue?"}

        status = resp.get("status", "?")
        order_id = resp.get("orderID") or resp.get("id", "?")
        print(f"[LIVE] ID: {order_id} | Status: {status}")

        if status == "matched":
            # Immediately matched
            filled = check_order_fill(client, order_id)
            fill_shares = filled if (filled and filled > 0) else shares
            print(f"✅ Instantly MATCHED! Filled: {fill_shares:.1f} shares")
            return {
                "success": True,
                "order_id": order_id, "status": "matched",
                "shares": fill_shares, "price": price,
                "cost": fill_shares * price, "token_id": token_id,
            }

        elif status in ("live", "unmatched") or resp.get("success"):
            # GTC order placed — leave it open to fill over time
            print(f"✅ GTC order LIVE — waiting for match")
            return {
                "success": True,
                "order_id": order_id, "status": "live",
                "shares": shares, "price": price,
                "cost": shares * price, "token_id": token_id,
            }

        else:
            err_msg = resp.get("errorMsg", str(resp))
            print(f"[ERROR] {err_msg}")
            return {"success": False, "error": f"CLOB rejected: {err_msg[:150]}"}

    except Exception as e:
        err = str(e)
        if "couldn't be fully filled" in err or "FOK" in err:
            return {"success": False, "error": f"No sell orders at ${price:.2f} — zero liquidity"}
        elif "not enough" in err.lower() or "insufficient" in err.lower():
            return {"success": False, "error": "Insufficient pUSD in deposit wallet"}
        elif "timeout" in err.lower() or "timed out" in err.lower():
            return {"success": False, "error": "Polymarket API timeout — retry next window"}
        print(f"[LIVE ERROR] {err[:200]}")
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
        req = urllib.request.Request(KLINES_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
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
        self.daily_stats = DailyStats(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
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
                print(f"💰 Polymarket pUSD: ${actual_balance:,.2f} — bankroll updated")
                self.bankroll = actual_balance
                self.initial_bankroll = actual_balance
            else:
                print(f"⚠️  No balance on deposit wallet — using --bankroll ${self.bankroll:.2f}")
                self.initial_bankroll = self.bankroll
        except Exception as e:
            print(f"[WARN] SDK balance check: {e}")
            self.initial_bankroll = self.bankroll
    # ── NOTIFY ─────────────────────────────────────────────────────────────

    def _notify(self, msg: str):
        print(msg)
        if self.telegram_token and self.chat_id:
            send_telegram(msg, self.telegram_token, self.chat_id)

    # ── DAILY RESET ────────────────────────────────────────────────────────

    def _check_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.daily_stats.date:
            summary = (
                f"📅 Day summary: {self.daily_stats.date}\n"
                f"   Trades: {self.daily_stats.trades} | W/L: {self.daily_stats.wins}/{self.daily_stats.losses}\n"
                f"   P/L: ${self.daily_stats.profit:+.2f} | Peak: ${self.daily_stats.peak_bankroll:.2f}"
            )
            self._notify(summary)
            self.daily_stats = DailyStats(date=today, peak_bankroll=self.bankroll)

    # ── RISK CONTROLS ──────────────────────────────────────────────────────

    def _should_stop_trading(self) -> bool:
        if self.daily_stats.profit <= -self.daily_stop:
            return True
        if self.bankroll < 1.0:
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

        if direction == Direction.DOWN and momentum > MOMENTUM_THRESHOLD:
            return False, f"COUNTER_MOMENTUM_UP (+{momentum:.4f}%)"

        if direction == Direction.UP and momentum < -MOMENTUM_THRESHOLD:
            return False, f"COUNTER_MOMENTUM_DOWN ({momentum:.4f}%)"

        win_prob = estimate_win_probability(abs_spread)
        ev       = calculate_ev(entry_price, win_prob)
        print(f"[SIGNAL] WinProb: {win_prob:.0%} | EV: ${ev:.4f}/share")

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

            # Fetch harga REAL dari Polymarket sebelum kirim sinyal
            up_price, down_price = fetch_polymarket_real_prices(window.start)
            real_price = up_price if direction == Direction.UP else down_price
            print(f"[PRICE CHECK] UP={up_price:.2f} DOWN={down_price:.2f} | Signal={direction.value}")

            # Validasi: harga real harus match direction
            # Kalau signal UP tapi UP sudah >0.85 = market terlalu mahal, skip
            # Kalau signal DOWN tapi DOWN sudah <0.30 = market pricing DOWN unlikely, skip
            MIN_PRICE = 0.30   # harga terlalu murah = market tidak percaya direction ini
            MAX_PRICE = 0.97   # harga terlalu mahal = profit terlalu kecil
            if real_price > 0:
                if real_price < MIN_PRICE:
                    msg_skip = (
                        f"⚠️ <b>SIGNAL SKIPPED</b>\n"
                        f"Direction {direction.value} tapi harga real hanya <code>{real_price:.2f}</code> ({int(real_price*100)}¢)\n"
                        f"Market tidak confident → skip untuk hindari loss"
                    )
                    print(f"[SKIP] {direction.value} price too low: {real_price:.2f}")
                    if self.telegram_token and self.chat_id:
                        send_telegram(msg_skip, self.telegram_token, self.chat_id)
                    return False
                if real_price > MAX_PRICE:
                    print(f"[SKIP] {direction.value} price too high (no profit): {real_price:.2f}")
                    return False

            # Pakai harga real jika tersedia, fallback ke estimasi
            actual_price = real_price if real_price > 0 else entry_price
            bet_usdc     = max(cost, MIN_ORDER_USDC)
            profit_est   = round(bet_usdc / actual_price * (1 - FEE_RATE) - bet_usdc, 2)
            win_prob     = estimate_win_probability(abs(spread))
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
                    # Apply price buffer to cross spread (dynamic: max($0.10 or 20% of price))
                    buffer = max(BUY_PRICE_BUFFER, actual_price * 0.20)
                    order_price = min(actual_price + buffer, 0.95)
                    print(f"[LIVE ORDER] Token: {token[:20]}... | {direction.value}")
                    print(f"[LIVE ORDER] Bet: ${bet_usdc:.2f} | Signal: {actual_price:.2f} → Limit: {order_price:.2f}")
                    order_result = place_live_order(self._private_key, self.clob_creds, token, bet_usdc, order_price, self.deposit_wallet)
                    if order_result.get("success"):
                        # ✅ TRACK WITH REAL ORDER NUMBERS (signal price, not limit price)
                        real_status = order_result["status"]
                        real_shares = order_result["shares"]
                        real_cost = order_result["cost"]
                        window._order_id = order_result["order_id"]
                        window._is_live = True
                        window._order_status = real_status
                        window._actual_price = actual_price  # Signal price (conservative)
                        window._actual_shares = real_shares
                        window._actual_cost = real_cost
                        window.entry_price = actual_price
                        window.size = real_shares

                        if real_status == "matched":
                            # Order already filled — deduct immediately
                            status_label = "MATCHED"
                            is_filled = True
                        else:
                            # GTC order live — deduct only when fill confirmed in resolve
                            status_label = "LIVE (pending fill)"
                            is_filled = False

                        msg_live = (
                            f"🤖 <b>AUTO-TRADE EXECUTED</b>\n"
                            f"Status  : <b>{status_label}</b>\n"
                            f"Action  : <b>{action}</b>\n"
                            f"Amount  : <code>${real_cost:.2f} USDC</code>\n"
                            f"Shares  : <code>{real_shares:.1f}</code> | Limit <code>{order_price:.2f}</code>\n"
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
        # Only deduct bankroll for PAPER or MATCHED orders. GTC live orders wait for fill.
        if not window._is_live or window._order_status == "matched":
            deduct = window._actual_cost if window._is_live else total_cost
            self.bankroll -= deduct
            print(f"[TRADE] Bankroll: ${self.bankroll:.2f}")
        else:
            print(f"[TRADE] GTC order pending — bankroll unchanged: ${self.bankroll:.2f}")
        window.result = "PENDING"
        log_window(window)
        log_stats(self.daily_stats, self.bankroll, self.mode.value)
        return True

    # ── FIX 1: RESOLVE WITH BOUNDARY PRICE ─────────────────────────────────

    def _check_polymarket_resolution(self, window: WindowState) -> Optional[bool]:
        """Check actual Polymarket market resolution for a live trade.
        Returns True if trade won, False if lost, None if still unresolved."""
        try:
            ts = int(window.start.timestamp())
            slug = f"btc-updown-5m-{ts}"
            # Gamma API uses query param, returns array
            url = f"{GAMMA_HOST}/markets?slug={slug}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                markets = json.loads(resp.read())
            
            if not markets or not isinstance(markets, list):
                return None
            data = markets[0]

            closed = data.get("closed", False)
            outcome = data.get("outcome", "")  # "Yes" or "No"

            if not closed or not outcome:
                return None  # Still pending

            # "Yes" = UP token won, "No" = DOWN token won
            yes_won = (outcome.lower() == "yes")

            if yes_won:
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

        # ── LIVE ORDER: check fill first, then resolve ──────────────
        if window._is_live and window._order_id:
            # If order was "live" (GTC, not yet matched), check fill now
            if window._order_status in ("live", "unmatched"):
                try:
                    _patch_httpx_proxy()
                    from py_clob_client_v2 import ClobClient, ApiCreds, SignatureTypeV2
                    from py_clob_client_v2.constants import POLYGON
                    api_creds = ApiCreds(
                        api_key=self.clob_creds["api_key"],
                        api_secret=self.clob_creds["api_secret"],
                        api_passphrase=self.clob_creds["api_passphrase"],
                    )
                    client = ClobClient(
                        host=CLOB_HOST, chain_id=POLYGON,
                        key=self._private_key, creds=api_creds,
                        signature_type=SignatureTypeV2.POLY_1271,
                        funder=self.deposit_wallet,
                    )
                    filled = check_order_fill(client, window._order_id)
                    if filled and filled > 0:
                        # Order filled! Deduct bankroll now and update tracking
                        actual_fill_cost = filled * window.entry_price
                        window._actual_shares = filled
                        window._actual_cost = actual_fill_cost
                        window.size = filled
                        window._order_status = "filled"
                        self.bankroll -= actual_fill_cost
                        print(f"[RESOLVE] Order {window._order_id[:15]}... FILLED: {filled:.1f} shares | deduct ${actual_fill_cost:.2f}")
                    else:
                        # Never filled — cancel and skip
                        try:
                            client.cancel(window._order_id)
                        except Exception:
                            pass
                        print(f"[RESOLVE] Order {window._order_id[:15]}... never filled → SKIP")
                        window.result = "SKIP (unfilled)"
                        if window in self._pending_live:
                            self._pending_live.remove(window)
                        log_window(window)
                        return
                except Exception as e:
                    print(f"[RESOLVE] Fill check error: {e}")
            # Try Polymarket API once (don't block — fallback to Chainlink)
            pm_won = self._check_polymarket_resolution(window)
            if pm_won is not None:
                won = pm_won  # Use Polymarket resolution
            else:
                # Fallback: Chainlink boundary price (same oracle Polymarket uses)
                won = (
                    (window.direction == Direction.DOWN and window.final_spread < 0) or
                    (window.direction == Direction.UP   and window.final_spread > 0)
                )
                print(f"[RESOLVE] Polymarket API unavailable — using Chainlink (same oracle)")
            # Remove from pending if it was deferred
            if window in self._pending_live:
                self._pending_live.remove(window)
        else:
            # Paper / fallback: use Chainlink boundary price
            won = (
                (window.direction == Direction.DOWN and window.final_spread < 0) or
                (window.direction == Direction.UP   and window.final_spread > 0)
            )

        self._finalize_window_result(window, won, boundary_price)

    def _finalize_window_result(self, window: WindowState, won: bool, boundary_price: float = 0.0):
        """Apply win/loss to bankroll and log results."""
        # Use REAL numbers for live trades, estimated for paper
        cost       = window._actual_cost if window._is_live else window.size * window.entry_price
        fee        = cost * FEE_RATE
        total_cost = cost + fee

        if won:
            payout = window.size * 1.0
            profit = payout - total_cost
            self.daily_stats.wins   += 1
            self.daily_stats.profit += profit
            self.bankroll           += payout
            window.result = "WIN"
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
            self.daily_stats.losses += 1
            self.daily_stats.profit -= loss
            window.result = "LOSS"
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
        # Persist hasil final ke file — overwrite baris PENDING
        log_window(window)
        log_stats(self.daily_stats, self.bankroll, self.mode.value)

    # ── MAIN LOOP ──────────────────────────────────────────────────────────

    def run(self):
        print("💡 PolyLock Bot running. Press Ctrl+C to stop.\n")

        while True:
            now = datetime.now(timezone.utc)
            self._check_new_day()

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

            # ── Retry pending live trades from previous cycles ─────────
            if self._pending_live:
                resolved_now = []
                for pw in self._pending_live:
                    result = self._check_polymarket_resolution(pw)
                    if result is not None:
                        # Polymarket resolved — finalize
                        pw.final_spread = btc_price - pw.ptb
                        self._finalize_window_result(pw, result)
                        resolved_now.append(pw)
                for pw in resolved_now:
                    self._pending_live.remove(pw)
                    self.all_time_trades.append(pw)

            # ── New window ─────────────────────────────────────────────────
            if self.current_window is None or self.current_window.start != window_start:
                if self.current_window:
                    # Don't resolve if already in pending_live (handled above)
                    if self.current_window not in self._pending_live:
                        self._resolve_window(self.current_window, btc_price)
                    if self.current_window not in self._pending_live:
                        self.all_time_trades.append(self.current_window)

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
            # FIX: selalu update direction di window state — tampil di log meski SKIP
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
                    ev = calculate_ev(entry_price, estimate_win_probability(abs_spread))
                    alert_msg = (
                        f"🚨 *LOCK SETUP* 🚨\n"
                        f"Window: `{format_time(w.start)}`\n"
                        f"PTB: `${w.ptb:,.2f}` | BTC: `${btc_price:,.2f}`\n"
                        f"Spread: `{spread:+.2f}` | Dir: *{direction.value}*\n"
                        f"Entry: `{entry_price:.2f}` | Size: `{size}` | EV: `${ev:.4f}`\n"
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
    parser.add_argument("--bankroll",       type=float, default=10.0, help="Starting bankroll USD")
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
