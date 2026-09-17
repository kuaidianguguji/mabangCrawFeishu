@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Mabang ERP - Feishu Sync
pushd "%~dp0"
if errorlevel 1 (
    echo Unable to enter the project directory.
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "launch_result=%errorlevel%"
popd
echo.
if not "%launch_result%"=="0" echo Startup or task failed. Please read the error above.
pause
exit /b %launch_result%
