@echo off
REM BTC 5m LOCK Alert Bot Launcher for Windows
REM Usage: double-click or run in CMD

set SCRIPT_DIR=%~dp0
set TOKEN=YOUR_BOT_TOKEN
set CHAT_ID=YOUR_CHAT_ID

echo Starting BTC 5m LOCK Alert Bot...
echo Logs: %TEMP%\poly_lock_alert.log

cd /d "%SCRIPT_DIR%"
python3 -u poly_btc_5m_lock_alert.py --telegram-token %TOKEN% --chat-id %CHAT_ID% > %TEMP%\poly_lock_alert.log 2>&1

pause