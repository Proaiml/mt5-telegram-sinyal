@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
where python >nul 2>nul || set "PY=py"
if not exist config.json copy config.example.json config.json >nul
%PY% mt5_telegram.py --kuru
pause
