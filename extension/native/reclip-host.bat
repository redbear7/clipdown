@echo off
REM Native messaging host wrapper for Windows
REM Uses bundled embedded Python first, falls back to system python

setlocal
cd /d "%~dp0\..\..\"

set EMBEDDED_PY=%~dp0..\..\runtime\python\python.exe

if exist "%EMBEDDED_PY%" (
    "%EMBEDDED_PY%" "%~dp0reclip-host.py"
) else (
    python "%~dp0reclip-host.py"
)
