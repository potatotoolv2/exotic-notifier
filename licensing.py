"""Paths for the packaged scanner."""
import os
import sys


def data_dir() -> str:
    override = os.environ.get("EXOTIC_DATA_DIR", "").strip()
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    if sys.platform == "darwin":
        root = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ExoticNotifier")
    elif sys.platform == "win32":
        root = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ExoticNotifier")
    else:
        root = os.path.join(
            os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
            "ExoticNotifier",
        )
    os.makedirs(root, exist_ok=True)
    return root


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
