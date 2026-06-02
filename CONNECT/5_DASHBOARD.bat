@echo off
title Download Dashboard
color 0E
echo ============================================================
echo   DOWNLOAD DASHBOARD
echo   Opens web UI to select and download videos
echo ============================================================
echo.

set PYTHONIOENCODING=utf-8
set PATH=%~dp0tools;%PATH%

cd /d "%~dp0.."
python dashboard.py

pause
