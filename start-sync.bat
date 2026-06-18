@echo off
title Pro Trader — GitHub Auto-Sync
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch-github.ps1"