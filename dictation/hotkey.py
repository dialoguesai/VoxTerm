"""Global hotkey listener — activates dictation from any application.

Push-to-talk: the hotkey fires ``on_press`` when the key goes down and
``on_release`` when it comes back up.  Hold the backtick (`) key to record,
release to paste.  Because backtick is an ordinary printable key, the listener
swallows it while held so it isn't typed into the focused app (Shift+` for the
tilde ~ still works normally).

macOS: Quartz CGEvent tap via ctypes (keyDown/keyUp, event swallowed).
Linux/X11: python-xlib XGrabKey (KeyPress/KeyRelease, auto-repeat filtered).
Linux/Wayland: SIGUSR1 (press) / SIGUSR2 (release) — user configures compositor.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from abc import ABC, abstractmethod
from typing import Callable

from audio.platform import CURRENT_PLATFORM, Platform


class GlobalHotkey(ABC):
    """Base class for platform-specific global hotkey registration.

    ``on_press`` fires once when the hotkey combo is pressed; ``on_release``
    fires once when it is released.  Key auto-repeat between press and release
    is suppressed so each is delivered exactly once per hold.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ):
        self._on_press = on_press
        self._on_release = on_release or (lambda: None)

    @abstractmethod
    def start(self) -> None:
        """Begin listening for the hotkey."""

    @abstractmethod
    def stop(self) -> None:
        """Stop listening."""


# ---------------------------------------------------------------------------
# macOS: Quartz CGEvent tap via ctypes
# ---------------------------------------------------------------------------

