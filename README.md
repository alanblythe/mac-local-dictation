# Local Voice Dictation for macOS (Apple Silicon)

A lightweight, 100% on-device voice dictation tool for macOS running on Apple Silicon. Powered by Apple's [MLX framework](https://github.com/ml-explore/mlx) and OpenAI's Whisper model (`whisper-large-v3-turbo`), running locally on unified memory with zero remote network calls, subscriptions, or external cloud APIs.

---

## Features

- **100% Private & On-Device:** Audio never leaves your Mac. Whisper inference executes locally in Apple Silicon unified memory using Metal GPU acceleration.
- **Push-to-Talk Hotkey (Right Command ⌘):**
  - **Push-to-Talk:** Press and hold `Right Command` while speaking; release to transcribe and paste.
  - **Hands-Free Mode:** Tap `Right Command` quickly to start recording; tap again (or click the menu item) to finish.
  - **Configurable Hotkey:** Supports switching between `Right Command` (default, Superwhisper standard) and `Right Option` via the menu bar or `~/.dictate_config.json`.
- **Selectable Audio Capture Modes:**
  - **Instant Stream (Default):** Continuous in-memory audio capture with `0ms` startup latency.
  - **On-Demand (Mic Privacy):** Spawns an internal Python worker only while speaking; macOS orange microphone indicator shuts off completely when idle.
- **Auto-Paste & Clipboard:** Transcribed text is copied to your system clipboard and automatically pasted directly into whatever application is currently focused (`Cmd + V`).
- **Live Menu Bar Indicator:** Displays current operational states (`🎙️ v1.0.0` Ready, `🔴 v1.0.0` Recording, `⏳ v1.0.0` Transcribing).
- **Fast GPU Pre-Warming:** Warms the Whisper model into GPU memory at launch so subsequent dictation requests transcribe with low latency.

---

## Prerequisites

- **Hardware:** Mac with Apple Silicon (`arm64` — M1, M2, M3, M4, or later).
- **Operating System:** macOS 14.0 (Sonoma) or later recommended.
- **Python:** Python 3.11 or 3.12 (via Homebrew, python.org, or pyenv).

---

## Quickstart & Installation

### 1. Clone the Repository

```bash
git clone https://github.com/alanblythe/mac-local-dictation.git
cd mac-local-dictation
```

### 2. Turnkey Automated Installation

Run the provided installation script:

```bash
./skills/mac-local-dictation/scripts/install.sh
```

The installer will:
1. Detect Apple Silicon architecture and locate Python 3.11+.
2. Prompt for your preferred audio mode (`stream` vs `on_demand`) and hotkey (`cmd_r` vs `alt_r`).
3. Set up a dedicated virtual environment with pre-built binary wheels (`--prefer-binary`).
4. Symlink a persistent CLI launcher to `~/.local/bin/dictate`.
5. Register a background macOS LaunchAgent service to start automatically on login.
6. Verify macOS Accessibility trust and open the Settings pane via AppleScript if needed.

---

## Manual Setup (Without Installer)

If you prefer to configure manually:

```bash
# 1. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install --upgrade pip
pip install --prefer-binary -r requirements.txt

# 3. Launch application
python dictate.py
```

---

## Permissions Setup

On first launch, macOS requires two standard permissions:

1. **Microphone Access:** Prompted when the audio stream initializes. Click **Allow**.
2. **Accessibility Access:** Required for global hotkey detection (`Right Command` / `Right Option`) and automatic cursor pasting (`Cmd + V`).
   - Open **System Settings > Privacy & Security > Accessibility** and ensure Python (or your Terminal) is enabled.

---

## Configuration

Preferences are stored in `~/.dictate_config.json`:

```json
{
  "audio_mode": "stream",
  "hotkey": "cmd_r",
  "pause_media": true
}
```

* `audio_mode`: `"stream"` (0ms latency) or `"on_demand"` (mic privacy).
* `hotkey`: `"cmd_r"` (Right Command) or `"alt_r"` (Right Option).
* `pause_media`: pause browser and media-app playback while recording, resume after (default `true`). A browser paused within ~10s before dictating can be resumed.

---

## Service Management

```bash
# Check status
ps aux | grep -i "[d]ictate.py"

# Query version
dictate --version

# Restart background service
./skills/mac-local-dictation/scripts/setup_service.sh

# Complete uninstallation
./skills/mac-local-dictation/scripts/uninstall.sh
```

---

## License

Apache License 2.0. Copyright 2026 Alan Blythe.
