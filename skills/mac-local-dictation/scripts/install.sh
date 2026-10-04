#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# mac-local-dictation: Turnkey Installation & Verification Script
# -----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DICTATE_PY="${SKILL_DIR}/dictate.py"
REQUIREMENTS_FILE="${SKILL_DIR}/requirements.txt"
VENV_DIR="${SKILL_DIR}/venv"

echo "==> Installing mac-local-dictation..."

# 1. Architecture Check (Apple Silicon required for MLX)
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
    echo "❌ Error: mac-local-dictation requires an Apple Silicon Mac (arm64). Current architecture: $ARCH"
    exit 1
fi

PREF_AUDIO_MODE=""
PREF_HOTKEY=""
PREF_AUTOSTART=""
SKIP_MODEL_DOWNLOAD=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --audio-mode)
            PREF_AUDIO_MODE="$2"
            shift 2
            ;;
        --hotkey)
            PREF_HOTKEY="$2"
            shift 2
            ;;
        --autostart)
            PREF_AUTOSTART="$2"
            shift 2
            ;;
        --skip-model-download)
            SKIP_MODEL_DOWNLOAD=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# Helper: test if a Python candidate can generate a working virtual environment
can_create_venv() {
    local candidate="$1"
    local test_dir
    test_dir="$(mktemp -d /tmp/test_venv_XXXXXX)"
    if "$candidate" -m venv "$test_dir" >/dev/null 2>&1 && "$test_dir/bin/python" -c "import encodings" >/dev/null 2>&1; then
        rm -rf "$test_dir"
        return 0
    else
        rm -rf "$test_dir"
        return 1
    fi
}

# 2. Python Discovery (Look for Python 3.11 or 3.12)
PYTHON_BIN=""
CANDIDATES=(
    "$(command -v python3.11 || true)"
    "$(command -v python3.12 || true)"
    "/opt/homebrew/bin/python3.11"
    "/opt/homebrew/bin/python3"
    "$(command -v python3 || true)"
)

for cand in "${CANDIDATES[@]}"; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then
        real_cand="$(python3 -c "import os; print(os.path.realpath('$cand'))" 2>/dev/null || echo "$cand")"
        if can_create_venv "$real_cand"; then
            PYTHON_BIN="$real_cand"
            echo "  [✓] Selected Python: $PYTHON_BIN"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Error: Could not find a Python 3.11+ interpreter capable of creating virtual environments."
    echo "   Please install Python via Homebrew (brew install python@3.11) or python.org."
    exit 1
fi

# 3. Version Verification
ACTUAL_VER=$("${PYTHON_BIN}" "${DICTATE_PY}" --version 2>/dev/null || echo "unknown")
echo "  [✓] Verified dictate.py version: v${ACTUAL_VER}"

# -----------------------------------------------------------------------------
# Configuration Preferences (Interactive or via CLI flags)
# -----------------------------------------------------------------------------
CONFIG_FILE="${HOME}/.dictate_config.json"

# Question 1: Audio Privacy Mode
if [ -z "$PREF_AUDIO_MODE" ]; then
    if [ -t 0 ]; then
        echo ""
        echo "Audio Capture Mode:"
        echo "  1) Instant Stream (0ms latency, orange mic dot stays illuminated) [Default]"
        echo "  2) On-Demand (Mic privacy, mic only opened while holding hotkey)"
        read -r -p "Select audio mode [1/2, default 1]: " mode_choice
        case "$mode_choice" in
            2|on_demand) PREF_AUDIO_MODE="on_demand" ;;
            *) PREF_AUDIO_MODE="stream" ;;
        esac
    else
        PREF_AUDIO_MODE="stream"
    fi
fi
echo "  [✓] Audio mode: ${PREF_AUDIO_MODE}"

# Question 2: Activation Hotkey
if [ -z "$PREF_HOTKEY" ]; then
    if [ -t 0 ]; then
        echo ""
        echo "Activation Hotkey:"
        echo "  1) Right Command (⌘) [Superwhisper standard, maximum keyboard compatibility] [Default]"
        echo "  2) Right Option (⌥) [Alternative]"
        read -r -p "Select hotkey [1/2, default 1]: " hk_choice
        case "$hk_choice" in
            2|alt_r) PREF_HOTKEY="alt_r" ;;
            *) PREF_HOTKEY="cmd_r" ;;
        esac
    else
        PREF_HOTKEY="cmd_r"
    fi
fi
echo "  [✓] Hotkey: ${PREF_HOTKEY}"

# Question 3: Auto-start on Login (LaunchAgent)
if [ -z "$PREF_AUTOSTART" ]; then
    if [ -t 0 ]; then
        echo ""
        read -r -p "Start local dictation automatically on login as a background service? [Y/n]: " as_choice
        case "$as_choice" in
            [nN][oO]|[nN]) PREF_AUTOSTART="false" ;;
            *) PREF_AUTOSTART="true" ;;
        esac
    else
        PREF_AUTOSTART="true"
    fi
