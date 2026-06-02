// ── Load .env if exists ─────────────────────────────────
const fs   = require('fs');
const path = require('path');
const ENV_FILE = path.join(__dirname, 'scripts', '.env');

function loadEnv(filePath) {
  if (!fs.existsSync(filePath)) return {};
  return Object.fromEntries(
    fs.readFileSync(filePath, 'utf8')
      .split('\n')
      .filter(l => l.trim() && !l.startsWith('#') && l.includes('='))
      .map(l => { const i = l.indexOf('='); return [l.slice(0,i).trim(), l.slice(i+1).trim()]; })
      .filter(([k, v]) => k && v)
  );
}

const dotenv = loadEnv(ENV_FILE);

// Proxy: pakai PROXY_URL dari .env atau env system
const PROXY_URL = dotenv.PROXY_URL || process.env.PROXY_URL || '';

// Shared env untuk semua proses bot
const botEnv = {
  ...(dotenv.POLYMARKET_PRIVATE_KEY ? { POLYMARKET_PRIVATE_KEY: dotenv.POLYMARKET_PRIVATE_KEY } : {}),
  ...(dotenv.POLY_RPC               ? { POLY_RPC:               dotenv.POLY_RPC }               : {}),
  ...(PROXY_URL ? {
    HTTP_PROXY:  PROXY_URL,
    HTTPS_PROXY: PROXY_URL,
    http_proxy:  PROXY_URL,
    https_proxy: PROXY_URL,
  } : {}),
};

module.exports = {
  apps: [
    // ── Dashboard (Node.js static server) ──────────────
    {
      name:        'polylock-dashboard',
      script:      './dashboard/server.js',
      cwd:         '/root/polymarket',
      instances:   1,
      autorestart: true,
      watch:       false,
      max_memory_restart: '150M',
      env: {
        NODE_ENV: 'production',
        PORT:     3456,
      },
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      error_file:  '/root/polymarket/logs/dashboard-error.log',
      out_file:    '/root/polymarket/logs/dashboard-out.log',
    },

    // ── Web Terminal (ttyd) ────────────────────────────
    {
      name:        'polylock-terminal',
      script:      'ttyd',
      args:        '--port 3457 --interface 127.0.0.1 bash',
      cwd:         '/root/polymarket',
      interpreter: 'none',
      autorestart: true,
      watch:       false,
      max_memory_restart: '80M',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      error_file:  '/root/polymarket/logs/terminal-error.log',
      out_file:    '/root/polymarket/logs/terminal-out.log',
    },

    // ── Bot A — $100 threshold PAPER ───────────────────
    {
      name:        'polylock-bot-100',
      script:      'python3',
      args:        '-u scripts/poly_btc_5m_autotrader.py --bankroll 10 --spread 100 --daily-stop 5 --max-trades 20',
      cwd:         '/root/polymarket',
      interpreter: 'none',
      autorestart: true,
      watch:       false,
      max_memory_restart: '100M',
      restart_delay: 5000,
      env:         botEnv,
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      error_file:  '/root/polymarket/logs/bot100-error.log',
      out_file:    '/root/polymarket/logs/bot100-out.log',
    },

    // ── Bot B — $50 threshold PAPER (A/B test) ─────────
    {
      name:        'polylock-bot-50',
      script:      'python3',
      args:        '-u scripts/poly_btc_5m_lock_50.py --bankroll 10 --spread 50 --daily-stop 5 --max-trades 20',
      cwd:         '/root/polymarket',
      interpreter: 'none',
      autorestart: true,
      watch:       false,
      max_memory_restart: '100M',
      restart_delay: 5000,
      env:         botEnv,
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      error_file:  '/root/polymarket/logs/bot50-error.log',
      out_file:    '/root/polymarket/logs/bot50-out.log',
    },
  ],
};
