#!/usr/bin/env node
'use strict';

const http  = require('http');
const https = require('https');
const fs    = require('fs');
const path  = require('path');
const net   = require('net');

// ── Chainlink BTC/USD proxy (no CORS issues from server side) ───────────────
const CHAINLINK_CONTRACT = '0xc907E116054Ad103354f2D350FD2514433D57F6f';
const CHAINLINK_SELECTOR = '0x50d25bcd'; // latestAnswer()
const POLYGON_RPCS = [
  'https://1rpc.io/matic',
  'https://polygon.drpc.org',
  'https://rpc-mainnet.matic.quiknode.pro',
];

let btcPriceCache = { price: 0, source: 'init', ts: 0 };

function fetchChainlinkPrice() {
  const payload = JSON.stringify({
    jsonrpc: '2.0', method: 'eth_call',
    params: [{ to: CHAINLINK_CONTRACT, data: CHAINLINK_SELECTOR }, 'latest'],
    id: 1,
  });
  const rpcs = [...POLYGON_RPCS];

  function tryNext() {
    if (!rpcs.length) {
      // Fallback: Binance
      const binanceReq = https.get(
        'https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT',
        { headers: { 'User-Agent': 'polylock/1.0' } },
        (r) => {
          let body = '';
          r.on('data', d => body += d);
          r.on('end', () => {
            try {
              const p = parseFloat(JSON.parse(body).price);
              if (p > 1000) btcPriceCache = { price: p, source: 'Binance', ts: Date.now() };
            } catch {}
          });
        }
      );
      binanceReq.on('error', () => {});
      return;
    }
    const rpc = rpcs.shift();
    const url = new URL(rpc);
    const opts = {
      hostname: url.hostname,
      port: url.port || 443,
      path: url.pathname,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'User-Agent': 'polylock/1.0', 'Content-Length': Buffer.byteLength(payload) },
    };
    const mod = url.protocol === 'https:' ? https : http;
    const req = mod.request(opts, (r) => {
      let body = '';
      r.on('data', d => body += d);
      r.on('end', () => {
        try {
          const res  = JSON.parse(body);
          const hex  = res?.result;
          if (!hex || hex === '0x' || hex === '0x0') { tryNext(); return; }
          const price = parseInt(hex, 16) / 1e8;
          if (price < 1000 || price > 1000000) { tryNext(); return; } // sanity
          btcPriceCache = { price, source: 'Chainlink', ts: Date.now() };
        } catch { tryNext(); }
      });
    });
    req.on('error', () => tryNext());
    req.write(payload);
    req.end();
  }
  tryNext();
}

// Poll Chainlink every 5s
fetchChainlinkPrice();
setInterval(fetchChainlinkPrice, 5000);

const PORT    = process.env.PORT || 3456;
const PUBLIC  = path.join(__dirname);
const TTYD_PORT = 3457;

// Simple auth token — change this
const AUTH_TOKEN = process.env.TERMINAL_TOKEN || 'polylock2025';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png':  'image/png',
  '.ico':  'image/x-icon',
  '.svg':  'image/svg+xml',
};

// ── WebSocket proxy helper ──────────────────────────────────────────────────
function proxyWebSocket(req, socket, head) {
  const target = net.createConnection(TTYD_PORT, '127.0.0.1', () => {
    // Rebuild the HTTP upgrade request, stripping the /terminal prefix
    const newUrl = req.url.replace(/^\/terminal/, '') || '/';
    const headers = Object.entries(req.headers)
      .map(([k, v]) => `${k}: ${v}`)
      .join('\r\n');
    target.write(
      `${req.method} ${newUrl} HTTP/${req.httpVersion}\r\n${headers}\r\n\r\n`
    );
    if (head && head.length) target.write(head);
    target.pipe(socket);
    socket.pipe(target);
  });
  target.on('error', () => socket.destroy());
  socket.on('error', () => target.destroy());
}

// ── HTTP proxy to ttyd ──────────────────────────────────────────────────────
function proxyToTtyd(req, res) {
  const targetPath = req.url.replace(/^\/terminal/, '') || '/';
  const options = {
    hostname: '127.0.0.1',
    port: TTYD_PORT,
    path: targetPath,
    method: req.method,
    headers: { ...req.headers, host: `127.0.0.1:${TTYD_PORT}` },
  };
  const proxy = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });
  proxy.on('error', () => { res.writeHead(502); res.end('ttyd unavailable'); });
  req.pipe(proxy);
}

