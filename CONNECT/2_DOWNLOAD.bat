@echo off
title Classplus Downloader - Step 2
color 0B
echo ============================================================
echo   CLASSPLUS VIDEO DOWNLOADER
echo   Play a video in app ^> it downloads here!
echo ============================================================
echo.

:: Use local tools
set PYTHONIOENCODING=utf-8
set PATH=%~dp0tools;%PATH%

:: Check bridge
echo   Checking bridge connection...
python -c "import requests; requests.get('http://127.0.0.1:8899/health', timeout=3)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: Bridge not running!
    echo   Run 1_START_BRIDGE.bat first and keep it open.
    echo.
    pause
    exit /b
)
echo   OK - Bridge connected
echo.

:: Run downloader
cd /d "%~dp0.."
python play_and_save.py

pause