class _MacOSHotkey(GlobalHotkey):
    """Global push-to-talk hotkey on macOS using a Quartz event tap.

    Listens for the backtick (`) key by installing a CGEvent tap that monitors
    keyDown/keyUp events.  Runs in a daemon thread with its own CFRunLoop.
    ``on_press`` fires when ` goes down (with no modifiers); ``on_release``
    fires when it comes up.  The key event is swallowed while used for
    push-to-talk so the backtick character isn't typed into the focused app.
    Shift+` (tilde ~) and other modified combos pass through untouched.
    """

    # Virtual keycode for the backtick/grave key on macOS (kVK_ANSI_Grave)
    _KEY_GRAVE = 50
    # Modifier masks (CGEventFlags) — if any of these are held we let the
    # keystroke through (so Shift+` types ~, Cmd+` cycles windows, etc.)
    _kCGEventFlagMaskShift = 0x00020000
    _kCGEventFlagMaskControl = 0x00040000
    _kCGEventFlagMaskAlternate = 0x00080000
    _kCGEventFlagMaskCommand = 0x00100000
    _MODIFIER_MASK = (
        _kCGEventFlagMaskShift
        | _kCGEventFlagMaskControl
        | _kCGEventFlagMaskAlternate
        | _kCGEventFlagMaskCommand
    )

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ):
        super().__init__(on_press, on_release)
        self._thread: threading.Thread | None = None
        self._running = False
        self._run_loop_ref = None
        self._tap = None  # CGEventTap ref (for re-enabling if disabled)
        self._pressed = False  # is the key currently held?

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._run_loop_ref is not None:
            import ctypes
            import ctypes.util
            cf_path = ctypes.util.find_library("CoreFoundation")
            cf = ctypes.cdll.LoadLibrary(cf_path)
            cf.CFRunLoopStop(self._run_loop_ref)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _fire_press(self) -> None:
        if self._pressed:
            return  # ignore key auto-repeat while held
        self._pressed = True
        try:
            self._on_press()
        except Exception:
            pass

    def _fire_release(self) -> None:
        if not self._pressed:
            return
        self._pressed = False
        try:
            self._on_release()
        except Exception:
            pass

    def _run(self) -> None:
        import ctypes
        import ctypes.util

        # Load frameworks
        cg_path = ctypes.util.find_library("CoreGraphics")
        cf_path = ctypes.util.find_library("CoreFoundation")
        cg = ctypes.cdll.LoadLibrary(cg_path)
        cf = ctypes.cdll.LoadLibrary(cf_path)

        # CGEventTapCreate callback type
        CGEventTapCallBack = ctypes.CFUNCTYPE(
            ctypes.c_void_p,  # return: CGEventRef (pass-through or NULL)
            ctypes.c_void_p,  # proxy
            ctypes.c_uint32,  # type (CGEventType)
            ctypes.c_void_p,  # event (CGEventRef)
            ctypes.c_void_p,  # userInfo
        )

        kCGEventKeyDown = 10
        kCGEventKeyUp = 11
        # Special types delivered when the system disables the tap.
        kCGEventTapDisabledByTimeout = 0xFFFFFFFE
        kCGEventTapDisabledByUserInput = 0xFFFFFFFF
        kCGSessionEventTap = 1  # session-level tap
        kCGHeadInsertEventTap = 0
        kCGEventTapOptionDefault = 0  # active tap: callback may swallow events

        callback_ref = self  # prevent GC

        @CGEventTapCallBack
        def _tap_callback(proxy, event_type, event, user_info):
            # If the system disabled the tap (slow callback / heavy input),
            # re-enable it — otherwise the hotkey silently stops working.
            if event_type in (kCGEventTapDisabledByTimeout,
                              kCGEventTapDisabledByUserInput):
                if callback_ref._tap is not None:
                    cg.CGEventTapEnable(callback_ref._tap, True)
                return event

            # kCGKeyboardEventKeycode = 9
            if event_type == kCGEventKeyDown:
                keycode = cg.CGEventGetIntegerValueField(event, 9)
                if keycode == callback_ref._KEY_GRAVE:
                    flags = cg.CGEventGetFlags(event)
                    if flags & callback_ref._MODIFIER_MASK:
                        return event  # Shift+` (~), Cmd+`, etc. — let it type
                    callback_ref._fire_press()
                    return None  # swallow so the backtick isn't typed
            elif event_type == kCGEventKeyUp:
                keycode = cg.CGEventGetIntegerValueField(event, 9)
                if keycode == callback_ref._KEY_GRAVE and callback_ref._pressed:
                    callback_ref._fire_release()
                    return None  # swallow the release of the PTT key
            return event

        # CGEventGetIntegerValueField
        cg.CGEventGetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cg.CGEventGetIntegerValueField.restype = ctypes.c_int64

        # CGEventGetFlags
        cg.CGEventGetFlags.argtypes = [ctypes.c_void_p]
        cg.CGEventGetFlags.restype = ctypes.c_uint64

        # CGEventTapCreate
        cg.CGEventTapCreate.argtypes = [
            ctypes.c_uint32,  # tap
            ctypes.c_uint32,  # place
            ctypes.c_uint32,  # options
            ctypes.c_uint64,  # eventsOfInterest
            CGEventTapCallBack,  # callback
            ctypes.c_void_p,  # userInfo
        ]
        cg.CGEventTapCreate.restype = ctypes.c_void_p

        # Create an active event tap for keyDown/keyUp events. The tap is NOT
        # listen-only so the callback can swallow the backtick keystroke.
        event_mask = (
            (1 << kCGEventKeyDown)
            | (1 << kCGEventKeyUp)
        )
        tap = cg.CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            event_mask,
            _tap_callback,
            None,
        )
        if not tap:
            print(
                "Failed to create event tap. "
                "Grant Accessibility permission in System Settings > Privacy > Accessibility.",
                file=sys.stderr,
            )
            return
        self._tap = tap

        # Create a CFRunLoopSource from the tap and add it to a run loop
        cf.CFMachPortCreateRunLoopSource.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
        ]
        cf.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p

        source = cf.CFMachPortCreateRunLoopSource(None, tap, 0)

        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        run_loop = cf.CFRunLoopGetCurrent()
        self._run_loop_ref = run_loop

        # kCFRunLoopCommonModes
        cf.CFRunLoopAddSource.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        # Get kCFRunLoopCommonModes string constant
        cf.kCFRunLoopCommonModes = ctypes.c_void_p.in_dll(cf, "kCFRunLoopCommonModes")
        cf.CFRunLoopAddSource(run_loop, source, cf.kCFRunLoopCommonModes)

        # Enable the tap
        cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        cg.CGEventTapEnable(tap, True)

        # Run the loop (blocks until CFRunLoopStop)
        cf.CFRunLoopRun()

        # Cleanup
        self._run_loop_ref = None


# ---------------------------------------------------------------------------
# Linux/X11: python-xlib XGrabKey
# ---------------------------------------------------------------------------

