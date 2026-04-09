# VoxTerm Cross-Platform Support Plan

Actionable plan for extending VoxTerm to Linux and Windows. Each item lists the file, what needs to change, and estimated effort so a future agent can pick this up and execute sequentially.

---

## Linux Support

**Status: Ready today** -- all core features work on Linux.

### What's already implemented

- **Mic capture** (`audio/capture.py`): sounddevice/PortAudio is cross-platform, works out of the box.
- **System audio** (`audio/system_capture.py`): `_start_linux()` captures via `parec` (PulseAudio/PipeWire monitor source). Fully implemented with auto-detection of `.monitor` sources via `pactl`.
- **Transcription** (`audio/transcriber.py`, `config.py`): Qwen3-ASR (primary via `qwen-asr` PyTorch package) + faster-whisper (fallback). Model registry in `config.py` lines 30-46 with `fw-*` entries.
- **ONNX diarization** (`audio/diarization/onnx_embedder.py`): onnxruntime runs identically on Linux, no platform-specific code.
- **XDG paths** (`paths.py`): `$XDG_DATA_HOME/voxterm` and `$XDG_CONFIG_HOME/voxterm` (lines 21-31).
- **Dictation mode**: Full implementation across X11 and Wayland:
  - `dictation/hotkey.py`: `_X11Hotkey` (python-xlib XGrabKey) and `_SignalHotkey` (SIGUSR1 for Wayland compositors).
  - `dictation/injector.py`: `_X11Injector` (xdotool) and `_WaylandInjector` (wtype/ydotool/wl-copy fallback).
  - `dictation/indicator.py`: `_PystrayIndicator` with PIL icon generation.
  - `dictation/app.py`: Linux tool checks, PID file for Wayland signal-based hotkey.
- **P2P networking** (`network/`): zeroconf mDNS discovery, TCP/UDP -- fully cross-platform.
- **Encryption** (`audio/speakers/crypto.py`): `cryptography` library backend for AES-256-CBC on Linux, file-based key storage with `chmod 0600`.
- **Platform detection** (`audio/platform.py`): `_get_output_device_info_linux()` via `pactl get-default-sink` for Bluetooth detection.
- **Clipboard** (`tui/app.py` line 69): supports `xclip`, `xsel`, `wl-copy`.
- **Requirements** (`requirements.txt`): Platform markers for `qwen-asr`, `torch`, `faster-whisper`, `pystray`, `Pillow`, `python-xlib` all gated with `sys_platform == "linux"`.
- **Installer** (`install.sh`): Bash installer with venv creation, works on both macOS and Linux.

### Minor polish items

- `install.sh` line 63 references `~/Library/Application Support/voxterm/` in the uninstall message -- should conditionally show the XDG path on Linux.
- The `voxterm` launcher script works as-is on Linux (bash + venv).

---

## Windows Support

### Phase 1: Make it launch (~2-3 days)

Goal: `python -m tui.app` starts the TUI on Windows without crashing at import time.

#### 1. `paths.py` -- Add Windows paths

**What's wrong**: Line 33 raises `RuntimeError("Unsupported platform: {sys.platform}")` for `win32`.

**What to do**: Add a Windows branch using `%LOCALAPPDATA%` for data and `%APPDATA%` for config:

```python
elif sys.platform == "win32":
    _appdata = Path(os.environ.get("LOCALAPPDATA", _home / "AppData" / "Local"))
    DATA_DIR = _appdata / "voxterm"
    SESSIONS_DIR = _home / "Documents" / "voxterm"
    LIVE_DIR = SESSIONS_DIR / ".live"
    BIN_DIR = DATA_DIR / "bin"
    CRASH_DIR = DATA_DIR / "crashes"
    STATE_FILE = DATA_DIR / "state.json"
```

**Effort**: ~30 minutes.

#### 2. `config.py` -- Add Windows model registry

**What's wrong**: Line 47 raises `RuntimeError` for non-darwin/linux platforms. No model registry for Windows.

