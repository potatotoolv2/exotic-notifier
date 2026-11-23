"""Keep the scanner alive in the background on Windows and macOS."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import ctypes
from typing import Optional

_sleep_prevented = False
_caffeinate_proc: Optional[subprocess.Popen] = None
_mac_assertion = None
_mac_iokit = None


def is_headless() -> bool:
    val = os.environ.get("EXOTIC_HEADLESS", "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    # Headed Chrome is more reliable against Cloudflare; flags below
    # stop Chrome from freezing when you tab away.
    return False


def chrome_args() -> list[str]:
    args = [
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
        "--disable-ipc-flooding-protection",
        "--disable-hang-monitor",
        "--disable-component-update",
        "--metrics-recording-only",
        "--hide-crash-restore-bubble",
        "--window-size=520,360",
        "--window-position=40,40",
    ]
    # Do not minimize. Windows suspends a minimized Chrome even with the
    # throttling flags above, which stops scans while you use other apps.
    return args


def prefer_high_priority() -> None:
    try:
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetCurrentProcess()
            kernel32.SetPriorityClass(handle, 0x00008000)  # ABOVE_NORMAL
            # Clear Windows 11 EcoQoS / efficiency mode if the API exists.
            try:
                ProcessPowerThrottling = 4
                class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
                    _fields_ = [
                        ("Version", ctypes.c_ulong),
                        ("ControlMask", ctypes.c_ulong),
                        ("StateMask", ctypes.c_ulong),
                    ]
                state = PROCESS_POWER_THROTTLING_STATE()
                state.Version = 1
                state.ControlMask = 0x1  # EXECUTION_SPEED
                state.StateMask = 0
                kernel32.SetProcessInformation(
                    handle,
                    ProcessPowerThrottling,
                    ctypes.byref(state),
                    ctypes.sizeof(state),
                )
            except Exception:
                pass
        else:
            os.nice(-5)
    except Exception:
        pass


def _mac_assertion_acquire() -> None:
    global _mac_assertion, _mac_iokit
    try:
        iokit = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/IOKit.framework/IOKit"
        )
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
        ]
        kCFStringEncodingUTF8 = 0x08000100
        reason = cf.CFStringCreateWithCString(
            None, b"ExoticNotifier scanner", kCFStringEncodingUTF8
        )
        assertion_type = cf.CFStringCreateWithCString(
            None, b"NoIdleSleepAssertion", kCFStringEncodingUTF8
        )
        aid = ctypes.c_uint32(0)
        iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int
        kIOPMAssertionLevelOn = 255
        err = iokit.IOPMAssertionCreateWithName(
            assertion_type, kIOPMAssertionLevelOn, reason, ctypes.byref(aid)
        )
        if err == 0:
            _mac_assertion = aid.value
            _mac_iokit = iokit
    except Exception:
        _mac_assertion = None
        _mac_iokit = None


def prevent_sleep(*, keep_display: Optional[bool] = None) -> None:
    """Stop the OS from sleeping the machine (and display if Chrome is visible)."""
    global _sleep_prevented, _caffeinate_proc
    if keep_display is None:
        keep_display = not is_headless()
    prefer_high_priority()
    if sys.platform == "win32":
        try:
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            ES_AWAYMODE_REQUIRED = 0x00000040
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            if keep_display:
                flags |= ES_DISPLAY_REQUIRED
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            _sleep_prevented = True
            print("[Power] Windows sleep prevention on "
                  f"(display={'held' if keep_display else 'free'}).")
        except Exception as e:
            print(f"[Power] Failed to prevent sleep: {e}")
        return

    if sys.platform == "darwin":
        if _caffeinate_proc and _caffeinate_proc.poll() is None:
            _sleep_prevented = True
            return
        args = ["caffeinate", "-i", "-s", "-w", str(os.getpid())]
        if keep_display:
            args.insert(1, "-d")
        try:
            _caffeinate_proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _sleep_prevented = True
            print("[Power] macOS caffeinate on — scanner keeps running in background.")
        except FileNotFoundError:
            _mac_assertion_acquire()
            _sleep_prevented = _mac_assertion is not None
            if _sleep_prevented:
                print("[Power] macOS sleep assertion on.")
            else:
                print("[Power] Could not start caffeinate; Mac may sleep.")
        except Exception as e:
            print(f"[Power] Failed to prevent sleep: {e}")
        return

    # Linux: systemd-inhibit is optional; best-effort.
    _sleep_prevented = True


def refresh_sleep_prevention() -> None:
    """Windows execution state can drop; re-assert periodically."""
    if sys.platform == "win32" and _sleep_prevented:
        prevent_sleep(keep_display=not is_headless())
    elif sys.platform == "darwin" and _sleep_prevented:
        if _caffeinate_proc and _caffeinate_proc.poll() is not None:
            prevent_sleep(keep_display=not is_headless())


def allow_sleep() -> None:
    global _sleep_prevented, _caffeinate_proc, _mac_assertion, _mac_iokit
    if sys.platform == "win32" and _sleep_prevented:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass
        print("[Power] Windows sleep prevention released.")
    if _caffeinate_proc is not None:
        try:
            _caffeinate_proc.terminate()
        except Exception:
            pass
        _caffeinate_proc = None
    if _mac_iokit is not None and _mac_assertion is not None:
        try:
            _mac_iokit.IOPMAssertionRelease(ctypes.c_uint32(_mac_assertion))
        except Exception:
            pass
        _mac_assertion = None
        _mac_iokit = None
    _sleep_prevented = False


def install_signal_cleanup() -> None:
    def _handler(signum, _frame):
        allow_sleep()
        raise SystemExit(0)

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass


def configure_event_loop() -> None:
    # Windows must keep the Proactor loop. The selector loop cannot start Chrome.
    return


def pid_path() -> str:
    from licensing import data_dir
    return os.path.join(data_dir(), "bot.pid")


def write_pid() -> None:
    try:
        with open(pid_path(), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def clear_pid() -> None:
    try:
        path = pid_path()
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def stop_pid_file() -> bool:
    path = pid_path()
    if not os.path.isfile(path):
        return False
    try:
        pid = int(open(path, encoding="utf-8").read().strip())
    except Exception:
        return False
    if pid == os.getpid():
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
