@echo off
title Pro Trader - GitHub Auto-Sync
cd /d "%~dp0"
echo.
echo  Auto-syncing to https://github.com/Rawlincoln/pro-trader
echo  Saves are pushed ~8 seconds after you stop editing.
echo  Keep this window OPEN. Close it to stop auto-sync.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch-github.ps1"
pause