**What to do**: Add a `sys.platform == "win32"` block, copying the Linux registry (faster-whisper + qwen-asr both work on Windows via PyTorch/CT2):

```python
elif sys.platform == "win32":
    DEFAULT_MODEL = "qwen3-0.6b"
    AVAILABLE_MODELS = {
        "qwen3-0.6b":  "Qwen/Qwen3-ASR-0.6B",
        "qwen3-1.7b":  "Qwen/Qwen3-ASR-1.7B",
        "fw-tiny":           "tiny",
        "fw-base":           "base",
        "fw-small":          "small",
        "fw-medium":         "medium",
        "fw-large-v3":       "large-v3",
        "fw-distil-large-v3": "distil-large-v3",
    }
    QWEN3_MODELS = {"qwen3-0.6b", "qwen3-1.7b"}
    WHISPER_MODEL = None
    FASTER_WHISPER_MODELS = {"fw-tiny", "fw-base", "fw-small", "fw-medium", "fw-large-v3", "fw-distil-large-v3"}
```

Also add `DICTATION_HOTKEY_WINDOWS = ("ctrl", "shift", "d")` near line 109.

**Effort**: ~30 minutes.

#### 3. `requirements.txt` -- Add win32 platform markers

**What's wrong**: No Windows-specific deps listed. `faster-whisper`, `torch`, `pystray`, and `Pillow` are all needed on Windows but not currently declared.

**What to do**: Add:

```
qwen-asr>=0.0.6; sys_platform == "win32"
torch>=2.0.0; sys_platform == "win32"
faster-whisper>=1.0.0; sys_platform == "win32"
pystray>=0.19.0; sys_platform == "win32"
Pillow>=10.0.0; sys_platform == "win32"
```

Note: `python-xlib` is NOT needed on Windows (X11-only). `rumps` is macOS-only.

**Effort**: ~15 minutes.

#### 4. `diagnostics.py` -- Conditional `resource` module import

**What's wrong**: Line 10 does `import resource` unconditionally. The `resource` module is Unix-only and will raise `ModuleNotFoundError` on Windows. Line 176 uses `resource.getrusage()` for peak RSS.

**What to do**: Guard the import and provide a psutil fallback for memory stats:

```python
try:
    import resource as _resource
except ImportError:
    _resource = None  # Windows -- use psutil fallback

# In write_crash_dump(), replace lines 176-178:
if _resource is not None:
    rss_bytes = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss_bytes / (1024 * 1024)
else:
    try:
        import psutil
        rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        rss_mb = -1
```

Also guard `signal.SIGSEGV` handler (line 67) and `termios` import (line 62) -- both are Unix-only. Wrap `setup_signal_handlers()` with a platform check to no-op on Windows.

**Effort**: ~1 hour.

#### 5. `tui/app.py` -- Same `resource` module fix in memory watchdog

**What's wrong**: Lines 689-691 import `resource` for the periodic GC memory watchdog. Same `ModuleNotFoundError` on Windows.

**What to do**: Apply the same conditional pattern as diagnostics.py:

```python
def _periodic_gc(self):
    gc.collect()
    try:
        import resource as _resource
        rss_bytes = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        rss_mb = rss_bytes / (1024 * 1024)
    except ImportError:
        try:
            import psutil
            rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
        except ImportError:
            return  # can't check memory, skip watchdog
    # ... rest of watchdog logic unchanged
```

**Effort**: ~30 minutes.

#### 6. `install.ps1` -- PowerShell installer

**What's wrong**: `install.sh` is bash-only. No Windows installer exists.

**What to do**: Create `install.ps1` with equivalent functionality:
- Check Python 3.9+ is available.
- Download release tarball or clone repo to `$env:LOCALAPPDATA\voxterm`.
- Create venv, `pip install -r requirements.txt`.
- Create `voxterm.bat` launcher in a PATH-accessible location (`$env:LOCALAPPDATA\voxterm\bin`).
- Print instructions for adding to PATH if needed.

