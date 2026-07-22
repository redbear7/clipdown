#!/bin/bash
# Installer for ReClip native messaging host

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_NAME="com.reclip.server"
HOST_SCRIPT="$SCRIPT_DIR/reclip-host.sh"

# Make scripts executable
chmod +x "$HOST_SCRIPT" "$SCRIPT_DIR/reclip-host.py"

echo ""
echo "==========================================="
echo "  ReClip Native Host Installer"
echo "==========================================="
echo ""
echo "1. Open chrome://extensions in Chrome"
echo "2. Enable Developer Mode (top right)"
echo "3. Find ReClip extension and copy its ID"
echo ""
read -p "Paste your extension ID: " EXT_ID

if [ -z "$EXT_ID" ]; then
    echo "Error: Extension ID is required"
    exit 1
fi

# Install for both Chrome and Chrome Canary/Beta
INSTALLED=0
for BROWSER_DIR in \
    "$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts" \
    "$HOME/Library/Application Support/Google/Chrome Beta/NativeMessagingHosts" \
    "$HOME/Library/Application Support/Google/Chrome Canary/NativeMessagingHosts" \
    "$HOME/Library/Application Support/Chromium/NativeMessagingHosts"; do

    PARENT="$(dirname "$BROWSER_DIR")"
    if [ -d "$PARENT" ]; then
        mkdir -p "$BROWSER_DIR"
        MANIFEST_PATH="$BROWSER_DIR/$HOST_NAME.json"
        cat > "$MANIFEST_PATH" <<EOF
{
  "name": "$HOST_NAME",
  "description": "ReClip server controller",
  "path": "$HOST_SCRIPT",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXT_ID/"
  ]
}
EOF
        echo "  ✓ $MANIFEST_PATH"
        INSTALLED=$((INSTALLED + 1))
    fi
done

if [ "$INSTALLED" -eq 0 ]; then
    echo "Error: No Chrome installation found"
    exit 1
fi

echo ""
echo "✓ Installed for $INSTALLED browser(s)"
echo "✓ Allowed extension: $EXT_ID"
echo ""
echo "Reload the ReClip extension in Chrome to apply."
echo ""