fi
echo "  [✓] Auto-start on login: ${PREF_AUTOSTART}"

# Save preferences to ~/.dictate_config.json
"${PYTHON_BIN}" -c "
import json, os
cfg_path = os.path.expanduser('~/.dictate_config.json')
cfg = {}
if os.path.exists(cfg_path):
    try:
        with open(cfg_path, 'r') as f:
            cfg = json.load(f)
    except Exception:
        pass
cfg['audio_mode'] = '${PREF_AUDIO_MODE}'
cfg['hotkey'] = '${PREF_HOTKEY}'
with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2)
"
echo "  [✓] Saved preferences to ${CONFIG_FILE}"

# 4. Virtual Environment Setup
VENV_PYTHON="${VENV_DIR}/bin/python"

if [ ! -x "${VENV_PYTHON}" ] || ! "${VENV_PYTHON}" --version >/dev/null 2>&1; then
    echo "  [+] Creating virtual environment at ${VENV_DIR} using ${PYTHON_BIN}..."
    rm -rf "${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# 5. Install Dependencies (using python -m pip to avoid shebang path issues)
echo "  [+] Installing dependencies from requirements.txt..."
"${VENV_PYTHON}" -m pip install --quiet --upgrade pip
"${VENV_PYTHON}" -m pip install --prefer-binary --quiet -r "${REQUIREMENTS_FILE}"

# 6. Symlink to ~/.local/bin/dictate (Atomic single-source of truth)
LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "${LOCAL_BIN}"

WRAPPER_FILE="${LOCAL_BIN}/dictate"
cat <<EOF > "${WRAPPER_FILE}"
#!/usr/bin/env bash
exec "${VENV_PYTHON}" "${DICTATE_PY}" "\$@"
EOF
chmod +x "${WRAPPER_FILE}"
echo "  [✓] Symlinked CLI launcher to ${WRAPPER_FILE}"

# 7. Configure / Reload macOS LaunchAgent
if [ "$PREF_AUTOSTART" = "true" ]; then
    "${SCRIPT_DIR}/setup_service.sh"
else
    echo "  [i] Skipping LaunchAgent registration (manual launch via 'dictate')."
fi

# 8. Accessibility Permission Verification (via AppleScript / deep link)
IS_TRUSTED=$("${VENV_PYTHON}" -c "
import ctypes
try:
    print(bool(ctypes.cdll.LoadLibrary('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices').AXIsProcessTrusted()))
except Exception:
    print(False)
" 2>/dev/null || echo "False")

if [ "$IS_TRUSTED" != "True" ]; then
    echo "  [!] Notice: macOS Accessibility permission is required for global hotkey detection."
    echo "  [+] Opening System Settings -> Privacy & Security -> Accessibility..."
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    osascript -e 'tell application "System Settings" to activate' 2>/dev/null || true
    echo "  [👉] In System Settings, toggle ON or add: ${PYTHON_BIN}"
else
    echo "  [✓] Verified macOS Accessibility permissions are active."
fi

# 9. Model Cache Pre-warm Confirmation
if [ "$SKIP_MODEL_DOWNLOAD" = false ]; then
    PROCEED_MODEL=true
    if [ -t 0 ]; then
        echo ""
        echo "Whisper Large-v3-Turbo model weights (~1.6 GB) will be cached locally in ~/.cache/huggingface/."
        read -r -p "Pre-download and warm up the model now? [Y/n]: " dl_choice
        case "$dl_choice" in
            [nN][oO]|[nN]) PROCEED_MODEL=false ;;
            *) PROCEED_MODEL=true ;;
        esac
    fi
    if [ "$PROCEED_MODEL" = true ]; then
        echo "  [+] Pre-warming Whisper model on Apple Silicon..."
        "${VENV_PYTHON}" -c "
import mlx_whisper, tempfile, os
import numpy as np
from scipy.io import wavfile
dummy = np.zeros(16000, dtype=np.float32)
with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
    wavfile.write(f.name, 16000, dummy)
    try:
        mlx_whisper.transcribe(f.name, path_or_hf_repo='mlx-community/whisper-large-v3-turbo')
        print('  [✓] Model ready in local GPU cache.')
    finally:
        if os.path.exists(f.name): os.remove(f.name)
" 2>/dev/null || echo "  [i] Note: Model will download in background upon first activation."
    else
        echo "  [i] Model pre-download skipped. It will download upon first dictation activation."
    fi
fi

echo "==> Installation complete! mac-local-dictation v${ACTUAL_VER} is ready."
