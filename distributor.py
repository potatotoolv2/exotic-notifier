"""Guide a server member onto their own PC without sharing this bot's token.

Their copy posts online hits and linked Discords. Recent logins stay off
until a channel is set on the host bot. The program files in the zip are
encrypted; run.py decrypts them in memory on their machine.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import aiohttp
import discord

# Only bits phonktester can put on the Scanner invite role. The invite link
# has to be a subset of that role or Discord rejects it.
INVITE_BITS = (
    "view_channel",
    "send_messages",
    "send_messages_in_threads",
    "embed_links",
    "attach_files",
    "read_message_history",
)
PORTAL_URL = "https://discord.com/developers/applications"


def loader_repo() -> str:
    env = os.getenv("EXOTIC_LOADER_REPO", "").strip().strip("/")
    if env:
        return env
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loader_repo.txt")
    if os.path.isfile(path):
        try:
            return open(path, encoding="utf-8").read().strip().strip("/")
        except Exception:
            return ""
    return ""
MAX_DISCORD_FILE = 9_500_000
INVITER_ROLE_NAME = "Scanner invite"
INVITER_WINDOW_SEC = 2 * 60 * 60

LICENSING_STUB = '''"""Paths for the packaged scanner."""
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
'''

RUN_PY = r'''"""Starts the scanner. Program files stay encrypted in payload.bin."""
from __future__ import annotations

import importlib.abc
import importlib.util
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
KEY = %%FERNET_KEY%%


def _python312() -> None:
    if sys.version_info < (3, 14) or os.environ.get("EXOTIC_PY_OK") == "1":
        return
    exe = ""
    try:
        if sys.platform == "win32":
            exe = subprocess.check_output(
                ["py", "-3.12", "-c", "import sys; print(sys.executable)"],
                text=True,
                timeout=30,
            ).strip()
        else:
            for name in ("python3.12", "python3"):
                try:
                    exe = subprocess.check_output(
                        [name, "-c", "import sys; print(sys.executable)"],
                        text=True,
                        timeout=30,
                    ).strip()
                except Exception:
                    exe = ""
                if exe:
                    break
    except Exception:
        exe = ""
    if not exe or not os.path.isfile(exe):
        print("This needs Python 3.12. Install it from https://www.python.org/downloads/ then run this again.")
        raise SystemExit(1)
    print("Restarting on Python 3.12.")
    os.environ["EXOTIC_PY_OK"] = "1"
    os.execv(exe, [exe, "-u", os.path.abspath(__file__)])


def _deps() -> None:
    if os.environ.get("BOT_CHILD") == "1":
        return
    print("Installing / updating Python packages...")
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"])
    if rc != 0:
        print("pip install failed.")
        raise SystemExit(rc)


def main() -> None:
    _python312()
    _deps()
    from cryptography.fernet import Fernet
    from dotenv import dotenv_values

    vals = dotenv_values(os.path.join(ROOT, ".env"))
    token = (vals.get("DISCORD_TOKEN") or "").strip()
    if token:
        os.environ["DISCORD_TOKEN"] = token
    os.environ["DATABASE_FILE"] = os.path.join(ROOT, "players.csv")
    os.environ["EXOTIC_SKIP_WIZARD"] = "1"
    role = (vals.get("NOTIFY_ROLE_NAME") or "").strip()
    if role:
        os.environ["NOTIFY_ROLE_NAME"] = role
    for key in ("EXOTIC_OWNER_ID", "EXOTIC_OWNER_NAME", "EXOTIC_CLAIM_WEBHOOK", "EXOTIC_RELAY_THREAD"):
        value = (vals.get(key) or "").strip()
        if value:
            os.environ[key] = value

    raw = Fernet(KEY).decrypt(open(os.path.join(ROOT, "payload.bin"), "rb").read())
    files = json.loads(raw.decode("utf-8"))
    sys.exotic_sources = files

    class Loader(importlib.abc.Loader):
        def __init__(self, src: str):
            self.src = src

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.__file__ = module.__name__ + ".py"
            exec(compile(self.src, module.__name__ + ".py", "exec"), module.__dict__)

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            src = files.get(fullname + ".py")
            if src is None or "." in fullname:
                return None
            return importlib.util.spec_from_loader(fullname, Loader(src))

    sys.meta_path.insert(0, Finder())
    ns = {
        "__name__": "__main__",
        "__file__": os.path.abspath(__file__),
        "__package__": None,
    }
    exec(compile(files["bot.py"], "bot.py", "exec"), ns)


if __name__ == "__main__":
    main()
'''

REQUIREMENTS = """discord.py>=2.4.0
aiohttp>=3.9.0
nodriver>=0.38
python-dotenv>=1.0.0
cryptography>=42.0.0
tzdata>=2024.1
"""

LOADER_PY = r'''"""Downloads the scanner, then starts it. The bot token stays in .env."""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
REPO = %%LOADER_REPO%%
FILES = (
    "bot.py",
    "keepalive.py",
    "user_setup.py",
    "distributor.py",
    "licensing.py",
    "requirements.txt",
)


def _python312() -> None:
    if sys.version_info < (3, 14) or os.environ.get("EXOTIC_PY_OK") == "1":
        return
    exe = ""
    try:
        if sys.platform == "win32":
            exe = subprocess.check_output(
                ["py", "-3.12", "-c", "import sys; print(sys.executable)"],
                text=True,
                timeout=30,
            ).strip()
        else:
            for name in ("python3.12", "python3"):
                try:
                    exe = subprocess.check_output(
                        [name, "-c", "import sys; print(sys.executable)"],
                        text=True,
                        timeout=30,
                    ).strip()
                except Exception:
                    exe = ""
                if exe:
                    break
    except Exception:
        exe = ""
    if not exe or not os.path.isfile(exe):
        print("This needs Python 3.12. Install it from https://www.python.org/downloads/ then run this again.")
        raise SystemExit(1)
    print("Restarting on Python 3.12.")
    os.environ["EXOTIC_PY_OK"] = "1"
    os.execv(exe, [exe, "-u", os.path.abspath(__file__)])


def _fetch() -> None:
    if not REPO or "/" not in REPO:
        print("This loader does not have a download location yet. Ask for a new /adduser.")
        raise SystemExit(1)
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "ExoticNotifier")]
    urllib.request.install_opener(opener)
    for name in FILES:
        url = f"https://raw.githubusercontent.com/{REPO}/main/{name}"
        print(f"Downloading {name}")
        try:
            urllib.request.urlretrieve(url, os.path.join(ROOT, name))
        except Exception as exc:
            print(f"Could not download {name}: {exc}")
            raise SystemExit(1)


def main() -> None:
    _python312()
    if not os.path.isfile(os.path.join(ROOT, ".env")):
        print("Missing .env. Unzip the Discord download and run start from inside that folder.")
        raise SystemExit(1)
    _fetch()
    print("Installing / updating Python packages...")
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"])
    if rc != 0:
        print("pip install failed.")
        raise SystemExit(rc)
    os.environ["EXOTIC_SKIP_WIZARD"] = "1"
    os.execv(sys.executable, [sys.executable, "-u", os.path.join(ROOT, "bot.py")])


if __name__ == "__main__":
    main()
'''

START_PS1 = r"""# Unzip, open the ExoticNotifier folder, then run this.
#   powershell -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 -u .\loader.py
    exit $LASTEXITCODE
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python -u .\loader.py
    exit $LASTEXITCODE
}
throw "Install Python 3.12 from https://www.python.org/downloads/ then run this again."
"""

START_MAC = r"""#!/bin/bash
cd "$(dirname "$0")" || exit 1
xattr -dr com.apple.quarantine "$(dirname "$0")" 2>/dev/null || true
echo "Exotic Notifier"
echo "Leave this window open. A small Chrome window will open and has to stay visible."
echo

