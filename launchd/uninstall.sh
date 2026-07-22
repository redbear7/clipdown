#!/bin/bash
# Remove ClipDown launchd auto-start

set -e
TARGET="$HOME/Library/LaunchAgents/com.bangju.clipdown.plist"

echo "=== Removing ClipDown auto-start ==="

if [ -f "$TARGET" ]; then
    launchctl unload "$TARGET" 2>/dev/null || true
    rm -f "$TARGET"
    echo "  Removed: $TARGET"
else
    echo "  No installation found at $TARGET"
fi

# Stop server if still running
EXISTING=$(lsof -ti :8899 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
    echo "  Stopping server (PID: $EXISTING)..."
    kill -9 $EXISTING 2>/dev/null || true
fi

echo ""
echo "ClipDown will no longer auto-start. You can still run it manually with reclip.sh."