Model the structure on `install.sh` but use PowerShell idioms (`Invoke-WebRequest`, `Expand-Archive`, etc.).

**Effort**: ~2-3 hours.

#### 7. `voxterm.bat` -- Windows launcher script

**What's wrong**: The `voxterm` launcher is a bash script. No equivalent for Windows.

**What to do**: Create `voxterm.bat`:

```bat
@echo off
set DIR=%~dp0
if "%1"=="--dictate" (
    shift
    "%DIR%.venv\Scripts\python.exe" -m dictation %*
    goto :eof
)
if "%1"=="-D" (
    shift
    "%DIR%.venv\Scripts\python.exe" -m dictation %*
    goto :eof
)
"%DIR%.venv\Scripts\python.exe" -m tui.app %*
```

**Effort**: ~15 minutes.

---

### Phase 2: Core features (~2-3 days)

Goal: System audio capture, Bluetooth detection, encryption, and clipboard work on Windows.

#### 8. `audio/system_capture.py` -- Windows system audio via WASAPI loopback

**What's wrong**: `start()` (line 44) only handles `Platform.LINUX` and `Platform.MACOS`. Windows falls through to "unsupported platform" (line 52). No WASAPI loopback implementation exists.

**What to do**: Add `_start_windows()` method using WASAPI loopback. Two approaches:

**Option A (recommended)**: Use `sounddevice` with WASAPI loopback device. sounddevice already supports WASAPI on Windows via PortAudio. Query for loopback devices:

```python
def _start_windows(self) -> None:
    import sounddevice as sd
    # Find WASAPI loopback device
    loopback = None
    for i, dev in enumerate(sd.query_devices()):
        if dev['max_input_channels'] > 0 and 'loopback' in dev['name'].lower():
            loopback = i
            break
    if loopback is None:
        self._unavailable = True
        self._status_message = "no WASAPI loopback device found"
        return
    # Open input stream on loopback device
    self._stream = sd.InputStream(
        device=loopback, channels=1, samplerate=SAMPLE_RATE,
        dtype='float32', blocksize=_CHUNK_SAMPLES,
        callback=self._wasapi_callback,
    )
    self._stream.start()
    self._active = True
```

**Option B**: Use the `soundcard` library (`pip install soundcard`) which has explicit WASAPI loopback support via `soundcard.default_speaker().recorder()`.

Also: guard `_kill_stale_helpers()` (uses `pgrep` / `os.kill` with Unix signals) -- no-op on Windows.

**Effort**: ~4-6 hours (needs testing with real Windows audio stack).

#### 9. `audio/platform.py` -- Windows Bluetooth/output device detection

**What's wrong**: `get_output_device_info()` (line 30) returns the fallback dict for Windows (`Platform.WINDOWS` is not handled). No Windows device detection.

**What to do**: Add `_get_output_device_info_windows()` using `sounddevice.query_devices(kind='output')`:

```python
if platform == Platform.WINDOWS:
    return _get_output_device_info_windows(fallback, _BT_KEYWORDS)

def _get_output_device_info_windows(fallback: dict, bt_keywords: tuple) -> dict:
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind='output')
        if dev and isinstance(dev, dict):
            name = dev.get('name', '')
            is_bt = any(kw in name.lower() for kw in bt_keywords)
            return {'name': name, 'is_bluetooth': is_bt}
    except Exception:
        pass
    return fallback
```

**Effort**: ~1 hour.

#### 10. `audio/speakers/crypto.py` -- Guard `os.fchmod`, consider Windows DPAPI

**What's wrong**: `_file_key_set()` at line 212 calls `os.fchmod(fd, 0o600)` which raises `AttributeError` on Windows (no `fchmod`). File permissions work differently on Windows.

