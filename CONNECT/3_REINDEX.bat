@echo off
title Re-index Videos
color 0E
echo ============================================================
echo   RE-INDEX COURSE VIDEOS
echo   Run this if new videos were added to the course
echo ============================================================
echo.

set PYTHONIOENCODING=utf-8
set PATH=C:\Users\Admin1\AppData\Local\Microsoft\WinGet\Links;%PATH%

:: Delete old index
if exist "%~dp0..\video_index.json" (
    del "%~dp0..\video_index.json"
    echo   Deleted old index.
)

echo   Next time you run 2_DOWNLOAD.bat it will re-scan all folders.
echo.
pause
