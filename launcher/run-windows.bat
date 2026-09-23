@echo off
REM Tiny, rarely-changing entry point for the CyberHQ GRC Data Migration
REM Tool. Everything that can change (Python provisioning, updating,
REM running the app) lives in bootstrap.py, fetched fresh from GitHub
REM every launch - this file's only job is getting `uv` in place and
REM handing off to that script.
setlocal

set "APP_DIR=%USERPROFILE%\.grc-migration-tool"
set "BIN_DIR=%APP_DIR%\bin"
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"
if not exist "%APP_DIR%\repo\launcher" mkdir "%APP_DIR%\repo\launcher"

where uv >nul 2>nul
if errorlevel 1 (
  if not exist "%BIN_DIR%\uv.exe" (
    echo Setting up ^(first run only^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$env:UV_INSTALL_DIR='%BIN_DIR%'; $env:INSTALLER_NO_MODIFY_PATH='1'; irm https://astral.sh/uv/install.ps1 | iex"
  )
  set "PATH=%BIN_DIR%;%PATH%"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/AvertroJoe/Customer-Migration-Tool/main/launcher/bootstrap.py' -OutFile '%APP_DIR%\repo\launcher\bootstrap.py'"

uv run --python 3.12 "%APP_DIR%\repo\launcher\bootstrap.py"
pause