**What to do**:
- Guard `os.fchmod` with a platform check. On Windows, use ACLs via `icacls` or skip (file is in `%LOCALAPPDATA%` which is user-private by default).
- For key storage, consider Windows DPAPI via `ctypes` (`CryptProtectData`/`CryptUnprotectData`) as the platform keystore equivalent of macOS Keychain. This provides OS-managed encryption without user key management.
- Add `_dpapi_key_get()` and `_dpapi_key_set()` functions analogous to the Keychain functions.
- Update `get_or_create_key()` to dispatch: macOS -> Keychain, Windows -> DPAPI, Linux -> file.

```python
if sys.platform == "win32":
    key = _dpapi_key_get()
elif _sec:
    key = _keychain_get()
else:
    key = _file_key_get()
```

**Effort**: ~3-4 hours (DPAPI integration needs careful testing).

#### 11. `tui/app.py` -- Add `clip.exe` to clipboard command list

**What's wrong**: `_clipboard_cmd()` (line 69) handles macOS (`pbcopy`) and Linux (`xclip`/`xsel`/`wl-copy`) but not Windows.

**What to do**: Add Windows clipboard support. `clip.exe` is built into Windows:

```python
def _clipboard_cmd() -> list[str] | None:
    if sys.platform == "darwin":
        return ["pbcopy"]
    if sys.platform == "win32":
        return ["clip.exe"]
    # Linux tools...
```

Note: `clip.exe` handles UTF-8 on modern Windows. For legacy Windows, consider `pyperclip` as a fallback.

**Effort**: ~15 minutes.

---

### Phase 3: Dictation mode (~1-2 days)

Goal: Global hotkey, keyboard injection, and system tray indicator work on Windows.

#### 12. `dictation/hotkey.py` -- `_WindowsHotkey` using `ctypes` + `RegisterHotKey`

**What's wrong**: `get_hotkey()` (line 336) raises `RuntimeError` for non-macOS/Linux platforms. No Windows global hotkey implementation.

**What to do**: Add `_WindowsHotkey` class using Win32 `RegisterHotKey` API via ctypes:

```python
class _WindowsHotkey(GlobalHotkey):
    """Global hotkey on Windows using RegisterHotKey (background thread)."""

    _DEBOUNCE_SEC = 0.4
    _HOTKEY_ID = 1
    _MOD_CONTROL = 0x0002
    _MOD_SHIFT = 0x0004
    _VK_D = 0x44

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import ctypes
        user32 = ctypes.windll.user32
        user32.RegisterHotKey(None, self._HOTKEY_ID,
                              self._MOD_CONTROL | self._MOD_SHIFT, self._VK_D)
        msg = ctypes.wintypes.MSG()
        while self._running:
            if user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
                if msg.message == 0x0312:  # WM_HOTKEY
                    self._callback()
        user32.UnregisterHotKey(None, self._HOTKEY_ID)
```

Update the factory to dispatch `Platform.WINDOWS` -> `_WindowsHotkey`.

**Effort**: ~2-3 hours.

#### 13. `dictation/injector.py` -- `_WindowsInjector` using `ctypes` + `SendInput`

**What's wrong**: `get_injector()` (line 236) raises `RuntimeError` for non-macOS/Linux platforms. No Windows text injection.

**What to do**: Add `_WindowsInjector` using Win32 `SendInput` API via ctypes:

```python
class _WindowsInjector(KeyboardInjector):
    """Injects keystrokes on Windows using SendInput (Unicode)."""

    def type_text(self, text: str) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        for char in text:
            # KEYBDINPUT with KEYEVENTF_UNICODE
            inputs = []
            for flag in (0x0004, 0x0004 | 0x0002):  # UNICODE down, UNICODE up
                ki = ...  # Build INPUT struct
                inputs.append(ki)
            user32.SendInput(len(inputs), ...)

    def is_available(self) -> bool:
        return sys.platform == "win32"
```

Update the factory to dispatch `Platform.WINDOWS` -> `_WindowsInjector`.

**Effort**: ~3-4 hours (SendInput struct layout is fiddly).

#### 14. `dictation/indicator.py` -- Route Windows to `_PystrayIndicator`

**What's wrong**: `get_indicator()` (line 212) returns `_StdoutIndicator` for Windows (falls through macOS and Linux checks). No system tray on Windows.

