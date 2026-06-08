module.exports = {
  apps: [
    {
      name:        "polylock-bot",
      script:      "python3",
      args:        "-u /root/.openclaw/workspace/polymarket/scripts/bot_clean.py --live",
      cwd:         "/root/.openclaw/workspace/polymarket/scripts",
      interpreter: "none",
      autorestart: true,
      watch:       false,
      max_memory_restart: "150M",
      restart_delay: 5000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1",
      },
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file:  "/root/.pm2/logs/polylock-bot-error.log",
      out_file:    "/root/.pm2/logs/polylock-bot-out.log",
    },
  ],
};
