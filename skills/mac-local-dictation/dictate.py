#!/usr/bin/env python3
"""
Local Voice Dictation for Apple Silicon macOS using mlx-whisper.
100% on-device, zero remote network calls.
"""

__version__ = "1.1.0"

import os
import sys

# Fast-path for version query
if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-v"):
  print(__version__)
  sys.exit(0)

SAMPLE_RATE = 16000
TITLE_IDLE = f"🎙️ v{__version__}"
TITLE_BUSY = f"⏳ v{__version__}"


def recording_title(elapsed):
  minutes, seconds = divmod(int(elapsed), 60)
  return f"🔴 {minutes}:{seconds:02d}"

# Fast-path for lightweight audio recording worker (bypasses heavy MLX and Cocoa imports)
# This enables sub-80ms startup and eliminates external FFmpeg / Santa binary requirements
if len(sys.argv) > 2 and sys.argv[1] == "--record-worker":
  import signal
  import threading
  import wave
  import sounddevice as sd

  out_path = sys.argv[2]
  device_name = sys.argv[3] if len(sys.argv) > 3 else None
  frames = []
  stop_event = threading.Event()

  def _audio_cb(indata, frame_count, time_info, status):
    if not stop_event.is_set():
      frames.append(indata.copy())

  def _sig_handler(*_):
    stop_event.set()

  signal.signal(signal.SIGINT, _sig_handler)
  signal.signal(signal.SIGTERM, _sig_handler)

  def _stdin_waiter():
    try:
      sys.stdin.read(1)
    except Exception:
      pass
    stop_event.set()

  threading.Thread(target=_stdin_waiter, daemon=True).start()

  try:
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        callback=_audio_cb,
        device=device_name,
    ):
      while not stop_event.is_set():
        stop_event.wait(0.05)
  except Exception as e:
    sys.stderr.write(f"Record worker error: {e}\n")
    sys.exit(1)

  if frames:
    audio_bytes = b"".join(f.tobytes() for f in frames)
    with wave.open(out_path, "wb") as wf:
      wf.setnchannels(1)
      wf.setsampwidth(2)
      wf.setframerate(SAMPLE_RATE)
      wf.writeframes(audio_bytes)
  sys.exit(0)

import ctypes
import json
import queue
import re
import struct
import subprocess
import tempfile
import threading
import time
import mlx.core as mx
import mlx_whisper
import numpy as np
import objc
from pynput import keyboard
import rumps
from AppKit import (
    NSBackingStoreBuffered, NSColor, NSEvent, NSFont, NSLineBreakByWordWrapping, NSMakeRect, NSPanel,
    NSPasteboard, NSPasteboardItem, NSPasteboardTypeString, NSScreen, NSStatusWindowLevel, NSTextField, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from PyObjCTools import AppHelper
from scipy.io import wavfile
import sounddevice as sd

# Whisper model to run locally on Apple Silicon GPU
MODEL_NAME = "mlx-community/whisper-large-v3-turbo"
CONFIG_FILE = os.path.expanduser("~/.dictate_config.json")

# Audio Modes:
# "stream": In-memory continuous stream (0ms latency, zero deadlocks, mic dot stays on)
# "on_demand": Process spawned on-demand (mic privacy, mic dot turns off, slight startup delay)
MODE_STREAM = "stream"
MODE_ON_DEMAND = "on_demand"

# Hotkey Modes:
# "cmd_r": Right Command (default, Superwhisper standard, maximum keyboard compatibility)
# "alt_r": Right Option (alternative push-to-talk key)
HOTKEY_CMD_R = "cmd_r"
HOTKEY_ALT_R = "alt_r"

MAX_RECORDING_SECONDS = 300
# The target app reads the clipboard asynchronously after Cmd+V; restoring sooner can paste the old contents
CLIPBOARD_RESTORE_SECONDS = 0.5
# nspasteboard.org convention: clipboard managers skip items carrying this type
TRANSIENT_PASTEBOARD_TYPE = "org.nspasteboard.TransientType"
# A full-length recording takes roughly 20s to transcribe
TRANSCRIPTION_HUNG_SECONDS = 90

# Live preview: re-transcribe the tail of the recording while the hotkey is held
PREVIEW_INTERVAL_SECONDS = 1.0
PREVIEW_WINDOW_SECONDS = 30
PREVIEW_MIN_SECONDS = 0.6
PREVIEW_MAX_CHARS = 260

# Whisper invents these from near-silence; only trusted on clips longer than HALLUCINATION_MAX_SECONDS
MIN_SPEECH_SECONDS = 0.3
HALLUCINATION_MAX_SECONDS = 2.0
SILENCE_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thank you very much", "thanks for watching",
    "thank you for watching", "bye", "gracias", "okay", "so",
}
# Another key pressed within this window of the hotkey means a shortcut, not dictation
CHORD_CANCEL_SECONDS = 1.0

STREAM_CHECK_SECONDS = 5
STREAM_STALL_SECONDS = 3.0
STREAM_HEALTHY_SECONDS = 10
STREAM_RETRY_MAX_SECONDS = 60
STREAM_CLOSE_TIMEOUT_SECONDS = 2.0


def clear_mlx_cache():
  if hasattr(mx, "clear_cache"):
    mx.clear_cache()
  elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
    mx.metal.clear_cache()


def log(category: str, message: str):
  ts = time.strftime("%Y-%m-%d %H:%M:%S")
  print(f"[{ts}] [{category}] {message}", flush=True)


# ---------------------------------------------------------------------------
# CoreAudio Queries
# ---------------------------------------------------------------------------
_COREAUDIO = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
AUDIO_SYSTEM_OBJECT = 1


