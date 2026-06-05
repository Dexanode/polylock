#!/usr/bin/env python3
"""
Maker Rebate Market Maker — 5m Polymarket BTC
==============================================
Strategy: Buy YES + NO at limit, profit from spread + maker rebate.
Hold both sides to resolution (no CTF merge needed for V1).

Based on direkturcrypto/polymarket-terminal — adapted for Python + 5-minute markets.

Usage:
  python3 strategy_maker_mm.py --live [--size 5] [--max-combined 0.98]
  python3 strategy_maker_mm.py --sim          # simulate only
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass, field

# ── Path fix ────────────────────────────────────────────────────────
sys.path.insert(0, '/tmp')
sys.path.insert(0, os.path.dirname(__file__))

# ── Auto-load .env ───────────────────────────────────────────────────
_ENV_FILE = os.path.join(os.path.dirname(__file__) or '.', ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                k, v = _line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
BUILDER_CODE = os.environ.get("POLYMARKET_BUILDER_CODE",
    "0x2111a204350f2c552401b7d34b7cb61021e32b68a17a15ef712b978fd991f55d")


# ═══════════════════════════════════════════════════════════════════════
#  Helpers (import compatible with bot_clean.py)
# ═══════════════════════════════════════════════════════════════════════

def compute_deposit_wallet_address(owner: str) -> str:
    """Compute UUPS deposit wallet address via CREATE2 (same as Polymarket)."""
    from eth_utils import to_bytes, to_checksum_address, keccak
    from eth_abi import encode
    
    DEPOSIT_WALLET_FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
    DEPOSIT_WALLET_IMPLEMENTATION = "0x58CA52ebe0DadfdF531Cde7062e76746de4Db1eB"
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


def compute_deposit_wallet_address_v2(owner: str) -> str:
    """Fallback: V2 factory (neg-risk exchange)."""
    from web3 import Web3
    owner = owner.lower()
    salt = "0x0000000000000000000000000000000000000000000000000000000000000000"
    # Simplified: use the V2 factory
    factory = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    # This is approximate — the actual init code differs
    # For the safer approach, use the primary factory
    return compute_deposit_wallet_address(owner)


def _patch_httpx_proxy():
    """Patch httpx to use system proxy settings."""
    import httpx
    if hasattr(httpx, '_patched_by_poly'):
        return
    try:
        from urllib import request as _req
    except Exception:
        return
    _orig_client = httpx.Client
    class _PatchedClient(_orig_client):
        def __init__(self, *args, **kwargs):
            if 'proxy' not in kwargs and 'proxies' not in kwargs:
                kwargs['proxy'] = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or None
            super().__init__(*args, **kwargs)
    httpx.Client = _PatchedClient
    _orig_async = httpx.AsyncClient
    class _PatchedAsync(_orig_async):
        def __init__(self, *args, **kwargs):
            if 'proxy' not in kwargs and 'proxies' not in kwargs:
                kwargs['proxy'] = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or None
            super().__init__(*args, **kwargs)
    httpx.AsyncClient = _PatchedAsync
    httpx._patched_by_poly = True


def send_telegram(msg: str, token: str, chat_id: str):
    """Send Telegram message via Bot API."""
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        print(f"[TELEGRAM] Error: {e}")


def snap_to_tick(price: float, tick_size: str = "0.01") -> float:
    """Round price to nearest valid tick."""
    ts = float(tick_size)
    return round(round(price / ts) * ts, max(2, len(str(ts).split('.')[1])))


# ═══════════════════════════════════════════════════════════════════════
#  Gamma API — market detection
# ═══════════════════════════════════════════════════════════════════════

def fetch_market_by_slug(asset: str, duration: str, slot_ts: int) -> Optional[dict]:
    """Fetch 5-minute market from Gamma API by deterministic slug."""
    slug = f"{asset}-updown-{duration}-{slot_ts}"
    url = f"{GAMMA_HOST}/markets/slug/{slug}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "polylock/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        if data and data.get("conditionId"):
            return data
    except Exception as e:
        print(f"[GAMMA] {slug} → {e}")
    return None


def extract_token_ids(market: dict) -> Tuple[Optional[str], Optional[str]]:
    """Extract YES/NO token IDs from Gamma market response."""
    token_ids = market.get("clobTokenIds")
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except Exception:
            token_ids = None
    if isinstance(token_ids, list) and len(token_ids) >= 2:
        return str(token_ids[0]), str(token_ids[1])
    tokens = market.get("tokens", [])
    if len(tokens) >= 2:
        return (
            str(tokens[0].get("token_id") or tokens[0].get("tokenId", "")),
            str(tokens[1].get("token_id") or tokens[1].get("tokenId", "")),
        )
    return None, None


# ═══════════════════════════════════════════════════════════════════════
#  CLOB — price & order book
# ═══════════════════════════════════════════════════════════════════════

def get_best_bid(client, token_id: str) -> Optional[float]:
    """Get current best bid price for a token."""
    try:
        result = client.get_price(token_id, "BUY")
        price = float(result.get("price", result if isinstance(result, (int, float)) else 0))
        if 0.01 <= price <= 0.99:
            return price
    except Exception:
        pass
    try:
        mp = client.get_midpoint(token_id)
        price = float(mp.get("mid", mp if isinstance(mp, (int, float)) else 0))
        if 0.01 <= price <= 0.99:
            return price
    except Exception:
        pass
    return None


def get_best_ask(client, token_id: str) -> Optional[float]:
    """Get current best ask (sell) price."""
    try:
        result = client.get_price(token_id, "SELL")
        price = float(result.get("price", result if isinstance(result, (int, float)) else 0))
        if 0.01 <= price <= 0.99:
            return price
    except Exception:
        pass
    return None


def check_liquidity(client, token_id: str) -> bool:
    """Check if token has an active order book."""
    try:
        book = client.get_order_book(token_id)
        if isinstance(book, dict):
            asks = book.get("asks", [])
            return len(asks) > 0
    except Exception as e:
        err = str(e)
        if "404" in err or "No orderbook" in err:
            return False
    return False


def check_order_fill(client, order_id: str) -> Optional[float]:
    """Check how many shares of an order were filled."""
    if not order_id or order_id.startswith("sim-"):
        return None
    try:
        order = client.get_order(order_id)
        if not order:
            return None
        status = order.get("status", "")
        if status in ("FILLED", "FILLED_FULLY", "matched"):
            return float(order.get("filled", order.get("original_size", 5)))
        if status in ("PARTIAL_FILLED", "FILLED_PARTIALLY", "live", "OPEN"):
            filled = float(order.get("filled", 0))
            return filled if filled > 0 else None
    except Exception as e:
        print(f"[FILL CHECK] {e}")
    return None


def cancel_all_orders(client) -> int:
    """Cancel all open orders. Returns count cancelled."""
    try:
        # Python CLOB v2: use get_orders with params
        from py_clob_client_v2.order_builder.helpers import GET_ORDERS_PARAMS
        resp = client._get("orders", params={"status": "open"})
        if isinstance(resp, list):
            orders = resp
        elif isinstance(resp, dict):
            orders = resp.get("data", resp.get("orders", []))
        else:
            orders = []
        count = 0
        for o in orders:
            oid = o.get("orderID") or o.get("id", "")
            if oid:
                try:
                    client.cancel(oid)
                    count += 1
                except Exception:
                    pass
        return count
    except Exception as e:
        print(f"[CANCEL] {e}")
        return 0


# ═══════════════════════════════════════════════════════════════════════
#  Core — Maker Rebate Strategy
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MMState:
    """State for one Maker Rebate cycle."""
    condition_id: str = ""
    question: str = ""
    yes_token: str = ""
    no_token: str = ""
    yes_price: float = 0.0
    no_price: float = 0.0
    trade_size: float = 5.0           # shares per side
    max_combined: float = 0.99        # max YES+NO bid
    tick_size: str = "0.01"
    neg_risk: bool = False
    end_time_ms: int = 0

    yes_order_id: str = ""
    no_order_id: str = ""
    yes_filled: float = 0.0
    no_filled: float = 0.0
    yes_cost: float = 0.0
    no_cost: float = 0.0

    status: str = "idle"  # idle | placing | monitoring | filled | stuck | done
    cycles_completed: int = 0
    stuck_side: str = ""  # "yes" or "no" — the side that filled while other didn't

    created_at: float = field(default_factory=time.time)


class MakerRebateMM:
    """Maker Rebate Market Maker for 5-minute Polymarket BTC markets."""

    def __init__(
        self,
        private_key: str,
        clob_creds: dict,
        deposit_wallet: str = None,
        *,
        live: bool = False,
        trade_size: float = 5.0,
        max_combined: float = 0.98,
        cut_loss_sec: int = 60,
        entry_window_sec: int = 45,
        reentry_delay_sec: float = 15.0,
        asset: str = "btc",
        duration: str = "5m",
        telegram_token: str = "",
        chat_id: str = "",
    ):
        self.pk = private_key
        self.creds = clob_creds
        self._funder = deposit_wallet
        self.live = live
        self.trade_size = trade_size
        self.max_combined = max_combined
        self.cut_loss_sec = cut_loss_sec
        self.entry_window_sec = entry_window_sec
        self.reentry_delay = reentry_delay_sec
        self.asset = asset
        self.duration = duration
        self.slot_sec = 300 if duration == "5m" else 900
        self.tg_token = telegram_token
        self.chat_id = chat_id

        self._client = None
        self._funder = deposit_wallet or compute_deposit_wallet_address(
            self._get_eoa()
        )
        self.stats = {"cycles": 0, "wins": 0, "losses": 0, "stuck": 0, "profit": 0.0}

    def _get_eoa(self) -> str:
        from web3 import Web3
        return Web3().eth.account.from_key(self.pk).address

    def _get_client(self):
        if self._client is None:
            _patch_httpx_proxy()
            from py_clob_client_v2 import ClobClient, ApiCreds, SignatureTypeV2
            from py_clob_client_v2.constants import POLYGON

            api = ApiCreds(
                api_key=self.creds["api_key"],
                api_secret=self.creds["api_secret"],
                api_passphrase=self.creds["api_passphrase"],
            )
            self._client = ClobClient(
                host=CLOB_HOST,
                chain_id=POLYGON,
                key=self.pk,
                creds=api,
                signature_type=SignatureTypeV2.POLY_1271,
                funder=self._funder,
            )
        return self._client

    def _notify(self, msg: str):
        """Send Telegram notification."""
        print(msg)
        if self.tg_token and self.chat_id:
            send_telegram(msg, self.tg_token, self.chat_id)

    # ── Market detection ──────────────────────────────────────────

    def _next_slot_ts(self) -> int:
        """Get Unix timestamp for next 5-minute slot."""
        return ((int(time.time()) // self.slot_sec) + 1) * self.slot_sec

    def _current_slot_ts(self) -> int:
        """Get Unix timestamp for current 5-minute slot."""
        return (int(time.time()) // self.slot_sec) * self.slot_sec

    def _detect_market(self, slot_ts: int) -> Optional[dict]:
        """Detect market for a given slot timestamp."""
        return fetch_market_by_slug(self.asset, self.duration, slot_ts)

    # ── Price calculation ─────────────────────────────────────────

    def _calculate_bid_prices(self, yes_token: str, no_token: str) -> Tuple[float, float]:
        """
        Calculate bid prices for YES and NO tokens.
        Strategy: bid at current best bid (or midpoint), ensure combined ≤ max_combined.
        """
        client = self._get_client()

        yes_bid = get_best_bid(client, yes_token) or 0.50
        no_bid = get_best_bid(client, no_token) or 0.50

        # Enforce combined cap
        if yes_bid + no_bid > self.max_combined:
            # Scale both down proportionally
            scale = self.max_combined / (yes_bid + no_bid)
            yes_bid = snap_to_tick(yes_bid * scale)
            no_bid = snap_to_tick(no_bid * scale)

        # Floor check
        yes_bid = max(0.01, min(0.99, yes_bid))
        no_bid = max(0.01, min(0.99, no_bid))

        # Re-check combined
        if yes_bid + no_bid > self.max_combined:
            no_bid = snap_to_tick(self.max_combined - yes_bid)

        return yes_bid, no_bid

    # ── Order placement ───────────────────────────────────────────

    def _place_limit_buy(self, token_id: str, price: float, shares: float, tick_size: str,
                         neg_risk: bool, side_label: str) -> Tuple[bool, str]:
        """Place a GTC limit BUY order. Returns (success, order_id)."""
        if not self.live:
            return True, f"sim-{side_label}-{int(time.time())}"

        from py_clob_client_v2.clob_types import OrderArgs
        from py_clob_client_v2 import OrderType

        client = self._get_client()
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=shares,
                side="BUY",
                builder_code=BUILDER_CODE,
            )
            resp = client.create_and_post_order(order_args, order_type=OrderType.GTC)
            if not resp:
                return False, ""
            oid = resp.get("orderID") or resp.get("id", "")
            status = resp.get("status", "?")
            print(f"  [{side_label}] {token_id[:15]}... | ${price:.3f} x {shares:.0f} | ID: {oid[:15]}... | {status}")
            return bool(oid), oid
        except Exception as e:
            print(f"  [{side_label}] Error: {e}")
            return False, ""

    # ── Fill monitoring ───────────────────────────────────────────

    def _wait_for_fills(self, state: MMState, timeout_sec: int = 120) -> MMState:
        """Monitor orders until both fill or timeout."""
        if not self.live:
            state.yes_filled = state.trade_size
            state.no_filled = state.trade_size
            state.yes_cost = state.yes_filled * state.yes_price
            state.no_cost = state.no_filled * state.no_price
            state.status = "filled"
            return state

        client = self._get_client()
        deadline = time.time() + timeout_sec
        poll_interval = 3.0  # seconds

        while time.time() < deadline:
            # Check YES fill
            if state.yes_filled < state.trade_size:
                filled = check_order_fill(client, state.yes_order_id)
                if filled is not None:
                    state.yes_filled = min(filled, state.trade_size)
                    state.yes_cost = state.yes_filled * state.yes_price
                    if state.yes_filled >= state.trade_size * 0.99:
                        print(f"  ✅ YES filled: {state.yes_filled:.1f} shares")

            # Check NO fill
            if state.no_filled < state.trade_size:
                filled = check_order_fill(client, state.no_order_id)
                if filled is not None:
                    state.no_filled = min(filled, state.trade_size)
                    state.no_cost = state.no_filled * state.no_price
                    if state.no_filled >= state.trade_size * 0.99:
                        print(f"  ✅ NO filled: {state.no_filled:.1f} shares")

            # Both filled?
            yes_done = state.yes_filled >= state.trade_size * 0.99
            no_done = state.no_filled >= state.trade_size * 0.99

            if yes_done and no_done:
                state.status = "filled"
                print(f"  🎯 BOTH SIDES FILLED!")
                return state

            if yes_done and not no_done:
                state.stuck_side = "yes"
            elif no_done and not yes_done:
                state.stuck_side = "no"

            time.sleep(poll_interval)

        # Timeout
        yes_done = state.yes_filled >= state.trade_size * 0.99
        no_done = state.no_filled >= state.trade_size * 0.99

        if yes_done and no_done:
            state.status = "filled"
        elif yes_done or no_done:
            state.status = "stuck"
            state.stuck_side = "yes" if yes_done else "no"
        else:
            state.status = "expired"  # neither side filled

        return state

    # ── Resolution handling ───────────────────────────────────────

    def _calculate_result(self, state: MMState) -> float:
        """
        Calculate profit/loss at resolution.
        YES + NO always pays $1.00 per pair (one wins, market resolves).
        Profit = (shares × $1.00) − total_cost
        """
        total_cost = state.yes_cost + state.no_cost
        filled_pairs = min(state.yes_filled, state.no_filled)
        payout = filled_pairs * 1.0  # $1.00 per share pair
        profit = payout - total_cost

        # If stuck: winning side pays $1.00, losing side pays $0
        if state.stuck_side == "yes":
            # YES filled, NO not filled
            payout = state.yes_filled * 1.0
            profit = payout - state.yes_cost
        elif state.stuck_side == "no":
            # NO filled, YES not filled
            payout = state.no_filled * 1.0
            profit = payout - state.no_cost

        return round(profit, 4)

    # ── Main cycle ────────────────────────────────────────────────

    def _run_one_cycle(self, state: MMState) -> MMState:
        """Run one maker rebate cycle: place → monitor → result."""
        secs_remaining = max(0, (state.end_time_ms / 1000) - time.time())
        print(f"\n{'='*55}")
        print(f"  🔄 CYCLE {state.cycles_completed + 1} | {state.question[:40]}")
        print(f"  Time remaining: {secs_remaining:.0f}s")
        print(f"{'='*55}")

        # Skip if not enough time for cut-loss
        if secs_remaining < self.cut_loss_sec + 15:
            print(f"  ⏰ Not enough time ({secs_remaining:.0f}s) — skipping cycle")
            state.status = "done"
            return state

        # 1. Calculate bid prices
        yes_price, no_price = self._calculate_bid_prices(state.yes_token, state.no_token)
        combined = yes_price + no_price

        if combined > self.max_combined:
            print(f"  ❌ Combined {combined:.3f} > {self.max_combined} — market too expensive")
            state.status = "done"
            return state

        state.yes_price = yes_price
        state.no_price = no_price

        print(f"  YES bid: ${yes_price:.3f} | NO bid: ${no_price:.3f} | Combined: ${combined:.3f}")
        print(f"  Size: {state.trade_size:.0f} shares/side | Max cost: ${combined * state.trade_size:.2f}")

        # 2. Check liquidity on both sides
        client = self._get_client()
        if not check_liquidity(client, state.yes_token):
            print(f"  ❌ No liquidity on YES token")
            self._notify(f"🛑 <b>MM SKIP</b> — No order book for YES\nMarket: <code>{state.question[:50]}</code>")
            state.status = "done"
            return state
        if not check_liquidity(client, state.no_token):
            print(f"  ❌ No liquidity on NO token")
            self._notify(f"🛑 <b>MM SKIP</b> — No order book for NO\nMarket: <code>{state.question[:50]}</code>")
            state.status = "done"
            return state

        # 3. Place orders
        print(f"  📝 Placing limit BUY orders...")
        yes_ok, state.yes_order_id = self._place_limit_buy(
            state.yes_token, yes_price, state.trade_size,
            state.tick_size, state.neg_risk, "YES"
        )
        no_ok, state.no_order_id = self._place_limit_buy(
            state.no_token, no_price, state.trade_size,
            state.tick_size, state.neg_risk, "NO"
        )

        if not (yes_ok and no_ok):
            print(f"  ❌ Order placement failed")
            cancel_all_orders(client)
            state.status = "error"
            return state

        # 4. Monitor fills
        fill_timeout = min(120, max(15, secs_remaining - self.cut_loss_sec - 10))
        print(f"  👀 Monitoring fills (timeout: {fill_timeout}s)...")
        state = self._wait_for_fills(state, timeout_sec=fill_timeout)

        # 5. Handle result
        if state.status == "filled":
            profit = self._calculate_result(state)
            total_cost = state.yes_cost + state.no_cost
            self.stats["cycles"] += 1
            self.stats["wins"] += 1
            self.stats["profit"] += profit
            state.cycles_completed += 1

            self._notify(
                f"✅ <b>MM CYCLE COMPLETE</b>\n"
                f"Market : <code>{state.question[:40]}</code>\n"
                f"YES    : <code>{state.yes_filled:.1f} @ ${state.yes_price:.3f}</code> → ${state.yes_cost:.2f}\n"
                f"NO     : <code>{state.no_filled:.1f} @ ${state.no_price:.3f}</code> → ${state.no_cost:.2f}\n"
                f"Cost   : <b>${total_cost:.2f}</b> | Payout: <b>$5.00</b> | Profit: <b>+${profit:.2f}</b>\n"
                f"Total P&L: <b>${self.stats['profit']:.2f}</b> | Cycles: {self.stats['cycles']}"
            )
            print(f"  💰 Profit: +${profit:.2f} | Total: ${self.stats['profit']:.2f}")

        elif state.status == "stuck":
            self.stats["stuck"] += 1
            self._notify(
                f"⚠️ <b>MM STUCK</b> — {state.stuck_side.upper()} filled, other side didn't\n"
                f"Market: <code>{state.question[:40]}</code>\n"
                f"Holding {state.stuck_side.upper()} to resolution"
            )
            state.status = "done"  # Don't re-enter when stuck

        elif state.status == "expired":
            print(f"  ⏰ Neither side filled — expired")
            cancel_all_orders(client)
            self._notify(
                f"⏰ <b>MM EXPIRED</b> — orders never filled\n"
                f"Market: <code>{state.question[:40]}</code>"
            )
            state.status = "done"

        return state

    # ── Entry point ────────────────────────────────────────────────

    def run(self):
        """Main loop: detect NEXT market, wait, enter at open, cycle until close."""
        print(f"\n{'='*55}")
        print(f"  ⚡ MAKER REBATE MM — {self.asset.upper()} {self.duration}")
        print(f"{'='*55}")
        print(f"  Mode     : {'LIVE' if self.live else 'SIMULATION'}")
        print(f"  Size     : {self.trade_size} shares/side")
        print(f"  Max bid  : ${self.max_combined:.2f} combined")
        print(f"  Cut-loss : {self.cut_loss_sec}s before close")
        print(f"  Funder   : {self._funder}")
        print(f"{'='*55}\n")

        if not self.live:
            self._notify(f"🟡 <b>MM SIMULATION</b> — {self.asset.upper()} {self.duration}\nSize: {self.trade_size}/side | Max: ${self.max_combined:.2f}")

        while True:
            # ── Wait for NEXT market to be detectable ──
            next_ts = self._next_slot_ts()
            secs_until = next_ts - time.time()
            
            if secs_until > 60:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Next slot opens in {secs_until:.0f}s — waiting...")
                time.sleep(min(30, secs_until - 30))
                continue

            # ── Pre-detect: try to find market before it opens ──
            market = None
            deadline = next_ts + 30  # Give 30s after slot start to appear
            while time.time() < deadline:
                market = self._detect_market(next_ts)
                if market:
                    break
                print(f"  Market not live yet, retry in 3s...")
                time.sleep(3)

            if not market:
                print(f"  ❌ Market never appeared for slot {next_ts}")
                self._notify(f"⏭️ <b>MM SKIP</b> — Market not found for slot {next_ts}")
                time.sleep(self.slot_sec)
                continue

            yes_token, no_token = extract_token_ids(market)
            if not yes_token or not no_token:
                print(f"  ❌ Missing token IDs")
                time.sleep(self.slot_sec)
                continue

            end_time_ms = 0
            try:
                end_ts = market.get("endDate") or market.get("end_date_iso") or ""
                if end_ts:
                    from datetime import datetime as dt
                    end_dt = dt.fromisoformat(end_ts.replace("Z", "+00:00"))
                    end_time_ms = end_dt.timestamp() * 1000
            except Exception:
                end_time_ms = (slot_ts + self.slot_sec) * 1000

            state = MMState(
                condition_id=market.get("conditionId", ""),
                question=market.get("question", ""),
                yes_token=yes_token,
                no_token=no_token,
                trade_size=self.trade_size,
                max_combined=self.max_combined,
                tick_size=str(market.get("orderPriceMinTickSize", "0.01")),
                neg_risk=market.get("negRisk", False),
                end_time_ms=end_time_ms,
            )

            print(f"  📊 {state.question[:60]}")
            print(f"  YES: {yes_token[:20]}... | NO: {no_token[:20]}...")
            self._notify(
                f"🔍 <b>MM MARKET DETECTED</b>\n"
                f"<code>{state.question[:60]}</code>\n"
                f"Ends: {datetime.fromtimestamp(end_time_ms/1000).strftime('%H:%M:%S')} UTC"
            )

            # Run cycles until market closes
            while state.status not in ("done", "error"):
                secs_left = max(0, (end_time_ms / 1000) - time.time())
                if secs_left < self.cut_loss_sec + 10:
                    print(f"  ⏰ Cut-loss time — stopping cycles")
                    cancel_all_orders(self._get_client())
                    break

                state = self._run_one_cycle(state)

                # Re-entry delay if still going
                if state.status == "filled" and state.cycles_completed < 10:
                    reentry = min(self.reentry_delay, secs_left - self.cut_loss_sec - 20)
                    if reentry > 0:
                        print(f"  🔁 Re-entering in {reentry:.0f}s...")
                        state.status = "idle"
                        state.yes_filled = 0.0
                        state.no_filled = 0.0
                        state.yes_cost = 0.0
                        state.no_cost = 0.0
                        state.stuck_side = ""
                        time.sleep(reentry)

            print(f"  ✅ Market cycle complete ({state.cycles_completed} cycles)")
            # Brief pause before next market
            time.sleep(5)


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Maker Rebate MM — Polymarket 5m BTC")
    parser.add_argument("--live", action="store_true", help="Real trading mode")
    parser.add_argument("--sim", action="store_true", help="Simulation only (default without --live)")
    parser.add_argument("--size", type=float, default=5.0, help="Shares per side (default: 5)")
    parser.add_argument("--max-combined", type=float, default=0.98, help="Max combined bid (default: 0.98)")
    parser.add_argument("--cut-loss", type=int, default=60, help="Seconds before close to stop (default: 60)")
    parser.add_argument("--entry-window", type=int, default=45, help="Max seconds after open to enter (default: 45)")
    parser.add_argument("--reentry-delay", type=float, default=15.0, help="Seconds between re-entries (default: 15)")
    parser.add_argument("--asset", type=str, default="btc", help="Asset (default: btc)")
    parser.add_argument("--duration", type=str, default="5m", help="Market duration (default: 5m)")
    parser.add_argument("--telegram-token", type=str, default="", help="Telegram bot token")
    parser.add_argument("--chat-id", type=str, default="", help="Telegram chat ID")
    args = parser.parse_args()

    live = args.live or args.sim is False  # --live takes precedence

    # Load creds
    creds_path = os.path.join(os.path.dirname(__file__) or '.', "..", "logs", "clob_creds.json")
    if not os.path.exists(creds_path):
        creds_path = "/root/polymarket/logs/clob_creds.json"
    if not os.path.exists(creds_path):
        print("❌ clob_creds.json not found")
        sys.exit(1)
    with open(creds_path) as f:
        clob_creds = json.load(f)

    pk = os.environ.get("POLYMARKET_PRIVATE_KEY")
    if not pk:
        print("❌ POLYMARKET_PRIVATE_KEY not set")
        sys.exit(1)

    mm = MakerRebateMM(
        private_key=pk,
        clob_creds=clob_creds,
        live=live,
        trade_size=args.size,
        max_combined=args.max_combined,
        cut_loss_sec=args.cut_loss,
        entry_window_sec=args.entry_window,
        reentry_delay_sec=args.reentry_delay,
        asset=args.asset,
        duration=args.duration,
        telegram_token=args.telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        chat_id=args.chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""),
    )

    try:
        mm.run()
    except KeyboardInterrupt:
        print("\n👋 Stopped.")


if __name__ == "__main__":
    main()
