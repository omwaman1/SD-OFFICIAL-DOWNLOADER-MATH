@echo off
title Classplus Bridge - Step 1
color 0A
echo ============================================================
echo   CLASSPLUS BRIDGE STARTER
echo   This starts the Frida bridge on your phone
echo ============================================================
echo.

:: Use local tools
set PATH=%~dp0tools;%PATH%

:: Check device
echo   [1/5] Checking device...
adb devices 2>nul | findstr /C:"device" >nul 2>&1
if errorlevel 1 (
    echo   ERROR: No device found! Connect your phone via USB.
    pause
    exit /b
)
echo   OK - Device connected
echo.

:: Force stop app
echo   [2/5] Restarting app...
adb shell am force-stop co.shield.yyxdj
timeout /t 3 /nobreak >nul

:: Start app
echo   [3/5] Launching app...
adb shell am start -n co.shield.yyxdj/co.classplus.app.ui.common.splash.KSplashActivity
echo   Waiting 12 seconds for app to load...
timeout /t 12 /nobreak >nul

:: Port forward
echo   [4/5] Port forwarding...
adb forward tcp:8899 tcp:8899
echo   OK - Port 8899 forwarded
echo.

:: Launch Frida
echo   [5/5] Launching Frida hooks...
echo   (Keep this window open!)
echo ============================================================
frida -U Gadget -l "%~dp0..\hook_combined.js" --eternalize

pause