class _AudioObjectAddress(ctypes.Structure):
  _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


def _fourcc(code):
  return struct.unpack(">I", code.encode())[0]


def coreaudio_property(obj_id, selector, ctype, scope="glob"):
  addr = _AudioObjectAddress(_fourcc(selector), _fourcc(scope), 0)
  size = ctypes.c_uint32(0)
  if _COREAUDIO.AudioObjectGetPropertyDataSize(obj_id, ctypes.byref(addr), 0, None, ctypes.byref(size)):
    return []
  buf = (ctype * (size.value // ctypes.sizeof(ctype)))()
  if _COREAUDIO.AudioObjectGetPropertyData(obj_id, ctypes.byref(addr), 0, None, ctypes.byref(size), buf):
    return []
  return list(buf)


def coreaudio_string(obj_id, selector):
  ref = coreaudio_property(obj_id, selector, ctypes.c_void_p)
  return str(objc.objc_object(c_void_p=ref[0])) if ref and ref[0] else ""


def input_devices():
  """Returns {name: device_id} for connected devices that have input streams."""
  devices = {}
  for device_id in coreaudio_property(AUDIO_SYSTEM_OBJECT, "dev#", ctypes.c_uint32):
    if coreaudio_property(device_id, "stm#", ctypes.c_uint32, scope="inpt"):
      devices[coreaudio_string(device_id, "lnam")] = device_id
  return devices


def choose_input_device(preferred_names):
  """First connected device from preferred_names (case-insensitive), else the macOS default."""
  if preferred_names:
    available = {name.lower(): (device_id, name) for name, device_id in input_devices().items()}
    for wanted in preferred_names:
      if wanted.lower() in available:
        return available[wanted.lower()]
  return default_input_device()


def default_input_device():
  """Returns (device_id, name) of the macOS default input, read live."""
  ids = coreaudio_property(AUDIO_SYSTEM_OBJECT, "dIn ", ctypes.c_uint32)
  if not ids:
    return None, ""
  return ids[0], coreaudio_string(ids[0], "lnam")


# ---------------------------------------------------------------------------
# Media Pause While Dictating
# ---------------------------------------------------------------------------
# macOS 15.4+ blocks reading Now Playing state, so "playing" means a media
# app has audio output open. Browsers hold output open ~10s after a pause.
MEDIA_BUNDLE_PREFIXES = (
    "com.google.Chrome",
    "com.apple.Safari",
    "com.apple.WebKit.GPU",
    "org.mozilla.firefox",
    "company.thebrowser.Browser",
    "com.microsoft.edgemac",
    "com.brave.Browser",
    "com.spotify.client",
    "com.apple.Music",
    "com.apple.podcasts",
    "com.apple.TV",
    "org.videolan.vlc",
    "com.colliderli.iina",
)
MR_COMMAND_PLAY = 0
MR_COMMAND_PAUSE = 1


class MediaController:

  def __init__(self):
    self._mediaremote = ctypes.cdll.LoadLibrary("/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote")
    self._mediaremote.MRMediaRemoteSendCommand.argtypes = [ctypes.c_int, ctypes.c_void_p]
    self._mediaremote.MRMediaRemoteSendCommand.restype = ctypes.c_bool
    self.paused_by_us = False

  def media_playing_apps(self):
    playing = []
    for process_id in coreaudio_property(AUDIO_SYSTEM_OBJECT, "prs#", ctypes.c_uint32):
      if coreaudio_property(process_id, "piro", ctypes.c_uint32)[:1] == [1]:
        bundle_id = coreaudio_string(process_id, "pbid")
        if bundle_id.startswith(MEDIA_BUNDLE_PREFIXES):
          playing.append(bundle_id)
    return playing

  def pause(self):
    playing = self.media_playing_apps()
    if playing:
      self._mediaremote.MRMediaRemoteSendCommand(MR_COMMAND_PAUSE, None)
      self.paused_by_us = True
      log("MEDIA", f"Paused media ({', '.join(sorted(set(playing)))}).")

  def resume(self):
    if self.paused_by_us:
      self.paused_by_us = False
      self._mediaremote.MRMediaRemoteSendCommand(MR_COMMAND_PLAY, None)
      log("MEDIA", "Resumed media.")


def snapshot_clipboard(pasteboard):
  """Copies every item on the pasteboard, in all of its types, so it can be written back."""
  items = []
  for item in pasteboard.pasteboardItems() or []:
    copy = NSPasteboardItem.alloc().init()
    for kind in item.types():
      data = item.dataForType_(kind)
      if data is not None:
        copy.setData_forType_(data, kind)
    items.append(copy)
  return items


class PreviewOverlay:
  """Floating, click-through panel showing the live transcript. Never takes focus, so paste lands in the user's app."""

  WIDTH, HEIGHT, MARGIN = 680, 112, 120

  def __init__(self):
    self.panel = None
    self.label = None

  def show(self, text):
    AppHelper.callAfter(self._show, text)

  def hide(self):
    AppHelper.callAfter(self._hide)

  def _build(self):
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, self.WIDTH, self.HEIGHT),
        NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered,
        False,
    )
    panel.setLevel_(NSStatusWindowLevel)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setIgnoresMouseEvents_(True)
    panel.setHasShadow_(True)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    content = panel.contentView()
    content.setWantsLayer_(True)
    content.layer().setCornerRadius_(14)
    content.layer().setBackgroundColor_(NSColor.colorWithWhite_alpha_(0.08, 0.9).CGColor())

    label = NSTextField.alloc().initWithFrame_(NSMakeRect(18, 12, self.WIDTH - 36, self.HEIGHT - 24))
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setBordered_(False)
    label.setDrawsBackground_(False)
    label.setTextColor_(NSColor.whiteColor())
    label.setFont_(NSFont.systemFontOfSize_(15))
    label.setMaximumNumberOfLines_(4)
    label.cell().setWraps_(True)
    label.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
    content.addSubview_(label)
    self.panel, self.label = panel, label

  def _show(self, text):
    if self.panel is None:
      self._build()
    self.label.setStringValue_(text)
    mouse = NSEvent.mouseLocation()
    screen = next((s for s in NSScreen.screens() if self._contains(s.frame(), mouse)), NSScreen.mainScreen())
    area = screen.visibleFrame()
    x = area.origin.x + (area.size.width - self.WIDTH) / 2
    y = area.origin.y + self.MARGIN
    self.panel.setFrameOrigin_((x, y))
    self.panel.orderFrontRegardless()

  def _hide(self):
    if self.panel is not None:
      self.panel.orderOut_(None)

  @staticmethod
  def _contains(frame, point):
    return (frame.origin.x <= point.x <= frame.origin.x + frame.size.width
            and frame.origin.y <= point.y <= frame.origin.y + frame.size.height)


