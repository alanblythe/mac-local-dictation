# Claude Code Guide for mac-local-dictation

On-device voice dictation for macOS Apple Silicon using `mlx-whisper` and OpenAI's `whisper-large-v3-turbo`. 100% private, local unified memory execution with global push-to-talk hotkey (`Right Command ⌘`).

---

## Quick Reference Commands

### Installation & Lifecycle
* **Install:** `./skills/mac-local-dictation/scripts/install.sh`
* **Check Status:** `ps aux | grep -i "[d]ictate.py"`
* **Check Version:** `dictate --version`
* **Restart Daemon:** `./skills/mac-local-dictation/scripts/setup_service.sh`
* **Uninstall Cleanly:** `./skills/mac-local-dictation/scripts/uninstall.sh`
* **Inspect Logs:** `tail -n 30 skills/mac-local-dictation/dictate.log`

### Custom Slash Command
A native Claude Code slash command is available at [`.claude/commands/dictate.md`](.claude/commands/dictate.md). You can run `/dictate` in terminal chat for service operations.

---

## Architectural Conventions

1. **Hardware Requirement:** Apple Silicon (`arm64`) only. Metal GPU acceleration is used for Whisper inference.
2. **Audio Capture:**
   * `stream` mode (default): In-memory continuous stream via `sounddevice` (0ms latency, orange mic dot stays on).
   * `on_demand` mode: Spawns internal Python child worker (`--record-worker`) to release microphone when idle.
3. **No External FFmpeg Dependency:** Audio recording and WAV export are handled directly in Python using `sounddevice` and `wave`.
4. **Permissions Management:**
   * Accessibility (`CGEventTap` hotkey detection) requires enabling Python or Terminal in `System Settings -> Privacy & Security -> Accessibility`.
   * `scripts/install.sh` verifies `AXIsProcessTrusted` and automatically deep-links to System Settings via AppleScript if untrusted.
