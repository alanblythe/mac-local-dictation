#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# mac-local-dictation: Turnkey Uninstallation Script
# -----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLIST_LABEL="com.${USER}.local-dictation"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"

echo "==> Uninstalling mac-local-dictation..."

# 1. Unload & remove LaunchAgent
if [ -f "${PLIST_PATH}" ]; then
    echo "  [+] Unloading launchd service..."
    launchctl unload "${PLIST_PATH}" 2>/dev/null || true
    rm -f "${PLIST_PATH}"
    echo "  [✓] Removed ${PLIST_PATH}"
fi

# 2. Kill running processes
echo "  [+] Terminating running dictate processes..."
pkill -f "${SKILL_DIR}/dictate.py" 2>/dev/null || true
pkill -f "dictate\.py" 2>/dev/null || true

# 3. Remove CLI launcher
WRAPPER_FILE="${HOME}/.local/bin/dictate"
if [ -f "${WRAPPER_FILE}" ] || [ -L "${WRAPPER_FILE}" ]; then
    rm -f "${WRAPPER_FILE}"
    echo "  [✓] Removed CLI launcher ${WRAPPER_FILE}"
fi

# 4. Remove virtual environment & logs
if [ -d "${SKILL_DIR}/venv" ]; then
    echo "  [+] Removing virtual environment at ${SKILL_DIR}/venv..."
    rm -rf "${SKILL_DIR}/venv"
fi

if [ -f "${SKILL_DIR}/dictate.log" ]; then
    rm -f "${SKILL_DIR}/dictate.log"
fi

echo "==> Uninstallation complete. Service stopped and environment cleaned."