class _X11Hotkey(GlobalHotkey):
    """Global push-to-talk hotkey on X11 using python-xlib XGrabKey.

    Grabs the bare backtick (`) key globally — held to record, released to
    paste.  While the app runs the backtick is consumed (it won't type), but
    Shift+` (~) and other modified combos still work.  Runs in a daemon thread.
    X11 emits a KeyRelease immediately followed by a KeyPress for auto-repeat
    while a key is held — those pairs are filtered so press/release fire once
    per hold.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ):
        super().__init__(on_press, on_release)
        self._thread: threading.Thread | None = None
        self._running = False
        self._pressed = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        try:
            from Xlib import X, XK, display as xdisplay
        except ImportError:
            print(
                "python-xlib not installed. Install it: pip install python-xlib",
                file=sys.stderr,
            )
            return

        disp = xdisplay.Display()
        root = disp.screen().root

        # Get keysym for the backtick/grave key and convert to keycode
        keysym = XK.string_to_keysym("grave")
        keycode = disp.keysym_to_keycode(keysym)

        # Bare key — no modifiers (so Shift+` = ~ is not grabbed)
        modifiers = 0

        # Grab the key — also grab with NumLock and CapsLock variations
        for extra_mod in (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask):
            root.grab_key(
                keycode, modifiers | extra_mod,
                True, X.GrabModeAsync, X.GrabModeAsync,
            )
        disp.flush()

        try:
            while self._running:
                if not disp.pending_events():
                    import time
                    time.sleep(0.02)
                    continue

                event = disp.next_event()

                if event.type == X.KeyPress and event.detail == keycode:
                    if not self._pressed:
                        self._pressed = True
                        try:
                            self._on_press()
                        except Exception:
                            pass
                elif event.type == X.KeyRelease and event.detail == keycode:
                    # Auto-repeat: a KeyRelease is paired with a KeyPress at the
                    # same time. If the very next queued event is that KeyPress,
                    # this is a repeat, not a real release.
                    if disp.pending_events():
                        nxt = disp.next_event()
                        if (nxt.type == X.KeyPress
                                and nxt.detail == keycode
                                and nxt.time == event.time):
                            continue  # swallow repeat pair, stay pressed
                        # Not a repeat — handle the release, then re-handle nxt
                        self._handle_release()
                        if nxt.type == X.KeyPress and nxt.detail == keycode:
                            if not self._pressed:
                                self._pressed = True
                                try:
                                    self._on_press()
                                except Exception:
                                    pass
                    else:
                        self._handle_release()
        finally:
            for extra_mod in (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask):
                root.ungrab_key(keycode, modifiers | extra_mod)
            disp.close()

    def _handle_release(self) -> None:
        if not self._pressed:
            return
        self._pressed = False
        try:
            self._on_release()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Linux/Wayland: SIGUSR1 / SIGUSR2 signal handlers
# ---------------------------------------------------------------------------

class _SignalHotkey(GlobalHotkey):
    """Push-to-talk via signals — user configures compositor to send them.

    SIGUSR1 starts recording (press), SIGUSR2 stops + pastes (release).
    Writes PID to /tmp/voxterm-dictation.pid so compositor keybinds can target
    this process.  Bind the same key's press/release to the two signals, e.g.::

        # press:   kill -USR1 $(cat /tmp/voxterm-dictation.pid)
        # release: kill -USR2 $(cat /tmp/voxterm-dictation.pid)
    """

    _PID_FILE = "/tmp/voxterm-dictation.pid"

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ):
        super().__init__(on_press, on_release)
        self._prev_usr1 = None
        self._prev_usr2 = None

    def start(self) -> None:
        # Write PID file
        try:
            with open(self._PID_FILE, "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass

        self._prev_usr1 = signal.getsignal(signal.SIGUSR1)
        self._prev_usr2 = signal.getsignal(signal.SIGUSR2)
        signal.signal(signal.SIGUSR1, self._handle_press)
        signal.signal(signal.SIGUSR2, self._handle_release)

        print(
            f"Wayland: no global hotkey protocol. "
            f"Configure your compositor to send SIGUSR1 on press and SIGUSR2 "
            f"on release:\n"
            f"  press:   kill -USR1 $(cat {self._PID_FILE})\n"
            f"  release: kill -USR2 $(cat {self._PID_FILE})",
            file=sys.stderr,
        )

    def stop(self) -> None:
        if self._prev_usr1 is not None:
            signal.signal(signal.SIGUSR1, self._prev_usr1)
        if self._prev_usr2 is not None:
            signal.signal(signal.SIGUSR2, self._prev_usr2)
        try:
            os.unlink(self._PID_FILE)
        except OSError:
            pass

    def _handle_press(self, signum: int, frame) -> None:
        try:
            self._on_press()
        except Exception:
            pass

    def _handle_release(self, signum: int, frame) -> None:
        try:
            self._on_release()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_hotkey(
    on_press: Callable[[], None],
    on_release: Callable[[], None] | None = None,
) -> GlobalHotkey:
    """Return the appropriate push-to-talk GlobalHotkey for the platform.

    ``on_press`` fires when the combo is held down, ``on_release`` when it is
    let go.
    """
    if CURRENT_PLATFORM == Platform.MACOS:
        return _MacOSHotkey(on_press, on_release)

    if CURRENT_PLATFORM == Platform.LINUX:
        from dictation.injector import _detect_display_server
        ds = _detect_display_server()
        if ds == "x11":
            return _X11Hotkey(on_press, on_release)
        # Wayland or unknown — use signal-based hotkey
        return _SignalHotkey(on_press, on_release)

    raise RuntimeError(f"Unsupported platform for global hotkey: {CURRENT_PLATFORM}")
