@echo off
title Key Capture Mode
color 0D
echo ============================================================
echo   KEY CAPTURE MODE
echo   Play videos rapidly - keys saved, no downloading!
echo ============================================================
echo.

set PYTHONIOENCODING=utf-8
set PATH=%~dp0tools;%PATH%

:: Check bridge
python -c "import requests; requests.get('http://127.0.0.1:8899/health', timeout=3)" >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Bridge not running!
    echo   Run 1_START_BRIDGE.bat first.
    pause
    exit /b
)

cd /d "%~dp0.."
python capture_keys.py

pause
