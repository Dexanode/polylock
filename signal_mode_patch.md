# Signal-Only Mode Patch untuk PolyLock

## Masalah
`--live` mode butuh CLOB auth + wallet. Kalau gagal auth, bot fallback ke PAPER dan gak kirim Telegram signal.

## Solusi
Tambah `--signal` flag yang:
- Gak butuh wallet / CLOB auth
- Tetap fetch harga real Polymarket via Gamma API
- Tetap validasi harga (skip <30¢, warn 30-45¢, skip >97¢)
- Kirim Telegram notif HTML dengan link clickable
- Tetap jalankan paper trade simulation di background

## Patch

### 1. Di `__init__`, tambahin signal mode detection:

```python
# Di class AutoTrader.__init__, setelah:
self.mode = Mode.LIVE if getattr(args, 'live', False) else Mode.PAPER

# Tambah:
self.signal_only = getattr(args, 'signal', False)  # Signal mode tanpa CLOB
```

### 2. Ubah `_init_live()`:

```python
def _init_live(self):
    """Setup CLOB credentials and fetch market tokens for live trading."""
    if self.signal_only:
        print("📡 SIGNAL-ONLY MODE — No orders, Telegram alerts only")
        return  # Skip CLOB init entirely
    
    # ... existing CLOB init code ...
```

### 3. Di `_execute_trade`, cek signal mode:

Di bagian `if self.mode == Mode.LIVE:`, ubah jadi:

```python
if self.mode == Mode.LIVE or self.signal_only:
```

### 4. Di `main()`, tambah argumen:

```python
parser.add_argument("--signal", action="store_true", 
                    help="Signal-only: Telegram alerts + price validation, NO orders")
```

### 5. Hapus requirement private key untuk signal:

```python
if args.live and not os.environ.get("POLYMARKET_PRIVATE_KEY"):
    print("❌ --live requires POLYMARKET_PRIVATE_KEY env var.")
    sys.exit(1)
# Signal mode doesn't need key
```

## Usage setelah patch

```bash
# Signal mode — gak butuh wallet, kirim Telegram alert dengan validasi Polymarket
python3 poly_btc_5m_lock_50.py --signal --telegram-token YOUR_TOKEN --chat-id YOUR_ID

# Paper mode (default) — simulasi doang
python3 poly_btc_5m_lock_50.py --bankroll 10 --spread 50

# Full live mode — butuh wallet + CLOB auth + proxy
POLYMARKET_PRIVATE_KEY=0x... python3 poly_btc_5m_lock_50.py --live
```