// ── Main HTTP server ────────────────────────────────────────────────────────
const server = http.createServer((req, res) => {
  if (req.method !== 'GET' && req.method !== 'POST') {
    res.writeHead(405); res.end(); return;
  }

  // /api/btc-price → serve cached Chainlink price (no CORS issues)
  if (req.url === '/api/btc-price') {
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify(btcPriceCache));
    return;
  }

  // /api/windows → baca windows.jsonl dari bot, kirim sebagai JSON array
  if (req.url === '/api/windows') {
    const wFile = path.join(__dirname, '..', 'logs', 'windows.jsonl');
    fs.readFile(wFile, 'utf8', (err, data) => {
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      if (err) { res.end('[]'); return; }
      // Parse JSONL — ambil entry terbaru per window start (resolve PENDING)
      const map = new Map();
      data.trim().split('\n').forEach(line => {
        try {
          const w = JSON.parse(line);
          // Selalu overwrite dengan entry terbaru (resolve PENDING → WIN/LOSS)
          map.set(w.start, w);
        } catch {}
      });
      const windows = [...map.values()].reverse(); // newest first
      res.end(JSON.stringify(windows));
    });
    return;
  }

  // /api/stats → baca stats.json dari bot
  if (req.url === '/api/stats') {
    const sFile = path.join(__dirname, '..', 'logs', 'stats.json');
    fs.readFile(sFile, 'utf8', (err, data) => {
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      if (err) { res.end('{}'); return; }
      res.end(data);
    });
    return;
  }

  // /terminal/* → proxy to ttyd (with token check)
  if (req.url.startsWith('/terminal') && !req.url.startsWith('/terminal-login')) {
    const url = new URL(req.url, `http://localhost`);
    const token = url.searchParams.get('token');
    // Allow if token in URL, or if session cookie is set
    const cookie = req.headers.cookie || '';
    const hasSession = cookie.includes(`term_auth=${AUTH_TOKEN}`);
    if (!hasSession && token !== AUTH_TOKEN) {
      res.writeHead(302, { Location: `/terminal-login.html` });
      res.end(); return;
    }
    // Set session cookie on first valid token
    if (token === AUTH_TOKEN && !hasSession) {
      res.setHeader('Set-Cookie', `term_auth=${AUTH_TOKEN}; Path=/; HttpOnly; SameSite=Strict`);
    }
    proxyToTtyd(req, res);
    return;
  }

  // Static files
  let urlPath = req.url.split('?')[0];
  if (urlPath === '/') urlPath = '/index.html';
  const filePath = path.join(PUBLIC, urlPath);
  if (!filePath.startsWith(PUBLIC)) { res.writeHead(403); res.end(); return; }

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(err.code === 'ENOENT' ? 404 : 500);
      res.end(err.code === 'ENOENT' ? 'Not found' : 'Server error');
      return;
    }
    const ext = path.extname(filePath);
    res.writeHead(200, {
      'Content-Type': MIME[ext] || 'application/octet-stream',
      'Cache-Control': 'no-store',
    });
    res.end(data);
  });
});

// ── WebSocket upgrade (for ttyd terminal) ──────────────────────────────────
server.on('upgrade', (req, socket, head) => {
  if (req.url.startsWith('/terminal')) {
    const cookie = req.headers.cookie || '';
    const hasSession = cookie.includes(`term_auth=${AUTH_TOKEN}`);
    const url = new URL(req.url, 'http://localhost');
    const token = url.searchParams.get('token');
    if (!hasSession && token !== AUTH_TOKEN) {
      socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
      socket.destroy(); return;
    }
    proxyWebSocket(req, socket, head);
  }
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`[polylock-dashboard] http://0.0.0.0:${PORT}`);
  console.log(`[polylock-terminal]  http://0.0.0.0:${PORT}/terminal/?token=${AUTH_TOKEN}`);
});

process.on('SIGINT',  () => { server.close(); process.exit(0); });
process.on('SIGTERM', () => { server.close(); process.exit(0); });
