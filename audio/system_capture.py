"""System audio capture via platform-specific backends.

On macOS: uses a Swift helper binary (ScreenCaptureKit) compiled on first use.
On Linux: uses parec (PulseAudio/PipeWire) to capture monitor source audio.
On Windows: uses sounddevice WASAPI loopback against the default output device,
with a Stereo Mix input device fallback for older PortAudio builds.
"""

from __future__ import annotations

import os
import shutil
import signal
import queue
import subprocess
import threading
import numpy as np
from pathlib import Path

from audio.platform import CURRENT_PLATFORM, Platform, has_swiftc, get_output_device_info
from config import SAMPLE_RATE, BIN_DIR

# 1024 samples * 4 bytes/float32 = 4096 bytes per chunk
_CHUNK_SAMPLES = 1024
_CHUNK_BYTES = _CHUNK_SAMPLES * 4

# Swift source lives next to this file
_SWIFT_SOURCE = Path(__file__).parent / "_macos_sck.swift"
_BINARY_PATH = BIN_DIR / "sck-helper"


class SystemCapture:
    """Captures system/desktop audio. Same interface as AudioCapture."""

    def __init__(self):
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stream = None  # sounddevice.InputStream — Windows WASAPI loopback only
        self._active = False
        self._unavailable = False
        self._status_message = ""
        self._bt_multi_output_active = False  # True if we created a multi-output device

    # ── public API (matches AudioCapture) ────────────────────

    def start(self) -> None:
        if self._active:
            return
        if CURRENT_PLATFORM == Platform.LINUX:
            self._start_linux()
            return
        if CURRENT_PLATFORM == Platform.WINDOWS:
            self._start_windows()
            return
        if CURRENT_PLATFORM != Platform.MACOS:
            self._unavailable = True
            self._status_message = "system audio capture not supported on this platform"
            return

        # Kill any stale sck-helper from a prior crash so it releases the audio tap
        self._kill_stale_helpers()

        binary = self._ensure_binary()
        if binary is None:
            return

        try:
            self._proc = subprocess.Popen(
                [str(binary)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )
        except OSError as e:
            self._unavailable = True
            self._status_message = f"failed to launch system audio helper: {e}"
            return

        self._active = True
        self._status_message = ""

        # Bluetooth detected — route audio through BlackHole if available
        try:
            dev_info = get_output_device_info()
            if dev_info.get("is_bluetooth"):
                from audio.blackhole import is_blackhole_installed, create_multi_output
                if is_blackhole_installed():
                    ok, msg, _ = create_multi_output()
                    if ok:
                        self._bt_multi_output_active = True
                    else:
                        self._status_message = (
                            "system audio limited with Bluetooth — "
                            "mic recording will continue normally"
                        )
                else:
                    self._status_message = (
                        "system audio limited with Bluetooth — "
                        "mic recording will continue normally"
                    )
        except Exception:
            pass

        # Reader thread: stdout → chunked numpy arrays → queue
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="sck-reader"
        )
        self._reader_thread.start()

        # Stderr monitor: capture error messages from helper
        threading.Thread(
            target=self._stderr_loop, daemon=True, name="sck-stderr"
        ).start()

    def stop(self) -> None:
        # Windows path: sounddevice stream rather than subprocess
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._active = False
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            return

        if self._proc is None:
            return

        # Send SIGTERM so the helper's signal handler can stop the SCStream
        # and release the CoreAudio tap before exiting
        try:
            self._proc.send_signal(signal.SIGTERM)
        except OSError:
            pass

        # Wait for clean shutdown (helper stops SCStream, then exits)
        try:
            self._proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            # Force kill as last resort
            try:
                self._proc.kill()
                self._proc.wait(timeout=1)
            except OSError:
                pass

        self._proc = None
        self._active = False

        # Teardown multi-output device if we created one
        if self._bt_multi_output_active:
            try:
                from audio.blackhole import destroy_multi_output
                destroy_multi_output()
            except Exception:
                pass
            self._bt_multi_output_active = False

        # Drain remaining items from queue
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

    def drain(self) -> list[np.ndarray]:
        chunks = []
        while True:
            try:
                chunks.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return chunks

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def status_message(self) -> str:
        return self._status_message

    # ── private ──────────────────────────────────────────────

    @staticmethod
    def _kill_stale_helpers() -> None:
        """Find and SIGTERM any orphaned sck-helper processes."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "sck-helper"],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.strip().splitlines():
                pid = int(line.strip())
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        except Exception:
            pass

    def _ensure_binary(self) -> Path | None:
        """Compile the Swift helper if needed. Returns binary path or None."""
        if not _SWIFT_SOURCE.exists():
            self._unavailable = True
            self._status_message = "system audio helper source not found"
            return None

        # Check if binary exists and is up-to-date
        if _BINARY_PATH.exists():
            src_mtime = _SWIFT_SOURCE.stat().st_mtime
            bin_mtime = _BINARY_PATH.stat().st_mtime
            if bin_mtime >= src_mtime:
                return _BINARY_PATH

        # Need to compile
        if not has_swiftc():
            self._unavailable = True
            self._status_message = (
                "system audio requires Swift compiler — "
                "run: xcode-select --install"
            )
            return None

        BIN_DIR.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["swiftc", "-O", "-o", str(_BINARY_PATH), str(_SWIFT_SOURCE)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                self._unavailable = True
                err = result.stderr.strip()[:200] if result.stderr else "unknown error"
                self._status_message = f"failed to compile system audio helper: {err}"
                return None
        except subprocess.TimeoutExpired:
            self._unavailable = True
            self._status_message = "system audio helper compilation timed out"
            return None
        except OSError as e:
            self._unavailable = True
            self._status_message = f"compilation error: {e}"
            return None

        return _BINARY_PATH

    def _reader_loop(self) -> None:
        """Read raw PCM from helper stdout, chunk into 1024-sample blocks."""
        buf = bytearray()
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._active = False
            return

        try:
            while True:
                data = proc.stdout.read(_CHUNK_BYTES)
                if not data:
                    break  # EOF — helper exited

                buf.extend(data)
                while len(buf) >= _CHUNK_BYTES:
                    chunk_bytes = bytes(buf[:_CHUNK_BYTES])
                    del buf[:_CHUNK_BYTES]
                    chunk = np.frombuffer(chunk_bytes, dtype=np.float32).copy()
                    try:
                        self.queue.put_nowait(chunk)
                    except queue.Full:
                        # Drop oldest chunk to prevent memory growth
                        try:
                            self.queue.get_nowait()
                        except queue.Empty:
                            pass
                        self.queue.put_nowait(chunk)
        except (OSError, ValueError):
            pass
        finally:
            self._active = False
            # Check exit code for permission errors (macOS-specific)
            if proc.poll() == 1 and CURRENT_PLATFORM == Platform.MACOS:
                self._status_message = (
                    "Screen Recording permission required — "
                    "grant access in System Settings > Privacy & Security > Screen Recording"
                )

    def _stderr_loop(self) -> None:
        """Capture stderr from helper for diagnostics."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                msg = line.decode("utf-8", errors="replace").strip()
                if msg and not self._status_message:
                    self._status_message = msg
        except (OSError, ValueError):
            pass

    # ── Linux: PulseAudio / PipeWire via parec ────────────────

    def _start_linux(self) -> None:
        """Start system audio capture on Linux using parec."""
        if not shutil.which("parec"):
            self._unavailable = True
            self._status_message = (
                "parec not found — install pulseaudio-utils or pipewire-pulse"
            )
            return

        if not shutil.which("pactl"):
            self._unavailable = True
            self._status_message = (
                "pactl not found — install pulseaudio-utils or pipewire-pulse"
            )
            return

        monitor = self._find_monitor_source()
        if monitor is None:
            self._unavailable = True
            self._status_message = "no monitor source found — is PulseAudio/PipeWire running?"
            return

        try:
            self._proc = subprocess.Popen(
                [
                    "parec",
                    "--format=float32le",
                    f"--rate={SAMPLE_RATE}",
                    "--channels=1",
                    f"--device={monitor}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            self._unavailable = True
            self._status_message = f"failed to launch parec: {e}"
            return

        self._active = True
        self._status_message = ""

        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="parec-reader"
        )
        self._reader_thread.start()

        threading.Thread(
            target=self._stderr_loop, daemon=True, name="parec-stderr"
        ).start()

    # ── Windows: WASAPI loopback via sounddevice ──────────────

    def _start_windows(self) -> None:
        """Start system audio capture on Windows.

        Approach: enumerate input devices for one of:
          - PortAudio's auto-exposed WASAPI loopback inputs (named
            ``<DeviceName> (loopback)`` — best UX, no user setup)
          - "Stereo Mix" / "Wave Out Mix" / "What U Hear" (legacy,
            requires user to enable in Sound settings)

        We open the chosen device at its NATIVE sample rate (Windows mix
        format, typically 44.1k or 48k) and resample chunks to ``SAMPLE_RATE``
        in the callback. PortAudio's WASAPI host will *not* auto-resample
        unless ``WasapiSettings(auto_convert=True)`` is supplied AND the
        device's host_api is WASAPI, so we hand the resampling to scipy
        which works regardless of host API.

        Note: ``sd.WasapiSettings(loopback=True)`` is *not* a real API
        (see spatialaudio/python-sounddevice#281). PortAudio surfaces the
        loopback functionality through device enumeration instead.
        """
        # Always start from a clean slate so a previous-failed start() does
        # not leave a stale error string lingering on a successful retry.
        self._status_message = ""
        self._unavailable = False

        try:
            import sounddevice as sd
        except Exception as e:
            self._unavailable = True
            self._status_message = f"sounddevice unavailable for system audio: {e}"
            return

        # Find a usable loopback / Stereo Mix input device
        loopback_id = None
        loopback_name = ""
        try:
            devices = sd.query_devices()
        except Exception as e:
            self._unavailable = True
            self._status_message = f"failed to enumerate audio devices: {e}"
            return

        for idx, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) <= 0:
                continue
            name_lower = (dev.get("name") or "").lower()
            if any(kw in name_lower for kw in (
                "loopback", "stereo mix", "wave out mix", "what u hear",
            )):
                loopback_id = idx
                loopback_name = dev.get("name", "")
                break

        if loopback_id is None:
            self._unavailable = True
            self._status_message = (
                "system audio unavailable: no WASAPI loopback or Stereo Mix "
                "input found. Enable 'Stereo Mix' in Settings -> Sound -> "
                "More sound settings, or use a recent Windows build with "
                "PortAudio loopback support. Mic capture continues normally."
            )
            return

        # Resolve native sample rate + channel count for the chosen device.
        # Falling back to SAMPLE_RATE only if the device claims a bogus value.
        try:
            native_rate = int(devices[loopback_id].get("default_samplerate") or SAMPLE_RATE)
        except Exception:
            native_rate = SAMPLE_RATE
        if native_rate <= 0:
            native_rate = SAMPLE_RATE

        # Most loopback devices are stereo. Open as stereo and downmix in the
        # callback (avoids PortAudio rejecting a channels=1 request against a
        # device whose mix format is 2-channel).
        try:
            native_channels = int(devices[loopback_id].get("max_input_channels", 2)) or 2
            native_channels = min(native_channels, 2)
        except Exception:
            native_channels = 2

        # scipy.signal.resample_poly is already in the dep tree (scipy>=1.10)
        # and is plenty fast for the small chunk sizes we deliver.
        if native_rate != SAMPLE_RATE:
            try:
                from math import gcd
                from scipy.signal import resample_poly
                _g = gcd(SAMPLE_RATE, native_rate)
                _up = SAMPLE_RATE // _g
                _down = native_rate // _g
                _resample = lambda x: resample_poly(x, _up, _down).astype(np.float32, copy=False)
            except Exception as e:
                self._unavailable = True
                self._status_message = (
                    f"system audio resampling unavailable ({e}); install scipy"
                )
                return
        else:
            _resample = lambda x: x

        def _callback(indata, frames, time_info, status):  # noqa: ARG001
            try:
                # Downmix to mono if the device gave us multichannel
                if indata.ndim > 1 and indata.shape[1] > 1:
                    mono = indata.mean(axis=1).astype(np.float32, copy=False)
                elif indata.ndim > 1:
                    mono = indata[:, 0].astype(np.float32, copy=False)
                else:
                    mono = indata.astype(np.float32, copy=False)
                chunk = _resample(mono)
                try:
                    self.queue.put_nowait(chunk.copy() if chunk.base is not None else chunk)
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.queue.put_nowait(chunk.copy() if chunk.base is not None else chunk)
                    except queue.Full:
                        pass
            except Exception:
                pass  # never raise out of the audio callback

        # Block size: keep ~64ms regardless of native rate so the callback
        # runs at a consistent cadence for the rest of the pipeline.
        block_size = max(_CHUNK_SAMPLES, int(native_rate * _CHUNK_SAMPLES / SAMPLE_RATE))

        try:
            self._stream = sd.InputStream(
                device=loopback_id,
                samplerate=native_rate,
                channels=native_channels,
                dtype="float32",
                blocksize=block_size,
                callback=_callback,
            )
            self._stream.start()
        except Exception as e:
            self._unavailable = True
            self._status_message = f"failed to open {loopback_name or 'system audio'}: {e}"
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            return

        self._active = True
        self._status_message = f"system audio via {loopback_name}"

    @staticmethod
    def _find_monitor_source() -> str | None:
        """Find a PulseAudio/PipeWire monitor source for system audio capture."""
        if not shutil.which("pactl"):
            return None
        try:
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            for line in result.stdout.strip().splitlines():
                fields = line.split("\t")
                if len(fields) >= 2 and ".monitor" in fields[1]:
                    return fields[1]
        except Exception:
            pass
        # Fallback: PulseAudio/PipeWire virtual name that resolves to the
        # current default output's monitor (works even when pactl doesn't
        # list .monitor sources by name, e.g. some PipeWire setups).
        return "@DEFAULT_SINK@.monitor"
