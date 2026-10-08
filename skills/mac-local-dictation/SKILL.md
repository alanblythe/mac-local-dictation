---
name: mac-local-dictation
description: >-
  Install, configure, manage, and diagnose the mac-local-dictation background service on Apple Silicon macOS.
  Use when the user asks to set up local voice dictation, check its service health or logs, switch audio recording
  modes (stream vs. on_demand), switch hotkeys (Right Command vs. Right Option), or troubleshoot macOS permissions.
---

# Local Voice Dictation for macOS (`mac-local-dictation`)

`mac-local-dictation` provides 100% on-device, private, GPU-accelerated voice dictation for macOS on Apple Silicon using `mlx-whisper` and OpenAI's `whisper-large-v3-turbo` model. It runs as a persistent menu bar app with global push-to-talk hotkey activation (default: **Right Command ⌘**, matching Superwhisper) and automatic cursor pasting.

---

## 1. Automated Installation & Setup

Execute the turnkey installation script to configure the environment, build dependencies, create the CLI launcher, and register the macOS LaunchAgent:

```bash
./skills/mac-local-dictation/scripts/install.sh
```

### Installation Options & CLI Flags:
When run interactively (or via agent flags), the installer prompts for four user preferences:
1. **Audio Capture Mode:** `Instant Stream` (0ms start, orange mic dot stays on) vs `On-Demand` (mic privacy, opens only while speaking).
   * Flag: `--audio-mode [stream|on_demand]` (default: `stream`)
2. **Activation Hotkey:** `Right Command (⌘)` (maximum compatibility) vs `Right Option (⌥)`.
   * Flag: `--hotkey [cmd_r|alt_r]` (default: `cmd_r`)
3. **Auto-Start on Login:** Whether to register the LaunchAgent service.
   * Flag: `--autostart [true|false]` (default: `true`)
4. **Model Pre-Warm:** Whether to pre-download the Whisper Large-v3-Turbo weights (~1.6 GB) to `~/.cache/huggingface/`.
   * Flag: `--skip-model-download`

### What the installer does:
1. **Architecture Assertion:** Confirms the host is Apple Silicon (`arm64`).
2. **Python Discovery:** Detects Python 3.11+ (Homebrew, pyenv, python.org, or system).
3. **Preferences Configuration:** Prompts for audio mode, hotkey, and autostart preferences, saving to `~/.dictate_config.json`.
4. **Virtual Environment:** Configures a dedicated virtual environment with pre-built binary wheels (`--prefer-binary`).
5. **CLI Launcher:** Symlinks a persistent wrapper at `~/.local/bin/dictate`.
6. **LaunchAgent Service:** Installs and loads `~/Library/LaunchAgents/com.<user>.local-dictation.plist` so the menu bar icon starts automatically on user login (if autostart enabled).
7. **Accessibility Setup:** Checks `AXIsProcessTrusted` and automatically triggers the macOS Accessibility UI via AppleScript if the user needs to grant permission.

---

## 2. Service Management & Verification

### Check Service Status
```bash
# Verify running process
ps aux | grep -i "[d]ictate.py"

# Verify CLI version
dictate --version
```

### Inspect Diagnostic Logs
Live operational and transcription logs are written to `dictate.log`:
```bash
tail -n 30 ./skills/mac-local-dictation/dictate.log
```

### Restart Service
```bash
./skills/mac-local-dictation/scripts/setup_service.sh
```

### Uninstall Cleanly
```bash
./skills/mac-local-dictation/scripts/uninstall.sh
```

---

## 3. Hotkey & Audio Modes Configuration

Preferences can be switched dynamically in the menu bar dropdown or saved in `~/.dictate_config.json`:

```json
{
  "audio_mode": "stream",
  "hotkey": "cmd_r",
  "pause_media": true,
  "vocabulary": ["Siobhan"],
  "replacements": {"Shivon": "Siobhan"},
  "input_devices": ["My USB Mic", "MacBook Pro Microphone"]
}
```

### Supported Hotkeys
* **`cmd_r` (Right Command ⌘ — Default):** Superwhisper standard. Supported on virtually all external and Mac keyboards, comfortable right-thumb push-to-talk, and zero interference with typing accented characters.
* **`alt_r` (Right Option ⌥):** Alternative hotkey.

### Supported Audio Modes
* **`stream` (Instant Stream — Default):** Keeps an in-memory PortAudio stream open. 0ms startup latency. Orange microphone dot remains illuminated while running.
* **`on_demand` (Mic Privacy):** Spawns an internal Python recording worker only while speaking. Microphone hardware is released completely when idle.

---

## 4. Troubleshooting & Permissions

* **Accessibility Permission:**
  Global hotkey detection (Mach `CGEventTap` via `pynput`) requires macOS Accessibility access. If keystrokes are ignored, ensure Python (or your terminal app) has Accessibility enabled in:
  `System Settings -> Privacy & Security -> Accessibility`
* **Microphone Permission:**
  Microphone input requires permission in:
  `System Settings -> Privacy & Security -> Microphone`
