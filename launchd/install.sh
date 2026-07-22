#!/bin/bash
# Install launchd agent to auto-start ClipDown on macOS login

set -e
cd "$(dirname "$0")"

REPO_DIR=$(cd .. && pwd)
PLIST_NAME="com.bangju.clipdown.plist"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
TARGET="$LAUNCH_AGENTS/$PLIST_NAME"

echo ""
echo "=========================================="
echo "  ClipDown launchd Auto-Start Installer"
echo "=========================================="
echo ""

# Read password from config.json
PWD=""
if [ -f "$REPO_DIR/config.json" ]; then
    PWD=$(python3 -c "import json; print(json.load(open('$REPO_DIR/config.json')).get('password',''))" 2>/dev/null || echo "")
fi

if [ -z "$PWD" ]; then
    read -p "Enter password for ClipDown (or leave empty for no auth): " PWD
    if [ -n "$PWD" ]; then
        cat > "$REPO_DIR/config.json" <<EOF
{"username": "clipdown", "password": "$PWD"}
EOF
        echo "Password saved to config.json"
    fi
fi

# Ensure venv exists
if [ ! -d "$REPO_DIR/venv" ]; then
    echo "Creating venv..."
    python3 -m venv "$REPO_DIR/venv"
    "$REPO_DIR/venv/bin/pip" install -q flask yt-dlp mutagen
fi

# Stop existing service if present
if launchctl list | grep -q "com.bangju.clipdown"; then
    echo "Stopping existing service..."
    launchctl unload "$TARGET" 2>/dev/null || true
fi

# Generate plist with absolute paths
mkdir -p "$LAUNCH_AGENTS"
sed -e "s|__REPO_DIR__|$REPO_DIR|g" \
    -e "s|__PASSWORD__|$PWD|g" \
    com.bangju.clipdown.plist > "$TARGET"

# Load
launchctl load "$TARGET"

sleep 2

echo ""
echo "=========================================="
echo "  Installed!"
echo "=========================================="
echo ""
echo "  Service:    com.bangju.clipdown"
echo "  Plist:      $TARGET"
echo "  Logs:       /tmp/clipdown.out.log"
echo "              /tmp/clipdown.err.log"
echo ""
echo "  Server URL: http://localhost:8899"
if [ -n "$PWD" ]; then
echo "  Username:   clipdown"
echo "  Password:   $PWD"
fi
echo ""

# Verify it's running
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8899/ | grep -q "200\|401"; then
    echo "  Status: RUNNING ✓"
else
    echo "  Status: NOT REACHABLE — check /tmp/clipdown.err.log"
fi

echo ""
echo "Commands:"
echo "  launchctl list | grep clipdown    # check status"
echo "  launchctl unload $TARGET    # stop"
echo "  launchctl load $TARGET      # start"
echo "  ./uninstall.sh                    # remove auto-start"
echo ""
