#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# mac-local-dictation: macOS LaunchAgent Service Configuration
# -----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLIST_LABEL="com.${USER}.local-dictation"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
VENV_PYTHON="${SKILL_DIR}/venv/bin/python"
DICTATE_PY="${SKILL_DIR}/dictate.py"
LOG_PATH="${SKILL_DIR}/dictate.log"

mkdir -p "${HOME}/Library/LaunchAgents"

echo "  [+] Configuring LaunchAgent: ${PLIST_LABEL}..."

cat <<EOF > "${PLIST_PATH}"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>${DICTATE_PY}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SKILL_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_PATH}</string>
</dict>
</plist>
EOF

# Reload service via launchctl
echo "  [+] Reloading launchd service..."
launchctl unload "${PLIST_PATH}" 2>/dev/null || true
pkill -f "${DICTATE_PY}" 2>/dev/null || true
sleep 1
launchctl load "${PLIST_PATH}"

echo "  [✓] LaunchAgent active: ${PLIST_PATH}"
