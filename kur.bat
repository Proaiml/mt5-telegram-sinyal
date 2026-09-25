@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
where python >nul 2>nul || set "PY=py"
%PY% -m pip install -r requirements.txt
if errorlevel 1 (echo Kurulum basarisiz. Python kurulu ve PATH'e ekli mi?) else (echo Kurulum tamam.)
pause
