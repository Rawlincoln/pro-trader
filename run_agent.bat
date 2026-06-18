@echo off
title EUR/USD XM Trading Agent
echo.
echo  EUR/USD XM Trading Agent
echo  ========================
echo  Stop loss is REQUIRED on every trade.
echo  Default mode: DRY RUN (simulation)
echo.
cd /d "%~dp0"
py agent\trader.py
pause