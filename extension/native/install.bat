@echo off
setlocal enabledelayedexpansion
REM Native messaging host installer for Windows
REM Registers the host via Windows Registry (HKEY_CURRENT_USER)

set HOST_NAME=com.reclip.server
set SCRIPT_DIR=%~dp0
set HOST_BAT=%SCRIPT_DIR%reclip-host.bat
set MANIFEST_PATH=%SCRIPT_DIR%manifest.windows.json

echo.
echo ===========================================
echo   ClipDown Native Host Installer (Windows)
echo ===========================================
echo.
echo 1. Open chrome://extensions in Chrome
echo 2. Enable Developer Mode (top right toggle)
echo 3. Find ClipDown extension and copy its ID
echo    (looks like: abcdefghijklmnopqrstuvwxyz...)
echo.

set /p EXT_ID="Paste your extension ID: "

if "!EXT_ID!"=="" (
    echo Error: Extension ID is required
    pause
    exit /b 1
)

REM Convert backslash paths to JSON-safe (escape)
set "ESCAPED_PATH=%HOST_BAT:\=\\%"

REM Write manifest JSON
(
echo {
echo   "name": "%HOST_NAME%",
echo   "description": "ClipDown server controller",
echo   "path": "!ESCAPED_PATH!",
echo   "type": "stdio",
echo   "allowed_origins": [
echo     "chrome-extension://!EXT_ID!/"
echo   ]
echo }
) > "%MANIFEST_PATH%"

REM Register in Windows Registry for Chrome
reg add "HKCU\Software\Google\Chrome\NativeMessagingHosts\%HOST_NAME%" /ve /t REG_SZ /d "%MANIFEST_PATH%" /f >nul

if errorlevel 1 (
    echo Error: Failed to register in Windows Registry
    pause
    exit /b 1
)

echo.
echo ===========================================
echo   Installed successfully!
echo ===========================================
echo.
echo   Manifest: %MANIFEST_PATH%
echo   Registry: HKCU\Software\Google\Chrome\NativeMessagingHosts\%HOST_NAME%
echo   Extension: %EXT_ID%
echo.
echo Reload the ClipDown extension in Chrome.
echo.
pause
