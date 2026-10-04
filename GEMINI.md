# Antigravity Guidelines for mac-local-dictation

This repository contains **mac-local-dictation**, a 100% on-device, private voice dictation tool for macOS on Apple Silicon using `mlx-whisper` and Metal GPU acceleration.

---

## 1. Skill Discovery & Execution

* The primary operational runbook and procedure guide is located at:
  [`skills/mac-local-dictation/SKILL.md`](file:///Users/ablythe/repos/personal/mac-local-dictation/skills/mac-local-dictation/SKILL.md)
* Use the skill whenever the user asks to install, configure, manage, diagnose, or troubleshoot local voice dictation on their Mac.

---

## 2. Common Agent Runbooks

### Turnkey Installation
```bash
./skills/mac-local-dictation/scripts/install.sh
```
* Supports flags for automated, non-interactive execution:
  * `--audio-mode [stream|on_demand]` (default: `stream`)
  * `--hotkey [cmd_r|alt_r]` (default: `cmd_r`)
  * `--autostart [true|false]` (default: `true`)
  * `--skip-model-download`

### Service Management
```bash
# Check service status
ps aux | grep -i "[d]ictate.py"

# Verify CLI version
dictate --version

# Restart background service
./skills/mac-local-dictation/scripts/setup_service.sh

# Complete uninstallation & cleanup
./skills/mac-local-dictation/scripts/uninstall.sh
```

---

## 3. Engineering & Architecture Rules

1. **Hardware Boundary:** This project strictly targets macOS on Apple Silicon (`arm64`). It will not run on Linux, Windows, or Intel Macs.
2. **Audio Stack & Latency:**
   * Default: `stream` (in-memory persistent PortAudio stream with 0ms startup latency).
   * Privacy mode: `on_demand` (internal Python record worker that releases microphone hardware when idle).
3. **No External FFmpeg:** Audio capture and conversion are handled entirely in Python using `sounddevice` and the standard library `wave` module. Do not introduce external CLI dependencies for recording.
4. **Permissions:**
   * Global hotkey detection requires macOS Accessibility permission (`CGEventTap`).
   * Microphone recording requires macOS Microphone permission.
   * `install.sh` automatically checks `AXIsProcessTrusted` and deep-links to System Settings via AppleScript if untrusted.
