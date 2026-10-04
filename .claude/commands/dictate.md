---
description: Manage and operate local macOS voice dictation (install, start, status, logs)
---

# /dictate — Local Voice Dictation Manager

Manage the `mac-local-dictation` background service on this Mac.

## Usage

* `/dictate status`: Checks if the dictation process and LaunchAgent daemon are running, and prints the version.
* `/dictate install`: Executes `./skills/mac-local-dictation/scripts/install.sh` to configure dependencies and LaunchAgent.
* `/dictate restart`: Reloads the background service using `./skills/mac-local-dictation/scripts/setup_service.sh`.
* `/dictate logs`: Displays the last 30 lines of `skills/mac-local-dictation/dictate.log`.
* `/dictate uninstall`: Unloads the daemon, terminates running processes, and cleans up the environment.

## Execution Logic

Parse the argument `$1`:
- If `$1` is `install`: run `bash ./skills/mac-local-dictation/scripts/install.sh`
- If `$1` is `restart`: run `bash ./skills/mac-local-dictation/scripts/setup_service.sh`
- If `$1` is `uninstall`: run `bash ./skills/mac-local-dictation/scripts/uninstall.sh`
- If `$1` is `logs`: run `tail -n 30 ./skills/mac-local-dictation/dictate.log`
- Otherwise (or if `status`): run `dictate --version 2>/dev/null || true; ps aux | grep -i "[d]ictate.py"` and summarize the service state.