def preview_tail(text):
  """Last PREVIEW_MAX_CHARS of text, cut at a word boundary."""
  if len(text) <= PREVIEW_MAX_CHARS:
    return text
  tail = text[-PREVIEW_MAX_CHARS:]
  return "…" + tail[tail.find(" ") + 1:]


class LocalWhisperApp(rumps.App):

  def __init__(self):
    super().__init__("Dictate", title=TITLE_IDLE)
    self.is_recording = False
    self.lock = threading.Lock()

    # Load preferences
    self.config = self._load_config()
    self.audio_mode = self.config.get("audio_mode", MODE_STREAM)
    if self.audio_mode not in (MODE_STREAM, MODE_ON_DEMAND):
      self.audio_mode = MODE_STREAM

    self.hotkey_mode = self.config.get("hotkey", HOTKEY_CMD_R)
    if self.hotkey_mode not in (HOTKEY_CMD_R, HOTKEY_ALT_R):
      self.hotkey_mode = HOTKEY_CMD_R
    self._apply_hotkey_config()

    self.pause_media = bool(self.config.get("pause_media", True))
    self.show_preview = bool(self.config.get("show_preview", False))
    self.overlay = PreviewOverlay()
    # Serialises GPU use between the live preview and the final transcription
    self.whisper_lock = threading.Lock()
    self.media = MediaController()
    self.media_queue = queue.Queue()

    # In-memory stream state (MODE_STREAM)
    self.sd_stream = None
    self.stream_lock = threading.RLock()
    self.stream_device_id = None
    self.stream_opened_at = 0.0
    self.stream_failures = 0
    self.next_stream_retry = 0.0
    self.stream_frames = []

    # On-demand process state (MODE_ON_DEMAND)
    self.record_proc = None
    self.current_wav_path = None

    # Queues for decoupled non-blocking event handling
    self.event_queue = queue.Queue()
    self.transcribe_queue = queue.Queue()

    self.key_press_time = None
    self.trigger_down = False
    self.press_started_recording = False
    self.pasting = False
    self.recording_start_time = None
    self.transcription_start_time = None
    self.last_audio_tick = 0.0
    self.keyboard_controller = keyboard.Controller()

    # Build Menu Bar Items
    self.menu_version = rumps.MenuItem(f"Local Dictation v{__version__}")
    self.menu_version.set_callback(None)
    self.menu_toggle = rumps.MenuItem(f"⏺ Toggle Recording ({self.hotkey_display_name})", callback=self.toggle_recording)
    self.menu_mode_stream = rumps.MenuItem("Mode: ⚡ Instant Stream (0ms Latency)", callback=self._set_mode_stream)
    self.menu_mode_ondemand = rumps.MenuItem("Mode: 🔒 On-Demand (Mic Privacy)", callback=self._set_mode_ondemand)
    self.menu_hotkey_cmdr = rumps.MenuItem("Hotkey: ⌘ Right Command (Superwhisper Standard)", callback=self._set_hotkey_cmdr)
    self.menu_hotkey_altr = rumps.MenuItem("Hotkey: ⌥ Right Option", callback=self._set_hotkey_altr)
    self.menu_pause_media = rumps.MenuItem("⏸ Pause Media While Dictating", callback=self._toggle_pause_media)
    self.menu_show_preview = rumps.MenuItem("👁 Show Live Transcript While Holding", callback=self._toggle_show_preview)
    self.menu_clear_cache = rumps.MenuItem("🧹 Clear Metal GPU Cache", callback=self.clear_gpu_cache)

    self.menu = [
        self.menu_version,
        rumps.separator,
        self.menu_toggle,
        rumps.separator,
        self.menu_mode_stream,
        self.menu_mode_ondemand,
        rumps.separator,
        self.menu_hotkey_cmdr,
        self.menu_hotkey_altr,
        rumps.separator,
        self.menu_pause_media,
        self.menu_show_preview,
        rumps.separator,
        self.menu_clear_cache,
    ]
    self._update_menu_state()

    # Initialize audio engine based on selected mode
    if self.audio_mode == MODE_STREAM:
      self._start_persistent_stream()

    # Start event processing, transcription worker, and watchdog loops
    threading.Thread(target=self._event_dispatcher_loop, daemon=True).start()
    threading.Thread(target=self._transcription_worker_loop, daemon=True).start()
    threading.Thread(target=self._watchdog_loop, daemon=True).start()
    threading.Thread(target=self._media_worker_loop, daemon=True).start()
    threading.Thread(target=self._preview_worker_loop, daemon=True).start()

    # Pre-warm Whisper model in background
    threading.Thread(target=self._preload_model, daemon=True).start()

    # Check macOS Accessibility trust
    try:
      import ctypes
      is_trusted = bool(ctypes.cdll.LoadLibrary('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices').AXIsProcessTrusted())
      if not is_trusted:
        log("AUTH", "WARNING: macOS Accessibility not granted. Hotkey input monitoring will be blocked.")
        log("AUTH", "To fix: System Settings -> Privacy & Security -> Accessibility, and enable Python.")
    except Exception:
      pass

    # Start global hotkey listener
    # Event tap callbacks ONLY push to queue (<0.05ms) to guarantee no OS freezes
    self.listener = keyboard.Listener(
        on_press=self._on_key_press,
        on_release=self._on_key_release,
    )
    self.listener.daemon = True
    self.listener.start()

    log("INIT", f"[Ready] Audio mode: {self.audio_mode.upper()} | Hotkey: {self.hotkey_display_name}. Tap or hold anywhere.")

  # ---------------------------------------------------------------------------
  # Configuration Management
  # ---------------------------------------------------------------------------
  def _apply_hotkey_config(self):
    if self.hotkey_mode == HOTKEY_ALT_R:
      self.trigger_key = keyboard.Key.alt_r
      self.hotkey_display_name = "Right Option"
    else:
      self.trigger_key = keyboard.Key.cmd_r
      self.hotkey_display_name = "Right Command"

  def _load_config(self):
    try:
      if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
          return json.load(f)
    except Exception as e:
      log("CONFIG", f"Error loading config: {e}")
    return {}

  def _save_config(self):
    try:
      cfg = self._load_config()
      cfg["audio_mode"] = self.audio_mode
      cfg["hotkey"] = self.hotkey_mode
      cfg["pause_media"] = self.pause_media
      cfg["show_preview"] = self.show_preview
      with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
      log("CONFIG", f"Saved preferences: mode={self.audio_mode}, hotkey={self.hotkey_mode}, pause_media={self.pause_media}")
    except Exception as e:
      log("CONFIG", f"Error saving config: {e}")

  def _update_menu_state(self):
    self.menu_mode_stream.state = 1 if self.audio_mode == MODE_STREAM else 0
    self.menu_mode_ondemand.state = 1 if self.audio_mode == MODE_ON_DEMAND else 0
    self.menu_hotkey_cmdr.state = 1 if self.hotkey_mode == HOTKEY_CMD_R else 0
    self.menu_hotkey_altr.state = 1 if self.hotkey_mode == HOTKEY_ALT_R else 0
    self.menu_pause_media.state = 1 if self.pause_media else 0
    self.menu_show_preview.state = 1 if self.show_preview else 0
    self.menu_toggle.title = f"⏺ Toggle Recording ({self.hotkey_display_name})"

  def _set_mode_stream(self, _=None):
    with self.lock:
      if self.audio_mode == MODE_STREAM:
        return
      log("MODE", "Switching to Instant In-Memory Stream mode (0ms latency)...")
      self.audio_mode = MODE_STREAM
      self._save_config()
      self._update_menu_state()
      self._start_persistent_stream()
      rumps.notification("Dictate", "Audio Mode Changed", "⚡ Instant Stream: Zero latency, optimal quality.")

  def _set_mode_ondemand(self, _=None):
    with self.lock:
      if self.audio_mode == MODE_ON_DEMAND:
        return
      log("MODE", "Switching to On-Demand Process mode (Microphone Privacy)...")
      self.audio_mode = MODE_ON_DEMAND
      self._save_config()
      self._update_menu_state()
      self._stop_persistent_stream()
      rumps.notification("Dictate", "Audio Mode Changed", "🔒 On-Demand: Mic active only while recording.")

  def _set_hotkey_cmdr(self, _=None):
    with self.lock:
      if self.hotkey_mode == HOTKEY_CMD_R:
        return
      self.hotkey_mode = HOTKEY_CMD_R
      self._apply_hotkey_config()
      self._save_config()
      self._update_menu_state()
      log("HOTKEY", "Switched hotkey to Right Command (cmd_r).")
      rumps.notification("Dictate", "Hotkey Changed", "⌘ Right Command is now your dictation key.")

  def _set_hotkey_altr(self, _=None):
    with self.lock:
      if self.hotkey_mode == HOTKEY_ALT_R:
        return
      self.hotkey_mode = HOTKEY_ALT_R
      self._apply_hotkey_config()
      self._save_config()
      self._update_menu_state()
      log("HOTKEY", "Switched hotkey to Right Option (alt_r).")
      rumps.notification("Dictate", "Hotkey Changed", "⌥ Right Option is now your dictation key.")

  def _toggle_pause_media(self, _=None):
    with self.lock:
      self.pause_media = not self.pause_media
      self._save_config()
      self._update_menu_state()
      log("MEDIA", f"Pause media while dictating: {'on' if self.pause_media else 'off'}.")

  def _toggle_show_preview(self, _=None):
    with self.lock:
      self.show_preview = not self.show_preview
      self._save_config()
      self._update_menu_state()
      log("PREVIEW", f"Live transcript while holding: {'on' if self.show_preview else 'off'}.")

  # ---------------------------------------------------------------------------
  # Live Preview Worker (re-transcribes the recording's tail while held)
  # ---------------------------------------------------------------------------
  def _preview_worker_loop(self):
    while True:
      time.sleep(PREVIEW_INTERVAL_SECONDS)
      # On-demand audio lives in a child process, so only stream mode can preview
      if not (self.is_recording and self.show_preview and self.audio_mode == MODE_STREAM):
        continue
      frames = list(self.stream_frames)
      if not frames:
        continue
      audio = np.concatenate(frames, axis=0).flatten()
      if len(audio) < PREVIEW_MIN_SECONDS * SAMPLE_RATE:
        continue
      clipped = len(audio) > PREVIEW_WINDOW_SECONDS * SAMPLE_RATE
      audio = audio[-PREVIEW_WINDOW_SECONDS * SAMPLE_RATE:]
      vocabulary = self._load_config().get("vocabulary") or []
      # Skip rather than queue behind a final transcription
      if not self.whisper_lock.acquire(blocking=False):
        continue
      try:
        res = mlx_whisper.transcribe(
            audio.astype(np.float32),
            path_or_hf_repo=MODEL_NAME,
            verbose=None,
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            initial_prompt=", ".join(vocabulary) + "." if vocabulary else None,
        )
        text = self._clean_repetitions(res.get("text", "").strip())
      except Exception as e:
        log("PREVIEW", f"Preview transcription failed: {e}")
        continue
      finally:
        self.whisper_lock.release()
      if self.is_recording and text and not self._is_silence_hallucination(text, vocabulary):
        self.overlay.show(preview_tail(("…" if clipped else "") + text))

  # ---------------------------------------------------------------------------
  # Media Worker Loop (serializes pause/resume off the hotkey path)
  # ---------------------------------------------------------------------------
  def _media_worker_loop(self):
    while True:
      action = self.media_queue.get()
      try:
        if action == "PAUSE":
          self.media.pause()
        elif action == "RESUME":
          self.media.resume()
      except Exception as e:
        log("ERROR", f"Media {action.lower()} failed: {e}")

  # ---------------------------------------------------------------------------
  # Persistent In-Memory Stream Backend (MODE_STREAM)
  # ---------------------------------------------------------------------------
  def _sd_audio_callback(self, indata, frames, time_info, status):
    self.last_audio_tick = time.time()
    if status:
      log("AUDIO_STATUS", f"Stream status: {status}")
    if self.is_recording and self.audio_mode == MODE_STREAM:
      self.stream_frames.append(indata.copy())

  def _start_persistent_stream(self):
    with self.stream_lock:
      if self.sd_stream is not None:
        return
      device_id, device_name = self._target_input_device()
      try:
        # PortAudio caches the device list at init; rescan so newly connected devices are visible
        sd._terminate()
        sd._initialize()
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._sd_audio_callback,
            blocksize=1024,
            device=device_name or None,
        )
        stream.start()
        self.sd_stream = stream
        self.stream_device_id = device_id
        self.stream_opened_at = time.time()
        self.last_audio_tick = time.time()
        log("AUDIO", f"Audio stream open on '{device_name}' (0ms latency ready).")
      except Exception as e:
        self._schedule_stream_retry(f"Failed to open audio stream on '{device_name}': {e}")

  def _target_input_device(self):
    # Read per check so input_devices edits apply without a restart
    return choose_input_device(self._load_config().get("input_devices") or [])

  def _schedule_stream_retry(self, reason):
    self.stream_failures += 1
    delay = min(STREAM_RETRY_MAX_SECONDS, 2 ** self.stream_failures)
    self.next_stream_retry = time.time() + delay
    log("WARN", f"{reason} Retrying in {delay}s.")

  def _close_stream(self, stream_obj):
    """Closes synchronously so PortAudio can be reinitialized; False if the close hangs."""
    closer = threading.Thread(target=self._safe_close_stream, args=(stream_obj,), daemon=True)
    closer.start()
    closer.join(STREAM_CLOSE_TIMEOUT_SECONDS)
    return not closer.is_alive()

  def _recreate_stream(self, reason):
    with self.stream_lock:
      log("AUDIO", f"Rebuilding audio stream: {reason}.")
      old_stream = self.sd_stream
      self.sd_stream = None
      if old_stream is not None and not self._close_stream(old_stream):
        self._schedule_stream_retry("Old audio stream did not close.")
        return
      self._start_persistent_stream()

  def _safe_close_stream(self, stream_obj):
    try:
      stream_obj.stop()
      stream_obj.close()
      log("AUDIO", "Audio stream closed cleanly.")
    except Exception as e:
      log("WARN", f"Exception closing audio stream: {e}")

  def _stop_persistent_stream(self):
    with self.stream_lock:
      if self.sd_stream is not None:
        stream = self.sd_stream
        self.sd_stream = None
        self._close_stream(stream)

  def _check_stream_health(self):
    if self.is_recording or time.time() < self.next_stream_retry:
      return
    with self.stream_lock:
      if self.sd_stream is None:
        self._start_persistent_stream()
        return
      device_id, device_name = self._target_input_device()
      stalled = not self.sd_stream.active or (time.time() - self.last_audio_tick > STREAM_STALL_SECONDS)
      if device_id != self.stream_device_id:
        self.stream_failures = 0
        self._recreate_stream(f"input device changed to '{device_name}'")
      elif stalled:
        if time.time() - self.stream_opened_at < STREAM_HEALTHY_SECONDS:
          self._stop_persistent_stream()
          self._schedule_stream_retry(f"Audio stream on '{device_name}' stalled right after opening.")
        else:
          self._recreate_stream("stream stalled")
      elif time.time() - self.stream_opened_at >= STREAM_HEALTHY_SECONDS:
        self.stream_failures = 0

  # ---------------------------------------------------------------------------
  # Hotkey Event Tap Callbacks (Ultra-lightweight: strictly non-blocking)
  # ---------------------------------------------------------------------------
  def _on_key_press(self, key):
    if key == self.trigger_key:
      self.trigger_down = True
      self.event_queue.put(("KEY_PRESS", time.time()))
    elif self.trigger_down and not self.pasting:
      self.event_queue.put(("CHORD", time.time()))

  def _on_key_release(self, key):
    if key == self.trigger_key:
      self.trigger_down = False
      self.event_queue.put(("KEY_RELEASE", time.time()))

  # ---------------------------------------------------------------------------
  # Event Dispatcher Loop (Handles hotkey state machine safely)
  # ---------------------------------------------------------------------------
  def _event_dispatcher_loop(self):
    while True:
      try:
        event_type, ts = self.event_queue.get()
        if event_type == "KEY_PRESS":
          if self.key_press_time is None:
            self.key_press_time = ts
            self.press_started_recording = not self.is_recording
            if not self.is_recording:
              log("HOTKEY", f"{self.hotkey_display_name} pressed -> starting recording...")
              self._start_recording()
            else:
              log("HOTKEY", f"{self.hotkey_display_name} tapped again -> stopping recording...")
              self._stop_and_enqueue_transcription()

        elif event_type == "KEY_RELEASE":
          if self.key_press_time is not None:
            duration = ts - self.key_press_time
            self.key_press_time = None
            self.press_started_recording = False
            # If held for >= 0.25s (push-to-talk mode), stop on release
            if duration >= 0.25 and self.is_recording:
              log("HOTKEY", f"{self.hotkey_display_name} released after {duration:.2f}s -> stopping push-to-talk...")
              self._stop_and_enqueue_transcription()
            elif self.is_recording:
              log("HOTKEY", f"Quick tap ({duration:.2f}s) -> hands-free recording active.")

        elif event_type == "CHORD":
          if self.press_started_recording and ts - self.key_press_time <= CHORD_CANCEL_SECONDS:
            self.press_started_recording = False
            log("HOTKEY", f"{self.hotkey_display_name} used in a shortcut -> recording discarded.")
            self._cancel_recording()

        elif event_type == "TOGGLE_MENU":
          if not self.is_recording:
            log("MENU", "Menu toggle -> starting recording...")
            self._start_recording()
          else:
            log("MENU", "Menu toggle -> stopping recording...")
            self._stop_and_enqueue_transcription()

      except Exception as e:
        log("ERROR", f"Error in event dispatcher: {e}")

  # ---------------------------------------------------------------------------
  # Recording Start / Stop Controls
  # ---------------------------------------------------------------------------
  def _start_recording(self):
    with self.lock:
      if self.is_recording:
        return
      self.is_recording = True
      self.recording_start_time = time.time()
      self.title = recording_title(0)
      if self.show_preview:
        self.overlay.show("Listening…")
      if self.pause_media:
        self.media_queue.put("PAUSE")

      if self.audio_mode == MODE_STREAM:
        device_id, device_name = self._target_input_device()
        if self.sd_stream is None or not self.sd_stream.active or (time.time() - self.last_audio_tick > 1.5):
          self._recreate_stream("stream dead or stalled at recording start")
        elif device_id != self.stream_device_id:
          self._recreate_stream(f"input device changed to '{device_name}'")
        self.stream_frames = []
        log("AUDIO", "Instant stream recording active (0ms start).")

      elif self.audio_mode == MODE_ON_DEMAND:
        temp_f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        self.current_wav_path = temp_f.name
        temp_f.close()

        cmd = [
            sys.executable,
            os.path.abspath(__file__),
            "--record-worker",
            self.current_wav_path,
        ]
        _, device_name = self._target_input_device()
        if device_name:
          cmd.append(device_name)
        try:
          self.record_proc = subprocess.Popen(
              cmd,
              stdin=subprocess.PIPE,
              stdout=subprocess.DEVNULL,
              stderr=subprocess.PIPE,
          )
          log("AUDIO", f"On-demand Python recording worker started (PID: {self.record_proc.pid}).")
        except Exception as e:
          log("ERROR", f"Failed to start on-demand recording worker: {e}")
          self.is_recording = False
          self.record_proc = None
          self.title = TITLE_IDLE
          self.overlay.hide()
          self.media_queue.put("RESUME")
          if self.current_wav_path and os.path.exists(self.current_wav_path):
            os.remove(self.current_wav_path)

  def _cancel_recording(self):
    with self.lock:
      if not self.is_recording:
        return
      self.is_recording = False
      self.recording_start_time = None
      self.title = TITLE_IDLE
      self.overlay.hide()
      self.stream_frames = []
      self.media_queue.put("RESUME")
      proc, wav_path = self.record_proc, self.current_wav_path
      self.record_proc = None
      self.current_wav_path = None
    if proc is not None:
      proc.kill()
      proc.wait()
    if wav_path and os.path.exists(wav_path):
      os.remove(wav_path)

  def _stop_and_enqueue_transcription(self):
    with self.lock:
      if not self.is_recording:
        return
      self.is_recording = False
      self.title = TITLE_BUSY
      if self.show_preview:
        self.overlay.show("Transcribing…")
      self.media_queue.put("RESUME")
      rec_duration = time.time() - self.recording_start_time if self.recording_start_time else 0
      self.recording_start_time = None

      if self.audio_mode == MODE_STREAM:
        captured_frames = list(self.stream_frames)
        self.stream_frames = []
        if not captured_frames and rec_duration > 0.2:
          self._recreate_stream(f"no frames captured during {rec_duration:.2f}s recording")
        self.transcription_start_time = time.time()
        self.transcribe_queue.put(("MEMORY", captured_frames, rec_duration))

      elif self.audio_mode == MODE_ON_DEMAND:
        proc = self.record_proc
        wav_path = self.current_wav_path
        self.record_proc = None
        self.current_wav_path = None
        threading.Thread(
            target=self._finish_ondemand_and_submit,
            args=(proc, wav_path, rec_duration),
            daemon=True,
        ).start()

  def _finish_ondemand_and_submit(self, proc, wav_path, rec_duration):
    if proc is not None:
      try:
        if proc.poll() is None and proc.stdin:
          try:
            proc.stdin.write(b"q\n")
            proc.stdin.flush()
            proc.stdin.close()
          except Exception:
            pass
          proc.wait(timeout=2.0)
      except Exception:
        try:
          proc.terminate()
          proc.wait(timeout=1.0)
        except Exception:
          proc.kill()
      log("AUDIO", "On-demand recording worker stopped.")

    self.transcription_start_time = time.time()
    self.transcribe_queue.put(("FILE", wav_path, rec_duration))

  # ---------------------------------------------------------------------------
  # Model Preloading & Warmup
  # ---------------------------------------------------------------------------
  def _preload_model(self):
    log("MODEL", f"Pre-loading {MODEL_NAME} locally onto Apple Silicon...")
    dummy = np.zeros(SAMPLE_RATE, dtype=np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
      wavfile.write(f.name, SAMPLE_RATE, dummy)
      try:
        with self.whisper_lock:
          mlx_whisper.transcribe(f.name, path_or_hf_repo=MODEL_NAME)
      finally:
        if os.path.exists(f.name):
          os.remove(f.name)
    clear_mlx_cache()
    log("MODEL", "Model ready in unified GPU memory.")

  # ---------------------------------------------------------------------------
  # Menu Actions
  # ---------------------------------------------------------------------------
  def toggle_recording(self, _=None):
    self.event_queue.put(("TOGGLE_MENU", time.time()))

  def clear_gpu_cache(self, _=None):
    clear_mlx_cache()
    if hasattr(mx, "metal"):
      active_mem = mx.metal.get_active_memory() / (1024 * 1024)
      peak_mem = mx.metal.get_peak_memory() / (1024 * 1024)
      log("METAL", f"Cleared Metal cache. Active: {active_mem:.1f} MB, Peak: {peak_mem:.1f} MB")
      rumps.notification("Dictate", "GPU Cache Cleared", f"Active: {active_mem:.1f} MB | Peak: {peak_mem:.1f} MB")

  # ---------------------------------------------------------------------------
  # Audio Processing Heuristics
  # ---------------------------------------------------------------------------
  def _trim_trailing_silence(self, audio, threshold=0.008, window_size=1600):
    if len(audio) < window_size:
      return audio
    idx = len(audio)
    while idx >= window_size:
      chunk = audio[idx - window_size : idx]
      rms = np.sqrt(np.mean(chunk**2))
      if rms > threshold:
        break
      idx -= window_size
    # Leave 0.4s padding so trailing syllables are preserved
    trim_end = min(len(audio), idx + 6400)
    return audio[:trim_end]

  def _clean_repetitions(self, text):
    if not text:
      return text
    # 1. Deduplicate sentences repeated 2 or more times consecutively
    pattern_sentence = r'(\b[^.!?]+[.!?])(?:\s*\1){2,}'
    text = re.sub(pattern_sentence, r'\1', text, flags=re.IGNORECASE)
    # 2. Deduplicate phrases of 2+ words repeated 2 or more times consecutively
    pattern_phrase = r'(\b(?:\w+\s+){1,8}\w+)(?:\s+\1){2,}'
    text = re.sub(pattern_phrase, r'\1', text, flags=re.IGNORECASE)
    return text.strip()

  def _is_silence_hallucination(self, text, vocabulary):
    normalized = re.sub(r"[^\w\s]", "", text).strip().lower()
    vocab_echo = re.sub(r"[^\w\s]", "", " ".join(vocabulary)).strip().lower()
    return normalized in SILENCE_HALLUCINATIONS or (vocab_echo and normalized == vocab_echo)

  def _apply_replacements(self, text, replacements):
    for heard, wanted in replacements.items():
      pattern = r"\b" + re.escape(heard) + r"\b"
      text = re.sub(pattern, lambda m: wanted.upper() if m.group(0).isupper() and len(m.group(0)) > 1 else wanted, text, flags=re.IGNORECASE)
    return text

  # ---------------------------------------------------------------------------
  # Dedicated Sequential Transcription Worker Loop
  # ---------------------------------------------------------------------------
  def _transcription_worker_loop(self):
    while True:
      payload_type, payload_data, rec_duration = self.transcribe_queue.get()
      temp_wav_to_clean = None
      try:
        if payload_type == "MEMORY":
          frames = payload_data
          if not frames:
            log("WHISPER", f"No audio frames captured (held {rec_duration:.2f}s).")
            continue
          audio = np.concatenate(frames, axis=0)
          if audio.ndim > 1:
            audio = audio.flatten()
        elif payload_type == "FILE":
          wav_path = payload_data
          temp_wav_to_clean = wav_path
          if not wav_path or not os.path.exists(wav_path) or os.path.getsize(wav_path) < 100:
            log("WHISPER", f"No valid audio recorded (held {rec_duration:.2f}s).")
            continue
          try:
            _, audio_data = wavfile.read(wav_path)
          except Exception as e:
            log("ERROR", f"Failed to read WAV file {wav_path}: {e}")
            continue
          if audio_data.dtype == np.int16:
            audio = audio_data.astype(np.float32) / 32768.0
          elif audio_data.dtype == np.float32:
            audio = audio_data
          else:
            audio = audio_data.astype(np.float32)
          if audio.ndim > 1:
            audio = audio.flatten()
        else:
          continue

        self._process_audio_and_transcribe(audio, rec_duration)

      except Exception as e:
        log("ERROR", f"Unhandled error during transcription: {e}")
      finally:
        self.transcription_start_time = None
        self.title = TITLE_IDLE
        self.overlay.hide()
        clear_mlx_cache()
        if temp_wav_to_clean and os.path.exists(temp_wav_to_clean):
          try:
            os.remove(temp_wav_to_clean)
          except Exception:
            pass

  def _process_audio_and_transcribe(self, audio, rec_duration):
    raw_duration = len(audio) / SAMPLE_RATE
    audio = self._trim_trailing_silence(audio)

    if len(audio) == 0:
      log("WHISPER", f"Only silence detected (held {rec_duration:.2f}s).")
      return

    trimmed_duration = len(audio) / SAMPLE_RATE
    peak_rms = np.sqrt(np.mean(audio**2))

    if trimmed_duration < MIN_SPEECH_SECONDS:
      log("WHISPER", f"Clip too short to contain speech ({trimmed_duration:.2f}s). Skipped.")
      return

    log("WHISPER", f"Transcribing audio: raw={raw_duration:.2f}s, trimmed={trimmed_duration:.2f}s, rms={peak_rms:.4f}")

    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
      wav_path = f.name
      wavfile.write(wav_path, SAMPLE_RATE, audio_int16)

    # Read per transcription so vocabulary edits apply without a restart
    cfg = self._load_config()
    vocabulary = cfg.get("vocabulary") or []
    replacements = cfg.get("replacements") or {}

    try:
      start_t = time.time()
      with self.whisper_lock:
        res = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo=MODEL_NAME,
            verbose=False,
            condition_on_previous_text=False,
            compression_ratio_threshold=2.0,
            no_speech_threshold=0.6,
            initial_prompt=", ".join(vocabulary) + "." if vocabulary else None,
        )
      text = res.get("text", "").strip()
      if trimmed_duration < HALLUCINATION_MAX_SECONDS and self._is_silence_hallucination(text, vocabulary):
        log("WHISPER", f"Dropped likely hallucination on {trimmed_duration:.2f}s clip: \"{text}\"")
        return
      text = self._clean_repetitions(text)
      text = self._apply_replacements(text, replacements)
      elapsed = time.time() - start_t
      rtf = trimmed_duration / max(elapsed, 0.001)

      if text:
        log("WHISPER", f"Result ({elapsed:.2f}s, {rtf:.1f}x realtime): \"{text}\"")
        self._paste_preserving_clipboard(text)
      else:
        log("WHISPER", f"No speech detected in audio ({elapsed:.2f}s).")
    finally:
      if os.path.exists(wav_path):
        os.remove(wav_path)

  # ---------------------------------------------------------------------------
  # Auto-Paste Simulation
  # ---------------------------------------------------------------------------
  def _paste_preserving_clipboard(self, text):
    pasteboard = NSPasteboard.generalPasteboard()
    saved = snapshot_clipboard(pasteboard)
    pasteboard.clearContents()
    pasteboard.setString_forType_(text, NSPasteboardTypeString)
    pasteboard.setString_forType_("", TRANSIENT_PASTEBOARD_TYPE)
    dictation_change = pasteboard.changeCount()
    try:
      time.sleep(0.08)
      self._paste()
    finally:
      time.sleep(CLIPBOARD_RESTORE_SECONDS)
      # A newer copy made by the user wins over the restore
      if pasteboard.changeCount() == dictation_change:
        pasteboard.clearContents()
        if saved:
          pasteboard.writeObjects_(saved)
        log("PASTE", f"Restored clipboard ({len(saved)} item(s)).")

  def _paste(self):
    # The listener sees these synthetic keys; keep them from reading as a hotkey shortcut
    self.pasting = True
    try:
      self.keyboard_controller.press(keyboard.Key.cmd)
      self.keyboard_controller.press('v')
      self.keyboard_controller.release('v')
      self.keyboard_controller.release(keyboard.Key.cmd)
      log("PASTE", "Pasted via keyboard controller.")
    except Exception as e:
      log("PASTE", f"Controller paste failed ({e}), falling back to osascript...")
      try:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'],
            check=False,
        )
        log("PASTE", "Pasted via osascript.")
      except Exception as ex:
        log("ERROR", f"Auto-paste failed: {ex}")
    finally:
      time.sleep(0.1)
      self.pasting = False

  # ---------------------------------------------------------------------------
  # Watchdog Loop (Self-healing for hung states)
  # ---------------------------------------------------------------------------
  def _watchdog_loop(self):
    last_stream_check = 0.0
    while True:
      time.sleep(1)
      now = time.time()

      with self.lock:
        if self.is_recording and self.recording_start_time:
          self.title = recording_title(now - self.recording_start_time)

      if self.transcription_start_time and (now - self.transcription_start_time > TRANSCRIPTION_HUNG_SECONDS):
        log("WATCHDOG", f"Transcription active for {now - self.transcription_start_time:.1f}s. Resetting status title.")
        self.transcription_start_time = None
        self.title = TITLE_IDLE
        self.overlay.hide()

      if self.is_recording and self.recording_start_time and (now - self.recording_start_time > MAX_RECORDING_SECONDS):
        log("WATCHDOG", f"Recording hit the {MAX_RECORDING_SECONDS}s cap. Auto-stopping.")
        self._stop_and_enqueue_transcription()

      if self.audio_mode == MODE_STREAM and now - last_stream_check >= STREAM_CHECK_SECONDS:
        last_stream_check = now
        self._check_stream_health()


if __name__ == "__main__":
  LocalWhisperApp().run()
