@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MT5 Telegram Sinyal
if not exist config.json (
  echo config.json bulunamadi. config.example.json dosyasini config.json olarak kopyalayip doldurun.
  pause
  exit /b 1
)
:dongu
python mt5_telegram.py
if errorlevel 1 (
  echo Program durdu. 10 saniye sonra yeniden baslatiliyor... Kapatmak icin pencereyi kapatin.
  timeout /t 10 /nobreak >nul
  goto dongu
)
pause