PY=""
for c in \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12" \
    "/usr/local/bin/python3.12" \
    "/opt/homebrew/bin/python3.12"
do
    if [ -x "$c" ]; then
        PY="$c"
        break
    fi
done
if [ -z "$PY" ] && command -v python3.12 >/dev/null 2>&1; then
    PY="$(command -v python3.12)"
fi

if [ -z "$PY" ]; then
    echo "Python 3.12 is not installed. Downloading it once."
    echo "Mac will ask for your password. That only installs Python."
    PKG="/tmp/python-3.12.10-macos11.pkg"
    curl -fL --retry 3 -o "$PKG" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"
    if [ $? -ne 0 ]; then
        echo "Download failed. Install Python 3.12 from https://www.python.org/downloads/ then double-click this file again."
        read -r -p "Press Return to close."
        exit 1
    fi
    sudo installer -pkg "$PKG" -target /
    rm -f "$PKG"
    PY="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
fi

if [ ! -x "$PY" ]; then
    echo "Python 3.12 still is not available. Install it from https://www.python.org/downloads/ then double-click this file again."
    read -r -p "Press Return to close."
    exit 1
fi

if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
    echo "Install Google Chrome first: https://www.google.com/chrome/"
    echo "Then double-click this file again."
    read -r -p "Press Return to close."
    exit 1
