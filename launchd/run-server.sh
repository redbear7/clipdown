#!/bin/bash
# Self-healing wrapper for ClipDown server
set -e
REPO_DIR="$HOME/clipdown"
cd "$REPO_DIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# Recreate venv if missing/broken
if [ ! -x "$REPO_DIR/venv/bin/python3" ]; then
    echo "$(date): venv missing, recreating..."
    rm -rf "$REPO_DIR/venv"
    python3 -m venv "$REPO_DIR/venv"
    "$REPO_DIR/venv/bin/pip" install -q --upgrade pip
    "$REPO_DIR/venv/bin/pip" install -q flask yt-dlp mutagen
fi

if ! "$REPO_DIR/venv/bin/python3" -c "import flask" 2>/dev/null; then
    "$REPO_DIR/venv/bin/pip" install -q flask yt-dlp mutagen
fi

exec "$REPO_DIR/venv/bin/python3" "$REPO_DIR/app.py"
