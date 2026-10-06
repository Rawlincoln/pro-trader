@echo off
echo Starting Pro Trader Dashboard...
echo   EUR/USD  -> http://127.0.0.1:5000/
echo   Gold     -> http://127.0.0.1:5000/gold
echo   Bitcoin  -> http://127.0.0.1:5000/bitcoin
echo   Invest   -> http://127.0.0.1:5000/investments
cd /d "%~dp0"
py app.py
pause