#!/bin/bash
# Tiny, rarely-changing entry point for the CyberHQ GRC Data Migration Tool.
# Everything that can change (Python provisioning, updating, running the
# app) lives in bootstrap.py, fetched fresh from GitHub every launch - this
# file's only job is getting `uv` in place and handing off to that script.
set -e

APP_DIR="$HOME/.grc-migration-tool"
BIN_DIR="$APP_DIR/bin"
mkdir -p "$BIN_DIR" "$APP_DIR/repo/launcher"

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$BIN_DIR/uv" ]; then
  echo "Setting up (first run only)..."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$BIN_DIR" INSTALLER_NO_MODIFY_PATH=1 sh
fi

if command -v uv >/dev/null 2>&1; then
  UV=uv
else
  UV="$BIN_DIR/uv"
fi

curl -LsSf "https://raw.githubusercontent.com/AvertroJoe/Customer-Migration-Tool/main/launcher/bootstrap.py" \
  -o "$APP_DIR/repo/launcher/bootstrap.py"

exec "$UV" run --python 3.12 "$APP_DIR/repo/launcher/bootstrap.py"
