@echo off
title Pro Trader - Online Tunnel
echo.
echo  Starting Pro Trader with public internet URL...
echo.
cd /d "%~dp0"

:: Start the app in background on port 5000
start "ProTrader" /B py app.py

timeout /t 8 /nobreak >nul

:: Try Cloudflare Tunnel (installs automatically if missing)
where cloudflared >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Installing Cloudflare Tunnel...
    winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
)

echo.
echo  ============================================
echo   Your public URL will appear below:
echo   Share it to access from anywhere
echo  ============================================
echo.

cloudflared tunnel --url http://127.0.0.1:5000

pause