**What to do**: `_PystrayIndicator` already works on Windows (pystray supports Windows system tray natively). Just update the factory:

```python
def get_indicator(**kwargs) -> DictationIndicator:
    if CURRENT_PLATFORM == Platform.MACOS:
        return _RumpsIndicator(**kwargs)
    if CURRENT_PLATFORM in (Platform.LINUX, Platform.WINDOWS):
        return _PystrayIndicator(**kwargs)
    return _StdoutIndicator(**kwargs)
```

**Effort**: ~15 minutes (pystray handles the platform differences).

#### 15. `dictation/app.py` -- Windows platform checks, PID file path

**What's wrong**: Line 174 prints "Unsupported platform" and exits for Windows. `_write_pid_file()` uses `/tmp/voxterm-dictation.pid` which doesn't exist on Windows.

**What to do**:
- Add a `_check_windows_tools()` function (minimal -- SendInput is always available).
- Add `Platform.WINDOWS` to the platform check at line 167.
- Fix PID file path to use `tempfile.gettempdir()`:

```python
def _write_pid_file() -> None:
    import tempfile
    pid_file = os.path.join(tempfile.gettempdir(), "voxterm-dictation.pid")
    ...
```

- Update the hotkey log message for Windows (line 225): `"hotkey: Ctrl+Shift+D"`.
- In `dictation/hotkey.py`, `_SignalHotkey._PID_FILE` also uses `/tmp/` -- update similarly.

**Effort**: ~1 hour.

---

### Already cross-platform (no work needed)

These components are pure Python or use cross-platform libraries and require zero changes for Windows:

| Component | File(s) | Why it works |
|-----------|---------|--------------|
| ONNX speaker diarization | `audio/diarization/onnx_embedder.py` | onnxruntime is cross-platform |
| Silero VAD | `audio/vad.py` | ONNX-based, no platform deps |
| Pure-numpy Fbank | `audio/diarization/fbank.py` | numpy only |
| Speaker clustering | `audio/diarization/cluster.py`, `engine.py` | numpy + scipy |
| Speaker store (SQLite) | `audio/speakers/store.py` | stdlib sqlite3 |
| Speaker models | `audio/speakers/models.py` | pure Python dataclasses |
| P2P networking | `network/` (all files) | zeroconf + stdlib sockets |
| Textual TUI | `tui/` (all widgets) | Textual is cross-platform |
| Waveform widget | `tui/widgets/waveform.py` | pure Textual + numpy |
| Transcript widget | `tui/widgets/transcript.py` | pure Textual |
| Audio capture (mic) | `audio/capture.py` | sounddevice/PortAudio |
| Audio buffer | `audio/buffer.py` | pure Python + numpy |
| Encryption core | `audio/speakers/crypto.py` (encrypt/decrypt) | `cryptography` lib or CommonCrypto |
| IPC protocol | `audio/diarization/ipc.py` | stdlib struct + pipes |
| Config store | `config_store.py` | stdlib json |
| Language ID | `audio/lid.py` | ONNX-based |
| Hallucination filter | `audio/transcriber.py` | pure Python regex |
| Transcriber engines | `audio/transcriber.py` | Qwen3/faster-whisper work on Windows via PyTorch |

---

### Testing strategy

1. **Phase 1 gate**: `python -m tui.app --help` runs without import errors on Windows. TUI renders. Mic capture works.
2. **Phase 2 gate**: System audio captures via WASAPI loopback. Clipboard copy works. Encryption round-trips.
3. **Phase 3 gate**: Ctrl+Shift+D toggles dictation. Text appears in Notepad. System tray icon shows state.

### Total estimated effort

| Phase | Scope | Effort |
|-------|-------|--------|
| Phase 1 | Make it launch | ~2-3 days |
| Phase 2 | Core features | ~2-3 days |
| Phase 3 | Dictation mode | ~1-2 days |
| **Total** | **Full Windows support** | **~5-8 days** |
