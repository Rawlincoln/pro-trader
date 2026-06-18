@echo off
title Pro Trader — Online (Cloudflare Tunnel)
cd /d "%~dp0"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

set CF=%~dp0cloudflared.exe
if not exist "%CF%" (
    if exist "%LOCALAPPDATA%\Temp\cloudflared.exe" (
        copy /Y "%LOCALAPPDATA%\Temp\cloudflared.exe" "%CF%" >nul
    ) else (
        echo Downloading cloudflared tunnel...
        powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '%CF%'"
    )
)

echo Starting app on port 5000...
start "ProTrader App" /MIN py "%~dp0app.py"

echo Waiting for server...
timeout /t 8 /nobreak >nul

echo.
echo ============================================
echo   PUBLIC URL will appear below (trycloudflare.com)
echo   Keep this window OPEN while sharing the link
echo ============================================
echo.

"%CF%" tunnel --url http://localhost:5000

pause