fi

"$PY" -u loader.py
status=$?
if [ $status -ne 0 ]; then
    echo
    echo "The scanner stopped (code $status)."
    read -r -p "Press Return to close."
fi
exit $status
"""

START_BAT = r"""@echo off
cd /d "%~dp0"
title Exotic Notifier
where py >nul 2>&1 && (
    py -3.12 -u loader.py
    if not errorlevel 1 exit /b 0
)
where python >nul 2>&1 && (
    python -u loader.py
    if not errorlevel 1 exit /b 0
)
echo Install Python 3.12 from https://www.python.org/downloads/ then double-click this file again.
start https://www.python.org/downloads/release/python-31210/
pause
"""

START_TXT = """1. Add your bot with the invite link from the Discord DM.
2. Double-click one file and leave that window open:
   Windows: start.bat
   Mac: Start ExoticNotifier.command
   If Mac says it cannot be opened, right-click it, choose Open, then Open.
   That file is the loader. It downloads the scanner, then starts it.
   You need Python 3.12 from https://www.python.org/downloads/
3. Leave the one small Chrome window open. Do not start this twice.
4. In #mange-your-noti, click Open my notifier.
   Run /start and /stop in that private thread on your bot.

Do not share this folder. .env contains your bot token.
"""


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "cryptography>=42", "-q"]
        )
        from cryptography.fernet import Fernet
    return Fernet


def _read_source(name: str) -> str:
    mem = getattr(sys, "exotic_sources", None) or {}
    if name in mem:
        return mem[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _scrub_bot(src: str) -> str:
    src = re.sub(
        r'(os\.getenv\(\s*"DATABASE_FILE",\s*)r?"[^"]*"',
        r'\1r"players.csv"',
        src,
        count=1,
    )
    return src.replace("Invite phonktester", "Invite this bot")


def _same(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a.encode(), b.encode())
    except Exception:
        return False


def notifier_names(discord_name: str, user_id: int) -> list[str]:
    """Bot username: their Discord name plus notifer. Hyphen first, then a dot."""
    raw = re.sub(r"[^a-z0-9]", "", (discord_name or "").lower()) or "user"
    tail = str(user_id)[-4:]
    names = []
    for sep in ("-", "."):
        for extra in ("", tail):
            suffix = f"{extra}{sep}notifer"
            room = 32 - len(suffix)
            if room < 1:
                continue
            candidate = raw[:room] + suffix
            if len(candidate) >= 2 and candidate not in names:
                names.append(candidate)
    return names or ["user-notifer"]


async def rename_bot(token: str, discord_name: str, user_id: int) -> str:
    names = notifier_names(discord_name, user_id)
    try:
        async with aiohttp.ClientSession() as session:
            for name in names:
                async with session.patch(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {token}"},
                    json={"username": name},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        print(f"[adduser] renamed bot to {name}")
                        return name
                    if resp.status != 400:
                        print(f"[adduser] rename {resp.status}")
                        return ""
    except Exception as e:
        print(f"[adduser] rename failed: {type(e).__name__}")
    return ""


def build_package(
    *,
    token: str,
    csv_path: str,
    channel_id: int,
    owner_id: str = "",
    owner_name: str = "",
) -> bytes:
    """Zip bytes. The build folder, including their token, is deleted before return."""
    if not csv_path or not os.path.isfile(csv_path):
        raise FileNotFoundError(csv_path or "CSV")
    if any(c.isspace() for c in token) or '"' in token or "'" in token:
        raise ValueError("token")

    tmp = tempfile.mkdtemp(prefix="exotic_pkg_")
    try:
        folder = os.path.join(tmp, "ExoticNotifier")
        os.makedirs(folder)
        with open(os.path.join(folder, "loader.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(LOADER_PY.replace("%%LOADER_REPO%%", repr(loader_repo())))
        with open(os.path.join(folder, "requirements.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(REQUIREMENTS)
        with open(os.path.join(folder, "start.ps1"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(START_PS1)
        with open(os.path.join(folder, "start.sh"), "w", encoding="utf-8", newline="\n") as f:
            f.write(START_MAC)
        with open(os.path.join(folder, "Start ExoticNotifier.command"), "w", encoding="utf-8", newline="\n") as f:
            f.write(START_MAC)
        with open(os.path.join(folder, "start.bat"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(START_BAT)
        with open(os.path.join(folder, "Start ExoticNotifier.bat"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(START_BAT)
        with open(os.path.join(folder, "START.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(START_TXT)
        with open(os.path.join(folder, ".env"), "w", encoding="utf-8", newline="\n") as f:
            lines = [f"DISCORD_TOKEN={token}", "NOTIFY_ROLE_NAME=dev"]
            if str(owner_id).isdigit():
                lines.append(f"EXOTIC_OWNER_ID={owner_id}")
            safe_name = (owner_name or "").replace("\r", "").replace("\n", " ").strip()
            if safe_name:
                lines.append(f"EXOTIC_OWNER_NAME={safe_name}")
            relay_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relay.json")
            if os.path.isfile(relay_path):
                try:
                    relay = json.loads(open(relay_path, encoding="utf-8").read())
                except Exception:
                    relay = {}
                hook = str(relay.get("webhook_url") or "").strip()
                thread_id = str(relay.get("thread_id") or "").strip()
                if hook and "\n" not in hook and "\r" not in hook:
                    lines.append(f"EXOTIC_CLAIM_WEBHOOK={hook}")
                if thread_id.isdigit():
                    lines.append(f"EXOTIC_RELAY_THREAD={thread_id}")
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(folder, "config.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "notification_channel_id": channel_id,
                    "log_channel_id": None,
                    "info_channel_id": channel_id,
                    "noti_channel_id": None,
                },
                f,
            )
        shutil.copyfile(csv_path, os.path.join(folder, "players.csv"))

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for dirpath, _dirs, names in os.walk(folder):
                for name in names:
                    full = os.path.join(dirpath, name)
                    arc = os.path.relpath(full, tmp).replace("\\", "/")
                    info = zipfile.ZipInfo(arc)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 3
                    mode = 0o755 if name.endswith((".command", ".sh")) else 0o644
                    info.external_attr = (0o100000 | mode) << 16
                    with open(full, "rb") as handle:
                        zipped.writestr(info, handle.read())
        data = buf.getvalue()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if len(data) > MAX_DISCORD_FILE:
        raise RuntimeError(f"package is {len(data)} bytes")
    return data


def _invite_url(application_id: str, guild_id: int | None, permissions: int) -> str:
    url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={application_id}&permissions={int(permissions)}"
        "&scope=bot%20applications.commands"
    )
    if guild_id:
        url += f"&guild_id={guild_id}&disable_guild_select=true"
    return url


def bot_invite_bits(role: discord.Role) -> int:
    """Permissions the invite may ask for. Manage Server stays off the bot."""
    value = 0
    for name in INVITE_BITS:
        if getattr(role.permissions, name, False):
            value |= discord.Permissions(**{name: True}).value
    return value


async def _bot_identity(token: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {token}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception as e:
        print(f"[adduser] token check failed: {type(e).__name__}")
        return None
    if not data.get("bot") or not data.get("id"):
        return None
    return data


def _role_store_path() -> str:
    from licensing import data_dir

    return os.path.join(data_dir(), "inviter_roles.json")


def _saved_role_id(guild_id: int) -> int | None:
    path = _role_store_path()
    if not os.path.isfile(path):
        return None
    try:
        data = json.loads(open(path, "r", encoding="utf-8").read())
    except Exception:
        return None
    raw = (data.get("guilds") or {}).get(str(guild_id))
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _save_role_id(guild_id: int, role_id: int) -> None:
    path = _role_store_path()
    data = {"guilds": {}}
    if os.path.isfile(path):
        try:
            data = json.loads(open(path, "r", encoding="utf-8").read())
        except Exception:
            data = {"guilds": {}}
    data.setdefault("guilds", {})[str(guild_id)] = role_id
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _granted(client) -> set:
    granted = getattr(client, "_inviter_granted", None)
    if granted is None:
        granted = set()
        client._inviter_granted = granted
    return granted


def _can_invite_without_setup_role(member: discord.Member, setup_role_id: int | None) -> bool:
    if member.guild.owner_id == member.id:
        return True
    perms = discord.Permissions.none()
    for role in member.roles:
        if setup_role_id and role.id == setup_role_id:
            continue
        perms.value |= role.permissions.value
    return bool(perms.administrator or perms.manage_guild)


def invite_role_permissions(guild: discord.Guild) -> discord.Permissions:
    """Manage Server, plus every bot permission this bot is allowed to grant."""
    me = guild.me
    have = me.guild_permissions if me is not None else discord.Permissions.none()
    wanted = discord.Permissions.none()
    if have.manage_guild:
        wanted.manage_guild = True
    for name in INVITE_BITS:
        if getattr(have, name, False):
            setattr(wanted, name, True)
    return wanted


async def _setup_role(guild: discord.Guild) -> discord.Role:
    wanted = invite_role_permissions(guild)
    role = None
    saved = _saved_role_id(guild.id)
    if saved:
        role = guild.get_role(saved)
    if role is None:
        role = discord.utils.get(guild.roles, name=INVITER_ROLE_NAME)
    if role is None:
        role = await guild.create_role(
            name=INVITER_ROLE_NAME,
            permissions=wanted,
            hoist=False,
            mentionable=False,
            reason="Lets /adduser add a scanner bot",
        )
    elif role.permissions.value != wanted.value:
        role = await role.edit(permissions=wanted, reason="Lets /adduser add a scanner bot")
    _save_role_id(guild.id, role.id)
    return role


async def ensure_invite_role(guild: discord.Guild) -> None:
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        print("[adduser] Can't update Scanner invite. phonktester needs Manage Roles.")
        return
    try:
        await _setup_role(guild)
        print("[adduser] Scanner invite can add a bot.")
    except Exception as e:
        print(f"[adduser] role: {type(e).__name__}: {e}")


async def grant_inviter(client, member: discord.Member) -> tuple[bool, str]:
    """Give this member permission to add a bot. Returns (ok, note)."""
    guild = member.guild
    saved = _saved_role_id(guild.id)
    if _can_invite_without_setup_role(member, saved):
        return True, ""
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return False, (
            "phonktester needs **Manage Roles**, and its role has to sit above the other roles. "
            "Server Settings → Roles, then run `/adduser` again."
        )
    try:
        role = await _setup_role(guild)
        if role >= me.top_role:
            return False, (
                "Move phonktester's role above **Scanner invite** in Server Settings → Roles, "
                "then run `/adduser` again."
            )
        if role not in member.roles:
            await member.add_roles(role, reason="/adduser can add a scanner bot")
        member = await guild.fetch_member(member.id)
        if role not in member.roles:
            return False, (
                "phonktester could not give you **Scanner invite**. "
                "Move phonktester's role above **Scanner invite**, then paste the token again."
            )
    except discord.Forbidden:
        return False, (
            "phonktester can't grant Manage Server yet, so Discord will reject the bot invite. "
            "That permission has to be turned on for phonktester first. Paste the token again after that."
        )
    _granted(client).add(member.id)
    return True, (
        "You can add a bot to this server. Use the invite link in the DM, not the developer portal button. "
        "That permission turns off after your bot joins, or in 2 hours."
    )


async def _revoke_inviter(client, guild_id: int, user_id: int) -> None:
    if user_id not in _granted(client):
        return
    guild = client.get_guild(guild_id)
    if guild is None:
        return
    role_id = _saved_role_id(guild_id)
    role = guild.get_role(role_id) if role_id else None
    if role is None:
        _granted(client).discard(user_id)
        return
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
    except discord.NotFound:
        _granted(client).discard(user_id)
        return
    except Exception as e:
        print(f"[adduser] could not load member for role removal: {type(e).__name__}")
        return
    if role not in member.roles:
        _granted(client).discard(user_id)
        return
    try:
        await member.remove_roles(role, reason="Scanner bot invite finished")
        print(f"[adduser] removed invite permission from {member}")
    except Exception as e:
        print(f"[adduser] role remove failed: {type(e).__name__}: {e}")
        return
    _granted(client).discard(user_id)


async def _member_in_guild(client, guild_id: int, user_id: str | None) -> bool:
    if not user_id or not str(user_id).isdigit():
        return False
    guild = client.get_guild(guild_id)
    if guild is None:
        return False
    try:
        await guild.fetch_member(int(user_id))
        return True
    except discord.NotFound:
        return False
    except Exception as e:
        print(f"[adduser] invite check: {type(e).__name__}")
        return False


async def _watch_inviter(client, guild_id: int, user_id: int, app_id: str | None) -> None:
    """Take the invite role away only after the bot actually joins from outside."""
    try:
        present = await _member_in_guild(client, guild_id, app_id)
        for _ in range(INVITER_WINDOW_SEC // 15):
            await asyncio.sleep(15)
            if not app_id:
                continue
            here = await _member_in_guild(client, guild_id, app_id)
            if here and not present:
                break
            present = here
        await _revoke_inviter(client, guild_id, user_id)
    except asyncio.CancelledError:
        return


def schedule_inviter_watch(client, guild_id: int, user_id: int, app_id: str | None) -> None:
    tasks = getattr(client, "_inviter_tasks", None)
    if tasks is None:
        tasks = {}
        client._inviter_tasks = tasks
    old = tasks.get(user_id)
    if old and not old.done():
        old.cancel()
    tasks[user_id] = asyncio.create_task(_watch_inviter(client, guild_id, user_id, app_id))


class TokenModal(discord.ui.Modal, title="Paste your bot token"):
    token_in = discord.ui.TextInput(
        label="Bot token",
        placeholder="Bot page → Reset Token → copy",
        style=discord.TextStyle.short,
        min_length=30,
        max_length=200,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if getattr(interaction.client, "access_revoked", lambda _id: False)(interaction.user.id):
            await interaction.response.send_message("Your access is revoked.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        token = str(self.token_in.value).strip().strip('"').strip("'")
        host = (os.getenv("DISCORD_TOKEN") or "").strip()
        if host and _same(token, host):
            await interaction.followup.send(
                "That token is the bot already running in this server. "
                "Create a **new** application, reset its token, and paste that. "
                "Two computers cannot stay online with one token.",
                ephemeral=True,
            )
            return
        ident = await _bot_identity(token)
        if not ident:
            await interaction.followup.send(
                "Discord rejected that token. On the Bot page, click **Reset Token** and paste the new one.",
                ephemeral=True,
            )
            return

        role_note = ""
        if isinstance(interaction.user, discord.Member) and interaction.guild_id:
            stop = getattr(interaction.client, "force_stop_scanner", None)
            if callable(stop):
                await stop(interaction.user.id, str(ident["id"]), token)
            ok, note = await grant_inviter(interaction.client, interaction.user)
            if ok and note:
                role_note = note
                schedule_inviter_watch(
                    interaction.client,
                    interaction.guild_id,
                    interaction.user.id,
                    str(ident["id"]),
                )
            elif not ok:
                role_note = note or "I couldn't give you Manage Server."
                await interaction.followup.send(role_note, ephemeral=True)
                return

        client = interaction.client
        channel_id = getattr(getattr(client, "cfg", None), "notification_channel_id", None)
        if not channel_id:
            await interaction.followup.send(
                "Set the notifier channel first. In that channel, run `/setchannel`, then `/adduser` again.",
                ephemeral=True,
            )
            return
        csv_path = ""
        getter = getattr(client, "database_path", None)
        if callable(getter):
            csv_path = getter() or ""
        csv_path = csv_path or os.environ.get("DATABASE_FILE") or ""
        if not csv_path or not os.path.isfile(csv_path):
            await interaction.followup.send(
                "The player list is not on this PC. Set it with `/setcsv`, then run `/adduser` again.",
                ephemeral=True,
            )
            return

        owner_name = interaction.user.name
        renamed = await rename_bot(token, owner_name, interaction.user.id)
        name = renamed or ident.get("username") or "your bot"
        claim_id = None
        if interaction.guild is not None:
            claim_ch = discord.utils.get(interaction.guild.text_channels, name="claim")
            if claim_ch is not None:
                claim_id = claim_ch.id
        dest_channel = int(claim_id or channel_id)
        print(f"[adduser] package for {interaction.user} → bot {name} channel {dest_channel}")
        remember = getattr(interaction.client, "remember_operator", None)
        if callable(remember):
            remember(interaction.user.id, owner_name, str(ident["id"]), name)
        try:
            data = build_package(
                token=token,
                csv_path=csv_path,
                channel_id=dest_channel,
                owner_id=str(interaction.user.id),
                owner_name=owner_name,
            )
        except Exception as e:
            print(f"[adduser] build failed: {type(e).__name__}: {e}")
            await interaction.followup.send(
                "Couldn't build the download. Run `/adduser` again.",
                ephemeral=True,
            )
            return

        permissions = discord.Permissions(view_channel=True, send_messages=True).value
        if interaction.guild is not None:
            invite_role = discord.utils.get(interaction.guild.roles, name=INVITER_ROLE_NAME)
            if invite_role is not None:
                permissions = bot_invite_bits(invite_role) or permissions
        url = _invite_url(str(ident["id"]), interaction.guild_id, permissions)
        invite_note = "1. Add it to this server. You have Manage Server for this invite:"
        if role_note:
            invite_note = "1. " + role_note
        text = (
            f"**{name}** is ready. Do these steps in order.\n\n"
            f"**1. Add the bot**\n{invite_note} {url}\n\n"
            "**2. Run the loader**\n"
            "Unzip `ExoticNotifier.zip`. Double-click **one** file and leave that window open:\n"
            "Windows: `start.bat`\n"
            "Mac: `Start ExoticNotifier.command`\n"
            "If Mac says it cannot be opened, right-click the file, choose Open, then Open.\n"
            "The loader downloads the scanner, installs Python packages, and starts your bot. "
            "You need Python 3.12 from https://www.python.org/downloads/\n"
            "One small Chrome window opens. Leave that one window open. Don't start the file twice.\n\n"
            "**3. Open your private thread**\n"
            f"In **#mange-your-noti**, click **Open my notifier**. That thread is only for you and **{name}**.\n"
            f"Run `/start` and `/stop` there on **{name}**. Not on phonktester.\n\n"
            "Online players are posted in the Claims thread by phonktester. "
            "The **Claim · 100** button spends 100 of your scans and DMs you the player.\n"
            "**Remove from scan** in that DM takes the player off your list.\n"
            "Recent logins stay off until `/setnotichannel` is run on phonktester.\n\n"
            "Don't share the zip. `.env` contains your bot token."
        )
        package = discord.File(io.BytesIO(data), filename="ExoticNotifier.zip")
        try:
            await interaction.user.send(text, file=package)
            await interaction.followup.send(
                f"Check your DMs. Invite **{name}** with that link. After it joins, open your thread and run `/start` on **{name}**.",
                ephemeral=True,
            )
        except discord.Forbidden:
            package = discord.File(io.BytesIO(data), filename="ExoticNotifier.zip")
            await interaction.followup.send(
                "DMs are closed, so the download is here instead.\n\n" + text,
                file=package,
                ephemeral=True,
            )
        except Exception as e:
            print(f"[adduser] send failed: {type(e).__name__}: {e}")
            await interaction.followup.send(
                "I built the download but couldn't send it. Open your DMs and run `/adduser` again.",
                ephemeral=True,
            )


class AddUserView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Paste token",
        style=discord.ButtonStyle.primary,
        custom_id="exotic:adduser-token",
    )
    async def paste_token(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TokenModal())


def register_adduser(bot, database_path) -> None:
    bot.database_path = database_path

    @bot.tree.command(
        name="adduser",
        description="Get the scanner on your PC. Pings stay in the notifier channel.",
    )
    async def cmd_adduser(interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Use `/adduser` in the server.", ephemeral=True)
            return
        if getattr(interaction.client, "access_revoked", lambda _id: False)(interaction.user.id):
            await interaction.response.send_message("Your access is revoked.", ephemeral=True)
            return
        channel_id = getattr(getattr(interaction.client, "cfg", None), "notification_channel_id", None)
        if not channel_id:
            await interaction.response.send_message(
                "Set the notifier channel first. In that channel, run `/setchannel`, then `/adduser` again.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        perm_line = "You can add your bot from the invite link in the next step."
        if isinstance(interaction.user, discord.Member):
            ok, note = await grant_inviter(interaction.client, interaction.user)
            if ok and note:
                perm_line = note
                schedule_inviter_watch(
                    interaction.client, interaction.guild.id, interaction.user.id, None
                )
            elif not ok and note:
                perm_line = note
        where = f"<#{channel_id}>"
        await interaction.followup.send(
            "This puts the scanner on **your** computer. You use your own bot token. "
            f"Pings still go to {where}.\n\n"
            f"**1. Create a bot and copy the token**\n{PORTAL_URL}\n"
            "New Application → **Bot** → **Reset Token** → copy it.\n\n"
            "**2. Paste the token**\n"
            "Click **Paste token** below. You get a DM with a small loader zip and an invite link.\n\n"
            "**3. Add your bot**\n"
            "Open the invite link in that DM and add the bot to this server.\n\n"
            "**4. Start the loader**\n"
            "Unzip the download. Double-click `start.bat` on Windows, or `Start ExoticNotifier.command` on Mac. "
            "Leave that window open. It downloads the scanner and starts your bot. "
            "One small Chrome window opens. Do not close it and do not start the loader twice.\n"
            "You need Python 3.12 from https://www.python.org/downloads/\n\n"
            "**5. Open your thread**\n"
            "After your bot is in the server, click **Open my notifier** in **#mange-your-noti**.\n"
            "In that private thread, run `/start` on **your** bot.\n\n"
            f"{perm_line}\n"
            "Each person runs their own loader, so the same player can be pinged once per person.",
            view=AddUserView(),
            ephemeral=True,
        )
