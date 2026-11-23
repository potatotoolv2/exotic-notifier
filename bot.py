# bot.py — sk1er-only exotic notifier (0.5 req/s, Windows-compatible)
import os
import sys
import subprocess


def _restart_on_supported_python() -> None:
    """nodriver's generated files are not valid UTF-8, so Python 3.14 cannot import it."""
    if sys.version_info < (3, 14) or os.environ.get("EXOTIC_PY_OK") == "1":
        return
    try:
        exe = subprocess.check_output(
            ["py", "-3.12", "-c", "import sys; print(sys.executable)"],
            text=True,
            timeout=30,
        ).strip()
    except Exception:
        exe = ""
    if not exe or not os.path.isfile(exe):
        print("This needs Python 3.12. Install it from python.org, then run: py -3.12 bot.py")
        raise SystemExit(1)
    print(f"[Runtime] Python {sys.version.split()[0]} cannot load the browser. Restarting on 3.12.")
    os.environ["EXOTIC_PY_OK"] = "1"
    os.execv(exe, [exe, "-u", *sys.argv])


_restart_on_supported_python()

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import secrets
import aiohttp
import nodriver as nd
import re
import json
import os
import sys
import csv
import html as html_lib
import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv

from keepalive import (
    allow_sleep,
    chrome_args,
    clear_pid,
    configure_event_loop,
    install_signal_cleanup,
    is_headless,
    prevent_sleep,
    refresh_sleep_prevention,
    write_pid,
)
from licensing import (
    data_dir,
    is_frozen,
)
from user_setup import load_config, run_wizard, save_config
from distributor import AddUserView, ensure_invite_role, register_adduser

load_dotenv()
configure_event_loop()


# ─────────────────────────── Config ───────────────────────────────────

BOT_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DATABASE_FILE = os.getenv(
    "DATABASE_FILE",
    r"players.csv",
)


def apply_runtime_config(*, wizard: bool) -> None:
    """GUI/launcher writes token + CSV. Frozen EXE stores runtime files in AppData."""
    global BOT_TOKEN, DATABASE_FILE
    if is_frozen():
        os.chdir(data_dir())
    skip = (
        os.environ.get("EXOTIC_SKIP_WIZARD") == "1"
        or os.environ.get("BOT_CHILD") == "1"
        or not wizard
    )
    cfg = load_config() if skip else run_wizard(force="--setup" in sys.argv)
    token = (os.getenv("DISCORD_TOKEN") or cfg.get("discord_token") or "").strip()
    csv_path = (os.getenv("DATABASE_FILE") or cfg.get("database_file") or "").strip()
    if token:
        BOT_TOKEN = token
        os.environ["DISCORD_TOKEN"] = token
    if csv_path:
        DATABASE_FILE = csv_path
        os.environ["DATABASE_FILE"] = csv_path


BLACKLIST_FILE = "blacklist.txt"
CONFIG_FILE = "config.json"
NOTIFIED_FILE = "notified.json"
SETTINGS_FILE = "settings.json"
INFO_POSTED_FILE = "info_posted.json"
RECENT_POSTED_FILE = "recent_posted.json"
RECENT_QUEUE_FILE = "recent_queue.json"
RECENT_LOGIN_DAYS = 30

NOTIFY_ROLE_NAME = os.getenv("NOTIFY_ROLE_NAME", "dev").strip() or "dev"
SUPER_FILTER_FILE = "super_filtered.csv"

REQUESTS_PER_SEC = 0.5  # 1 request every 2 seconds, per IP

# Official Mojang → Microsoft Java cutoff: Sep 19, 2023 11:00 ET (15:00 UTC).
# 12/28/23 is after that; anyone who logged in after Sep 19 is already migrated.
MIGRATION_DATE = datetime(2023, 9, 19, 15, 0, tzinfo=timezone.utc)
ALLOWED_GUILD = "poopy notifier"
NUKE_EXIT = 86
ADMIN_ID = 1495477760477495317
HOST_BOT_ID = 1574126363340837046
ADMIN_NAME = "foidslays55"
REVOKED_MARKER = "revoked:"
CLAIM_COST = 100


def _pacific():
    try:
        return ZoneInfo("America/Los_Angeles")
    except Exception:
        return timezone(timedelta(hours=-8))


LOCAL_TZ = _pacific()
APP_VERSION = "0.0.4"
CHANGELOG = [
    {
        "version": "0.0.1",
        "title": "Notifier is open",
        "notes": [
            "Each person runs their own bot. `/start`, `/stop`, and `/status` stay in a private thread under #mange-your-noti.",
            "Everyone scans the same player list. Online hits go to #claim and ping @claim.",
            "The scan leaderboard in #claim updates every 5 minutes.",
            "#info shows all-time, monthly, and daily scans, plus who is scanning right now.",
            "New versions post in #updates on their own.",
        ],
    },
    {
        "version": "0.0.2",
        "title": "Admin panel",
        "notes": [
            "The admin account gets a private panel to see who has a scanner and revoke or restore them.",
            "Revoked people lose @claim, their bot can no longer post hits, and a new download is blocked.",
            "Full claims are sent to the admin in DMs. The public Claim button stays for everyone else.",
            "The admin changes the shared player list with `/setcsv`. New downloads get that file.",
        ],
    },
    {
        "version": "0.0.3",
        "title": "Your bot only",
        "notes": [
            "`/start`, `/stop`, and `/status` run on your own bot, inside your private thread.",
            "The thread in #mange-your-noti opens after your bot has joined the server.",
            "Scanner invite includes the permissions Discord asks for when you add that bot.",
            "The scan leaderboard no longer uses the bar.",
        ],
    },
    {
        "version": "0.0.4",
        "title": "Claims cost scans",
        "notes": [
            "@claim is only for people whose scanner bot is in the server.",
            "Each claim costs 100 scans. The leaderboard shows the scans you have left.",
        ],
    },
]


def guild_allowed(name: str) -> bool:
    return (name or "").strip().casefold() == ALLOWED_GUILD


def is_admin(user) -> bool:
    if user is None:
        return False
    try:
        if int(user.id) == ADMIN_ID:
            return True
    except (TypeError, ValueError):
        pass
    return (getattr(user, "name", "") or "").lower() == ADMIN_NAME


def load_operators() -> dict:
    empty = {"users": {}, "admin_thread_id": None, "revoked_message_id": None}
    if not os.path.isfile("operators.json"):
        return empty
    try:
        raw = json.loads(open("operators.json", encoding="utf-8").read())
    except Exception:
        return empty
    if not isinstance(raw, dict):
        return empty
    if not isinstance(raw.get("users"), dict):
        raw["users"] = {}
    return raw


def save_operators(data: dict) -> None:
    with open("operators.json", "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_relay() -> dict:
    if not os.path.isfile("relay.json"):
        return {}
    try:
        raw = json.loads(open("relay.json", encoding="utf-8").read())
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_relay(data: dict) -> None:
    with open("relay.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "webhook_url": str(data.get("webhook_url") or ""),
                "thread_id": data.get("thread_id"),
            },
            f,
        )


def operator_revoked(user_id: int) -> bool:
    try:
        if int(user_id) == ADMIN_ID:
            return False
    except (TypeError, ValueError):
        return False
    row = (load_operators().get("users") or {}).get(str(user_id)) or {}
    return bool(row.get("revoked"))


def parse_leaderboard(content: str) -> dict:
    if "```lb" not in (content or ""):
        return {}
    body = content.split("```lb", 1)[1].split("```", 1)[0]
    scores = {}
    for line in body.splitlines():
        parts = line.strip().split(":", 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        scores[parts[0]] = {"scans": int(parts[1]), "name": parts[2].strip() or parts[0]}
    return scores


def merge_scores(existing: dict, local_id: str, local_name: str, local_scans: int) -> dict:
    scores = {k: {"scans": int(v["scans"]), "name": v["name"]} for k, v in existing.items()}
    for uid, row in list(scores.items()):
        if not str(uid).isdigit():
            scores.pop(uid, None)
    if str(local_id).isdigit() and local_scans > 0:
        prev = scores.get(str(local_id), {"scans": 0, "name": local_name})
        scores[str(local_id)] = {
            "scans": max(int(prev["scans"]), int(local_scans)),
            "name": local_name or prev["name"],
        }
    return scores


def leaderboard_embed(scores: dict) -> discord.Embed:
    """Top-10 card: medal, name, and the count."""
    rows = sorted(
        scores.items(),
        key=lambda kv: (-int(kv[1]["scans"]), str(kv[1]["name"]).lower()),
    )[:10]
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, row) in enumerate(rows):
        scans = int(row["scans"])
        rank = medals[i] if i < 3 else f"`{i + 1:>2}`"
        who = f"<@{uid}>" if str(uid).isdigit() else str(row["name"])
        lines.append(f"{rank}  {who}  ·  **{scans:,}**")
    embed = discord.Embed(
        title="🏆  Scan leaderboard",
        description="\n".join(lines) if lines else "No scans yet. Add your bot, then run `/start` on it.",
        color=0xF1C40F,
    )
    embed.set_footer(
        text=f"Updates every 5 minutes  ·  a claim costs {CLAIM_COST} scans"
    )
    return embed


def load_bank() -> dict:
    base = {"spent": {}, "acks": [], "message_id": None}
    if not os.path.isfile("bank.json"):
        return base
    try:
        raw = json.loads(open("bank.json", encoding="utf-8").read())
    except Exception:
        return base
    if not isinstance(raw, dict):
        return base
    spent = raw.get("spent") if isinstance(raw.get("spent"), dict) else {}
    acks = raw.get("acks") if isinstance(raw.get("acks"), list) else []
    return {
        "spent": {str(k): int(v or 0) for k, v in spent.items() if str(k).isdigit()},
        "acks": [str(item) for item in acks][-40:],
        "message_id": raw.get("message_id"),
    }


def save_bank(data: dict) -> None:
    with open("bank.json", "w", encoding="utf-8") as f:
        json.dump(data, f)


def remaining_scores(scores: dict) -> dict:
    spent = load_bank().get("spent") or {}
    out = {}
    for uid, row in scores.items():
        left = int(row["scans"]) - int(spent.get(str(uid)) or 0)
        if left > 0:
            out[str(uid)] = {"scans": left, "name": row["name"]}
    return out


def take_scans(user_id: int, name: str, reported_gross: int) -> tuple[bool, int]:
    scores = load_score_file()
    uid = str(user_id)
    prev = scores.get(uid) or {"scans": 0, "name": name or uid}
    gross = max(int(prev.get("scans") or 0), int(reported_gross or 0))
    bank = load_bank()
    spent = int((bank.get("spent") or {}).get(uid) or 0)
    left = gross - spent
    if left < CLAIM_COST:
        return False, max(left, 0)
    scores[uid] = {"scans": gross, "name": name or prev.get("name") or uid}
    bank["spent"][uid] = spent + CLAIM_COST
    save_score_file(scores)
    save_bank(bank)
    return True, left - CLAIM_COST


def parse_bank(content: str) -> tuple[dict, dict]:
    balances, acks = {}, {}
    if not (content or "").startswith("bank"):
        return balances, acks
    for line in content.splitlines()[1:]:
        if line.startswith("ack:"):
            parts = line.split(":", 2)
            if len(parts) == 3:
                acks[parts[1]] = parts[2]
            continue
        uid, _, left = line.partition(":")
        if uid.isdigit() and left.isdigit():
            balances[uid] = int(left)
    return balances, acks


def load_score_file() -> dict:
    path = "leaderboard.json"
    if not os.path.isfile(path):
        return {}
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return {}
    scores = {}
    for uid, row in raw.items():
        if str(uid).isdigit() and isinstance(row, dict):
            scores[str(uid)] = {
                "scans": int(row.get("scans") or 0),
                "name": str(row.get("name") or uid),
            }
    return scores


def save_score_file(scores: dict) -> None:
    with open("leaderboard.json", "w", encoding="utf-8") as f:
        json.dump(scores, f)


def load_activity_file() -> dict:
    path = "activity.json"
    if not os.path.isfile(path):
        return {}
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return {}
    users = raw.get("users") if isinstance(raw, dict) else None
    if not isinstance(users, dict):
        return {}
    clean = {}
    for uid, row in users.items():
        if str(uid).isdigit() and isinstance(row, dict):
            ip = str(row.get("ip") or "")
            if not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", ip):
                ip = ""
            clean[str(uid)] = {
                "name": str(row.get("name") or uid),
                "all": int(row.get("all") or 0),
                "day": int(row.get("day") or 0),
                "day_key": str(row.get("day_key") or ""),
                "month": int(row.get("month") or 0),
                "month_key": str(row.get("month_key") or ""),
                "scanning": bool(row.get("scanning")),
                "seen": float(row.get("seen") or 0),
                "session": int(row.get("session") or 0),
                "hits": int(row.get("hits") or 0),
                "ip": ip,
            }
    return clean


def save_activity_file(users: dict) -> None:
    with open("activity.json", "w", encoding="utf-8") as f:
        json.dump({"users": users}, f)


def parse_pulse(content: str):
    if not (content or "").startswith("pulse:"):
        return None
    parts = content.split(":", 8)
    if len(parts) != 9:
        return None
    _tag, uid, scanning, day, month, total, day_key, month_key, name = parts
    if not (uid.isdigit() and day.isdigit() and month.isdigit() and total.isdigit()):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_key):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}", month_key):
        return None
    return {
        "id": uid,
        "name": name.strip() or uid,
        "scanning": scanning == "1",
        "day": int(day),
        "month": int(month),
        "all": int(total),
        "day_key": day_key,
        "month_key": month_key,
    }


def merge_activity(users: dict, row: dict, seen: float) -> None:
    uid = row["id"]
    prev = users.get(uid, {})
    day = int(row["day"])
    month = int(row["month"])
    if prev.get("day_key") == row["day_key"]:
        day = max(day, int(prev.get("day") or 0))
    if prev.get("month_key") == row["month_key"]:
        month = max(month, int(prev.get("month") or 0))
    users[uid] = {
        "name": row["name"] or prev.get("name") or uid,
        "all": max(int(row["all"]), int(prev.get("all") or 0)),
        "day": day,
        "day_key": row["day_key"],
        "month": month,
        "month_key": row["month_key"],
        "scanning": bool(row["scanning"]),
        "seen": seen,
        "session": int(prev.get("session") or 0),
        "hits": int(prev.get("hits") or 0),
        "ip": str(prev.get("ip") or ""),
    }


def parse_stat(text: str):
    parts = (text or "").split(":")
    if len(parts) < 4 or parts[0] != "stat" or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    scanning = True
    session = None
    hits = None
    ip = ""
    if (
        len(parts) >= 8
        and parts[3] in {"0", "1"}
        and parts[4].isdigit()
        and parts[5].isdigit()
        and re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", parts[6])
    ):
        scanning = parts[3] == "1"
        session = int(parts[4])
        hits = int(parts[5])
        ip = parts[6]
        name = ":".join(parts[7:]).strip()
    elif len(parts) >= 5 and parts[3] in {"0", "1"}:
        scanning = parts[3] == "1"
        name = ":".join(parts[4:]).strip()
    else:
        name = ":".join(parts[3:]).strip()
    return {
        "id": parts[1],
        "scans": int(parts[2]),
        "scanning": scanning,
        "session": session,
        "hits": hits,
        "ip": ip,
        "name": name,
    }


def activity_embed(users: dict) -> discord.Embed:
    now = datetime.now(LOCAL_TZ)
    day_key = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")
    all_time = month_total = day_total = 0
    active = []
    for uid, row in users.items():
        all_time += int(row.get("all") or 0)
        if row.get("month_key") == month_key:
            month_total += int(row.get("month") or 0)
        if row.get("day_key") == day_key:
            day_total += int(row.get("day") or 0)
        if row.get("scanning") and float(row.get("seen") or 0) >= time.time() - 180:
            active.append((uid, row.get("name") or uid))
    active.sort(key=lambda item: str(item[1]).lower())
    if not active:
        who = "Nobody right now."
    else:
        shown = active[:15]
        who = "\n".join(f"<@{uid}>" for uid, _name in shown)
        extra = len(active) - len(shown)
        if extra:
            who += f"\n+{extra} more"
    embed = discord.Embed(title="Scan activity", color=0x5865F2)
    embed.add_field(name="All time", value=f"**{all_time:,}**", inline=True)
    embed.add_field(name="This month", value=f"**{month_total:,}**", inline=True)
    embed.add_field(name="Today", value=f"**{day_total:,}**", inline=True)
    embed.add_field(name=f"Scanning now  ·  {len(active)}", value=who, inline=False)
    embed.set_footer(text="Updates every minute  ·  Pacific time")
    return embed


def changelog_embed(entry: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"{entry['version']}  ·  {entry['title']}",
        description="\n".join(f"• {note}" for note in entry["notes"]),
        color=0x57F287,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Exotic Notifier")
    return embed


def schedule_code_delete() -> None:
    """Remove this copy after it exits. Only the app folder, never a drive or home directory."""
    root = os.path.abspath(os.path.dirname(__file__))
    parent = os.path.abspath(os.path.dirname(root))
    home = os.path.abspath(os.path.expanduser("~"))
    if root in {parent, home} or len(root) < 12:
        return
    if not any(
        os.path.exists(os.path.join(root, name))
        for name in ("bot.py", "run.py", "payload.bin", "keepalive.py")
    ):
        return
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200 | 0x08000000
        subprocess.Popen(
            f'ping 127.0.0.1 -n 4 >nul & rmdir /s /q "{root}"',
            shell=True,
            creationflags=flags,
            close_fds=True,
        )
        return
    subprocess.Popen(
        ["sh", "-c", f"sleep 3; rm -rf {root!r}"],
        start_new_session=True,
    )


def dashed_uuid(uuid: str) -> str:
    u = uuid.replace("-", "").lower()
    if len(u) != 32:
        return uuid
    return f"{u[0:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:32]}"


def pretty_item_id(item_id: str) -> str:
    return item_id.replace("_", " ").title()


def namemc_url(uuid: str) -> str:
    return f"https://namemc.com/profile/{dashed_uuid(uuid)}"


def exotico_url(ign: str) -> str:
    return f"https://exotico.flori.tv/?player={quote(ign, safe='')}"


def color_from_items(items: list) -> discord.Color:
    for item in items:
        h = (getattr(item, "hex_code", "") or "").lstrip("#")
        if len(h) == 6:
            try:
                return discord.Color(int(h, 16))
            except ValueError:
                pass
    return discord.Color.from_rgb(46, 204, 113)


def add_profile_link_buttons(view: discord.ui.View, uuid: str, ign: str,
                             row: int = 0):
    view.add_item(discord.ui.Button(
        label="NameMC",
        style=discord.ButtonStyle.link,
        url=namemc_url(uuid),
        row=row,
    ))
    if ign:
        view.add_item(discord.ui.Button(
            label="Exotico",
            style=discord.ButtonStyle.link,
            url=exotico_url(ign),
            row=row,
        ))


# ─────────────────────────── Data classes ─────────────────────────────

class ExoticItem:
    def __init__(self, item_uuid, hex_code, item_id, rarity, reforge):
        self.item_uuid = item_uuid
        self.hex_code = hex_code
        self.item_id = item_id
        self.rarity = rarity
        self.reforge = reforge


class PlayerExoticData:
    def __init__(self, player_uuid: str, player_name: str):
        self.player_uuid = player_uuid
        self.player_name = player_name
        self.items: list[ExoticItem] = []

    def add_item(self, item: ExoticItem):
        self.items.append(item)

    @property
    def total_item_count(self) -> int:
        return len(self.items)


class RateLimiter:
    """Global 0.5 req/s gate for the single outbound IP."""

    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self.min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


sk1er_limiter = RateLimiter(REQUESTS_PER_SEC)


# ─────────────────────────── Name Resolver ────────────────────────────

_name_cache: dict[str, tuple[str, float]] = {}
_NAME_CACHE_TTL = 3600
_mojang_session: Optional[aiohttp.ClientSession] = None


async def resolve_current_name(uuid: str) -> Optional[str]:
    global _mojang_session
    clean = uuid.replace("-", "").lower()
    now = time.time()
    if clean in _name_cache:
        n, t = _name_cache[clean]
        if now - t < _NAME_CACHE_TTL:
            return n
    if _mojang_session is None or _mojang_session.closed:
        _mojang_session = aiohttp.ClientSession()
    try:
        async with _mojang_session.get(
            f"https://sessionserver.mojang.com/session/minecraft/profile/{clean}",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                name = data.get("name")
                if name:
                    _name_cache[clean] = (name, now)
                    return name
            elif resp.status == 429 and clean in _name_cache:
                return _name_cache[clean][0]
            return None
    except Exception:
        return _name_cache[clean][0] if clean in _name_cache else None


async def close_mojang_session():
    global _mojang_session
    if _mojang_session and not _mojang_session.closed:
        await _mojang_session.close()


# ─────────────────────────── Parser ───────────────────────────────────

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


_EMPTY_STATS = {
    "", "-", "none", "n/a", "not set", "unlinked", "null", "unknown", "no",
}


def _clean_stat(val: str) -> Optional[str]:
    val = html_lib.unescape(val or "")
    val = re.sub(r"<[^>]+>", "", val)
    val = re.sub(r"\s+", " ", val).strip()
    if not val or val.lower() in _EMPTY_STATS:
        return None
    return val


def parse_labeled_stat(html: str, label: str) -> Optional[str]:
    m = re.search(
        rf'{re.escape(label)}:</span>\s*<span[^>]*class="stats-value"[^>]*>\s*(.*?)\s*</span>',
        html, re.IGNORECASE | re.DOTALL
    )
    if not m:
        m = re.search(
            rf'{re.escape(label)}\s*:\s*</(?:span|div|td)>\s*<(?:span|div|td)[^>]*>\s*(.*?)\s*</',
            html, re.IGNORECASE | re.DOTALL
        )
    if not m:
        return None
    return _clean_stat(m.group(1))


def parse_last_login(html: str) -> Optional[datetime]:
    raw = parse_labeled_stat(html, "Latest Login")
    if not raw:
        m = re.search(
            r'Latest Login:</span>\s*<span[^>]*class="stats-value"[^>]*>\s*([^<]+?)\s*</span>',
            html, re.IGNORECASE
        )
        raw = m.group(1).strip() if m else None
    if not raw:
        return None
    date_m = re.search(
        r'(\w{3})\s+(\d{1,2}),\s+(\d{4})\s+(\d{1,2}):(\d{2})\s+(AM|PM)',
        raw, re.IGNORECASE
    )
    if not date_m:
        date_m = re.search(
            r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})',
            raw,
        )
        if date_m:
            try:
                a, b, year = int(date_m.group(1)), int(date_m.group(2)), int(date_m.group(3))
                month, day = a, b
                if month > 12:
                    day, month = a, b
                return datetime(year, month, day, tzinfo=timezone.utc)
            except (ValueError, OverflowError):
                return None
        return None
    try:
        month = _MONTHS.get(date_m.group(1).lower())
        if not month:
            return None
        day, year = int(date_m.group(2)), int(date_m.group(3))
        hour, mins = int(date_m.group(4)), int(date_m.group(5))
        ampm = date_m.group(6).upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        return datetime(year, month, day, hour, mins, tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None


def _format_age(delta) -> str:
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def parse_online_raw(html: str) -> Optional[bool]:
    m = re.search(
        r'class="stats-value"[^>]*>\s*<span[^>]*style="[^"]*color:\s*#([0-9A-Fa-f]{6})[^"]*"[^>]*>\s*(Online|Offline)\s*</span>',
        html, re.IGNORECASE
    )
    if not m:
        status = parse_labeled_stat(html, "Online Status")
        if status:
            low = status.lower()
            if "online" in low and "offline" not in low:
                return True
            if "offline" in low:
                return False
        return None
    color = m.group(1).upper()
    text = m.group(2).strip().lower()

    if text == "online" and color == "3CE63C":
        return True
    if text == "offline" and color == "FF5555":
        return False
    if text == "online":
        return int(color[2:4], 16) > int(color[0:2], 16)
    if text == "offline":
        return False
    return None


_SOCIAL_HREFS = {
    "youtube": r'https?://(?:www\.)?youtube\.com/[^"\'\s<>]+',
    "twitter": r'https?://(?:www\.)?(?:twitter|x)\.com/[^"\'\s<>]+',
    "twitch": r'https?://(?:www\.)?twitch\.tv/[^"\'\s<>]+',
    "instagram": r'https?://(?:www\.)?instagram\.com/[^"\'\s<>]+',
    "tiktok": r'https?://(?:www\.)?tiktok\.com/[^"\'\s<>]+',
    "hypixel": r'https?://(?:www\.)?hypixel\.net/[^"\'\s<>]+',
}


def parse_socials(html: str) -> dict[str, str]:
    socials: dict[str, str] = {}
    for label, key in (
        ("Discord", "discord"),
        ("YouTube", "youtube"),
        ("Youtube", "youtube"),
        ("Twitter", "twitter"),
        ("Twitch", "twitch"),
        ("Instagram", "instagram"),
        ("TikTok", "tiktok"),
        ("Tiktok", "tiktok"),
        ("Hypixel", "hypixel"),
        ("Forums", "hypixel"),
    ):
        val = parse_labeled_stat(html, label)
        if val and key not in socials:
            socials[key] = val

    for key, pat in _SOCIAL_HREFS.items():
        if key in socials:
            continue
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            socials[key] = m.group(0).rstrip(").,;")

    if "discord" not in socials:
        m = re.search(
            r'fa-discord[^<]*</i>\s*([^<]{2,40})',
            html, re.IGNORECASE
        )
        if m:
            cleaned = _clean_stat(m.group(1))
            if cleaned:
                socials["discord"] = cleaned
        else:
            m = re.search(
                r'href="(https?://(?:discord\.gg|discord\.com/invite)/[^"]+)"',
                html, re.IGNORECASE
            )
            if m and "discord.gg/sk1er" not in m.group(1).lower():
                socials["discord"] = m.group(1)

    return socials


@dataclass
class Sk1erProfile:
    online: bool
    last_login: Optional[datetime]
    socials: dict[str, str] = field(default_factory=dict)

    @property
    def discord(self) -> Optional[str]:
        return self.socials.get("discord")

    @property
    def migrated(self) -> bool:
        return (
            self.last_login is not None
            and self.last_login >= MIGRATION_DATE
        )

    @property
    def super_filtered(self) -> bool:
        return bool(self.discord) and self.migrated


def parse_sk1er_profile(html: str) -> Optional[Sk1erProfile]:
    raw_online = parse_online_raw(html)
    last_login = parse_last_login(html)
    socials = parse_socials(html)
    if raw_online is None and last_login is None and not socials:
        return None

    confirmed_online = False
    if raw_online is True:
        if last_login is None:
            print("  [Parser] Online but no last login — not treating as online")
        else:
            age = datetime.now(timezone.utc) - last_login
            if age.total_seconds() > 86400:
                print(f"  [Parser] Stale — last login {_format_age(age)} ago")
            else:
                print(f"  [Parser] Confirmed — last login {_format_age(age)} ago")
                confirmed_online = True

    return Sk1erProfile(
        online=confirmed_online,
        last_login=last_login,
        socials=socials,
    )


def build_sk1er_url(uuid: str) -> str:
    return f"https://sk1er.club/stats/{uuid}"


# ─────────────────────────── Safe evaluate ────────────────────────────

async def safe_evaluate(tab, expression: str) -> Optional[str]:
    try:
        result = await asyncio.wait_for(tab.evaluate(expression), timeout=8)
        if result is None:
            return None
        if isinstance(result, str):
            return result
        if hasattr(result, "value"):
            return str(result.value)
        s = str(result)
        if "ExceptionDetails" in s or "exception" in s.lower():
            return None
        return s
    except Exception:
        return None


# ─────────────────────────── Database ─────────────────────────────────

def parse_database(filepath: str) -> dict[str, PlayerExoticData]:
    players: dict[str, PlayerExoticData] = {}
    if not os.path.exists(filepath):
        print(f"[DB] {filepath} not found.")
        return players

    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        lines = f.readlines()

    for line_num, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("playeruuid"):
            continue

        try:
            parts = line.split(",")
            if len(parts) < 3:
                continue

            player_uuid = parts[0].strip().lower()
            item_id = parts[1].strip()
            hex_code = parts[2].strip().lstrip("#")

            if not player_uuid or not item_id:
                continue
            if len(player_uuid) != 32:
                continue

            if player_uuid not in players:
                players[player_uuid] = PlayerExoticData(player_uuid, "")

            players[player_uuid].add_item(ExoticItem(
                item_uuid="",
                hex_code=hex_code,
                item_id=item_id,
                rarity="",
                reforge="",
            ))
        except Exception as e:
            print(f"[DB] Line {line_num}: {e}")

    total_items = sum(len(p.items) for p in players.values())
    print(f"[DB] {len(players)} players, {total_items} exotic items.")
    return players


# ─────────────────────────── Support classes ──────────────────────────

class BlacklistManager:
    def __init__(self):
        self.blacklisted: set[str] = set()
        self.load()

    def add(self, uuid: str):
        self.blacklisted.add(uuid.replace("-", "").lower())
        self.save()

    def remove(self, uuid: str) -> bool:
        clean = uuid.replace("-", "").lower()
        if clean in self.blacklisted:
            self.blacklisted.discard(clean)
            self.save()
            return True
        return False

    def is_blacklisted(self, uuid: str) -> bool:
        return uuid.replace("-", "").lower() in self.blacklisted

    def save(self):
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            for uid in sorted(self.blacklisted):
                f.write(uid + "\n")

    def load(self):
        if os.path.exists(BLACKLIST_FILE):
            with open(BLACKLIST_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.blacklisted.add(line.lower())
        print(f"[Blacklist] {len(self.blacklisted)} entries.")


class ConfigManager:
    def __init__(self):
        self.notification_channel_id: Optional[int] = None
        self.log_channel_id: Optional[int] = None
        self.info_channel_id: Optional[int] = None
        self.noti_channel_id: Optional[int] = None
        self.manage_channel_id: Optional[int] = None
        self.leaderboard_message_id: Optional[int] = None
        self.stats_message_id: Optional[int] = None
        self.load()

    def save(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "notification_channel_id": self.notification_channel_id,
                "log_channel_id": self.log_channel_id,
                "info_channel_id": self.info_channel_id,
                "noti_channel_id": self.noti_channel_id,
                "manage_channel_id": self.manage_channel_id,
                "leaderboard_message_id": self.leaderboard_message_id,
                "stats_message_id": self.stats_message_id,
            }, f)

    def load(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                d = json.load(f)
            self.notification_channel_id = d.get("notification_channel_id")
            self.log_channel_id = d.get("log_channel_id")
            self.info_channel_id = d.get("info_channel_id")
            self.noti_channel_id = d.get("noti_channel_id")
            self.manage_channel_id = d.get("manage_channel_id")
            self.leaderboard_message_id = d.get("leaderboard_message_id")
            self.stats_message_id = d.get("stats_message_id")


class NotifiedTracker:
    def __init__(self):
        self.notified: dict[str, float] = {}
        self.load()

    def is_notified(self, uuid: str) -> bool:
        return (time.time() -
                self.notified.get(uuid.replace("-", "").lower(), 0)) < 1800

    def mark_notified(self, uuid: str):
        self.notified[uuid.replace("-", "").lower()] = time.time()
        self.save()

    def save(self):
        with open(NOTIFIED_FILE, "w", encoding="utf-8") as f:
            json.dump(self.notified, f)

    def load(self):
        if os.path.exists(NOTIFIED_FILE):
            try:
                with open(NOTIFIED_FILE, encoding="utf-8") as f:
                    self.notified = json.load(f)
            except Exception:
                self.notified = {}


class InfoPostedTracker:
    """Post each Discord-linked player to the info channel once (or if Discord changes)."""

    def __init__(self):
        self.posted: dict[str, str] = {}
        self.load()

    def should_post(self, uuid: str, discord_tag: str) -> bool:
        uid = uuid.replace("-", "").lower()
        prev = self.posted.get(uid)
        return prev is None or prev != discord_tag

    def mark(self, uuid: str, discord_tag: str):
        self.posted[uuid.replace("-", "").lower()] = discord_tag
        self.save()

    def save(self):
        with open(INFO_POSTED_FILE, "w", encoding="utf-8") as f:
            json.dump(self.posted, f)

    def load(self):
        if os.path.exists(INFO_POSTED_FILE):
            try:
                with open(INFO_POSTED_FILE, encoding="utf-8") as f:
                    self.posted = json.load(f)
            except Exception:
                self.posted = {}


class RecentLoginTracker:
    """Post each recent login once per login timestamp, not every scan cycle."""

    def __init__(self):
        self.posted: dict[str, str] = {}
        self.load()

    def _stamp(self, last_login: datetime) -> str:
        return last_login.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")

    def should_post(self, uuid: str, last_login: datetime) -> bool:
        uid = uuid.replace("-", "").lower()
        return self.posted.get(uid) != self._stamp(last_login)

    def mark(self, uuid: str, last_login: datetime):
        self.posted[uuid.replace("-", "").lower()] = self._stamp(last_login)
        self.save()

    def save(self):
        with open(RECENT_POSTED_FILE, "w", encoding="utf-8") as f:
            json.dump(self.posted, f)

    def load(self):
        if os.path.exists(RECENT_POSTED_FILE):
            try:
                with open(RECENT_POSTED_FILE, encoding="utf-8") as f:
                    self.posted = json.load(f)
            except Exception:
                self.posted = {}


def logged_in_within_month(profile: Sk1erProfile) -> bool:
    if not profile.last_login:
        return False
    age = datetime.now(timezone.utc) - profile.last_login
    return 0 <= age.total_seconds() <= RECENT_LOGIN_DAYS * 86400


class SuperFilterStore:
    """Players with Hypixel Discord linked AND last login after MSA migration."""

    FIELDS = [
        "player_uuid", "ign", "discord", "last_login", "migrated",
        "youtube", "twitter", "twitch", "instagram", "tiktok", "hypixel",
        "item_count", "added_at",
    ]

    def __init__(self, path: str = SUPER_FILTER_FILE):
        self.path = path
        self.rows: dict[str, dict] = {}
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            print("[SuperFilter] 0 entries.")
            return
        try:
            with open(self.path, encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    uid = (row.get("player_uuid") or "").replace("-", "").lower()
                    if uid:
                        self.rows[uid] = row
        except Exception as e:
            print(f"[SuperFilter] Load error: {e}")
        print(f"[SuperFilter] {len(self.rows)} entries.")

    def save(self):
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writeheader()
            for uid in sorted(self.rows):
                writer.writerow(self.rows[uid])

    def add(self, exotic: "PlayerExoticData", profile: Sk1erProfile) -> bool:
        uid = exotic.player_uuid.replace("-", "").lower()
        is_new = uid not in self.rows
        login_iso = (
            profile.last_login.isoformat() if profile.last_login else ""
        )
        row = {
            "player_uuid": uid,
            "ign": exotic.player_name or self.rows.get(uid, {}).get("ign", ""),
            "discord": profile.discord or "",
            "last_login": login_iso,
            "migrated": "yes" if profile.migrated else "no",
            "youtube": profile.socials.get("youtube", ""),
            "twitter": profile.socials.get("twitter", ""),
            "twitch": profile.socials.get("twitch", ""),
            "instagram": profile.socials.get("instagram", ""),
            "tiktok": profile.socials.get("tiktok", ""),
            "hypixel": profile.socials.get("hypixel", ""),
            "item_count": str(len(exotic.items)),
            "added_at": self.rows.get(uid, {}).get(
                "added_at", datetime.now(timezone.utc).isoformat()
            ),
        }
        self.rows[uid] = row
        self.save()
        return is_new


class ScanSettings:
    def __init__(self):
        # One tab only — extra tabs would share the same IP and break 0.5/s.
        self.num_tabs: int = 1
        self.load()

    def save(self):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"num_tabs": 1}, f, indent=2)

    def load(self):
        self.num_tabs = 1

    def cycle_estimate(self, total: int) -> str:
        secs = total / REQUESTS_PER_SEC if REQUESTS_PER_SEC else 0
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        return f"~{h}h {m}m"


# ─────────────────────────── Tab Worker (browser) ─────────────────────

class TabWorker:
    def __init__(self, worker_id: int, tab, queue: asyncio.Queue,
                 result_cb, stats: dict, browser=None, skip_cb=None):
        self.worker_id = worker_id
        self.tab = tab
        self.browser = browser
        self.queue = queue
        self.result_cb = result_cb
        self.stats = stats
        self.skip_cb = skip_cb
        self.running = True

    async def _mark_page_active(self):
        await safe_evaluate(
            self.tab,
            "try{Object.defineProperty(document,'hidden',{get:()=>false});"
            "Object.defineProperty(document,'visibilityState',{get:()=>'visible'});"
            "document.dispatchEvent(new Event('visibilitychange'));true}catch(e){false}",
        )
        try:
            from nodriver import cdp
            await asyncio.wait_for(
                self.tab.send(cdp.page.set_web_lifecycle_state(state="active")),
                timeout=3,
            )
        except Exception:
            pass

    async def _ensure_tab(self) -> bool:
        href = await safe_evaluate(self.tab, "window.location.href") if self.tab else None
        if href is not None:
            return True
        if not self.browser:
            return False
        if time.time() < getattr(self, "_tab_backoff", 0):
            return False
        try:
            self.tab = await asyncio.wait_for(
                self.browser.get("about:blank"), timeout=30)
            print(f"[Tab {self.worker_id}] Recreated browser tab.")
            return True
        except Exception as e:
            self._tab_backoff = time.time() + 30
            print(f"[Tab {self.worker_id}] Tab recreate failed: {e}")
            return False

    async def _wait_for_real_page(self, uuid: str,
                                   max_wait: float = 20.0
                                   ) -> tuple[Optional[str], Optional[str]]:
        start = time.time()
        while (time.time() - start) < max_wait:
            await asyncio.sleep(0.5)
            title = await safe_evaluate(self.tab, "document.title")
            if title and "just a moment" in title.lower():
                continue
            await self._mark_page_active()
            html = await safe_evaluate(
                self.tab, "document.documentElement.outerHTML")
            if html is None:
                continue
            if "stats-value" not in html:
                continue
            if "challenges.cloudflare.com" in html:
                continue
            return title, html
        return None, None

    async def run(self):
        print(f"[Tab {self.worker_id}] Started (0.5 req/s, background-safe).")
        while self.running:
            got = False
            name = "?"
            try:
                uuid, name, index, total = await asyncio.wait_for(
                    self.queue.get(), timeout=5.0)
                got = True
            except asyncio.TimeoutError:
                refresh_sleep_prevention()
                continue
            except asyncio.CancelledError:
                break

            try:
                if self.skip_cb and self.skip_cb(uuid):
                    print(f"[Tab {self.worker_id}] Skip blacklisted {name or uuid[:8]}")
                    continue
                if not await self._ensure_tab():
                    self.stats["errors"] += 1
                    continue
                await sk1er_limiter.acquire()
                await asyncio.wait_for(
                    self.tab.get(build_sk1er_url(uuid)), timeout=35)
                await self._mark_page_active()
                title, html = await self._wait_for_real_page(uuid)

                if html is None:
                    self.stats["errors"] += 1
                    if (self.stats["errors"] <= 10 or
                            self.stats["errors"] % 50 == 0):
                        print(f"[Tab {self.worker_id}] Page failed: "
                              f"{name} (err #{self.stats['errors']})")
                    continue

                profile = parse_sk1er_profile(html)
                if profile is None:
                    self.stats["parse_fails"] += 1
                    if self.stats["parse_fails"] <= 5:
                        print(f"[Tab {self.worker_id}] Parse fail: {name}")
                    continue

                self.stats["browser_requests"] += 1
                await asyncio.wait_for(
                    self.result_cb(uuid, name, profile, index, total),
                    timeout=45,
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.stats["errors"] += 1
                if "ExceptionDetails" not in str(e):
                    print(f"[Tab {self.worker_id}] Error ({name}): "
                          f"{type(e).__name__}: {e}")
            finally:
                if got:
                    try:
                        self.queue.task_done()
                    except ValueError:
                        pass
        print(f"[Tab {self.worker_id}] Stopped.")


# ─────────────────────────── Discord Views ────────────────────────────

def player_actions_view(player_uuid: str, player_name: str = "", *, removed: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    add_profile_link_buttons(view, player_uuid, player_name, row=0)
    view.add_item(discord.ui.Button(
        label="Removed from scan" if removed else "Remove from scan",
        style=discord.ButtonStyle.danger,
        custom_id="exotic:remove-scan",
        disabled=removed,
        row=1,
    ))
    return view


class ClaimAccessView(discord.ui.View):
    """Button in #claim. Gives @claim so the member can see online alerts."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Unlock claims",
        style=discord.ButtonStyle.success,
        custom_id="exotic:claim-role",
    )
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild
        if operator_revoked(getattr(member, "id", 0)):
            await interaction.response.send_message("Your access is revoked.", ephemeral=True)
            return
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Use this in the server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not await interaction.client.member_has_verified_bot(member):
            await interaction.followup.send(
                "Finish setup first. Your scanner bot has to be in this server. "
                "Run `/adduser`, open the invite link in the DM, then unlock claims.",
                ephemeral=True,
            )
            return
        role = discord.utils.find(lambda r: r.name.lower() == "claim", guild.roles)
        if role is None:
            await interaction.followup.send("The @claim role is not set up yet.", ephemeral=True)
            return
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="Unlocked claims")
            except discord.Forbidden:
                await interaction.followup.send(
                    "I can't give you @claim. Move phonktester's role above @claim.",
                    ephemeral=True,
                )
                return
        thread = None
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            for existing in channel.threads:
                if existing.name == "Claims":
                    thread = existing
                    break
            if thread is None:
                try:
                    async for archived in channel.archived_threads(private=True, joined=True, limit=20):
                        if archived.name == "Claims":
                            thread = archived
                            break
                except Exception:
                    thread = None
        if thread is not None:
            try:
                if thread.archived:
                    await thread.edit(archived=False)
                await thread.add_user(member)
            except Exception as e:
                print(f"[claim] add user: {type(e).__name__}: {e}")
        await interaction.followup.send(
            "You have @claim. Online alerts ping that role, and you can see them now.",
            ephemeral=True,
        )


class AdminActionView(discord.ui.View):
    def __init__(self, user_id: int, name: str):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.user_name = name
        revoked = operator_revoked(self.user_id)
        revoke = discord.ui.Button(
            label="Restore access" if revoked else "Revoke access",
            style=discord.ButtonStyle.success if revoked else discord.ButtonStyle.danger,
        )
        revoke.callback = self.act
        reset = discord.ui.Button(label="Reset account", style=discord.ButtonStyle.secondary)
        reset.callback = self.reset
        refresh = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.primary)
        refresh.callback = self.refresh
        self.add_item(revoke)
        self.add_item(reset)
        self.add_item(refresh)

    async def act(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        if self.user_id == ADMIN_ID:
            await interaction.response.send_message("The admin account stays.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        client = interaction.client
        if operator_revoked(self.user_id):
            note = await client.restore_access(self.user_id, self.user_name)
        else:
            note = await client.revoke_access(self.user_id, self.user_name)
        await client._refresh_admin_panel()
        await client._upsert_person_card(self.user_id, create=True)
        await interaction.edit_original_response(
            content=note,
            embed=client._person_embed(self.user_id),
            view=AdminActionView(self.user_id, self.user_name),
        )

    async def reset(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        if self.user_id == ADMIN_ID:
            await interaction.response.send_message("The admin account stays.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        client = interaction.client
        note = await client.reset_operator(self.user_id, self.user_name)
        await interaction.edit_original_response(content=note, embed=None, view=None)

    async def refresh(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        client = interaction.client
        await client._upsert_person_card(self.user_id, create=True)
        await interaction.response.edit_message(
            embed=client._person_embed(self.user_id),
            view=AdminActionView(self.user_id, self.user_name),
        )


class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Choose a member",
        min_values=1,
        max_values=1,
        custom_id="exotic:admin-pick",
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        member = select.values[0]
        if int(member.id) == ADMIN_ID:
            await interaction.response.send_message("That's the admin account.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.client._upsert_person_card(member.id, create=True)
        await interaction.followup.send(
            embed=interaction.client._person_embed(member.id),
            view=AdminActionView(member.id, member.name),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Refresh",
        style=discord.ButtonStyle.secondary,
        custom_id="exotic:admin-refresh",
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        client = interaction.client
        await interaction.response.edit_message(
            content=client._admin_text(),
            embed=client._admin_embed(),
            view=AdminPanelView(),
        )


class ManageOpenView(discord.ui.View):
    """Opens a private thread so /start, /stop, and /status stay hidden."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open my notifier",
        style=discord.ButtonStyle.primary,
        custom_id="exotic:manage-open",
    )
    async def open_thread(self, interaction: discord.Interaction, button: discord.ui.Button):
        if operator_revoked(interaction.user.id):
            await interaction.response.send_message("Your access is revoked.", ephemeral=True)
            return
        client = interaction.client
        manage = await client._manage_channel()
        if manage is None or interaction.channel_id != manage.id:
            await interaction.response.send_message(
                "Use this button in #mange-your-noti.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in the server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not await client.member_has_verified_bot(interaction.user):
            await interaction.followup.send(
                "This Discord account doesn't have a scanner bot in the server. "
                "Open the thread from the account that ran `/adduser`.",
                ephemeral=True,
            )
            return
        try:
            thread = await client._user_manage_thread(interaction.user, manage)
        except Exception as e:
            print(f"[manage] thread: {type(e).__name__}: {e}")
            await interaction.followup.send(
                "I couldn't open a private thread. phonktester needs permission to create private threads.",
                ephemeral=True,
            )
            return
        role_name = client._owner_role_name(interaction.user)
        await interaction.followup.send(
            f"Your role is **{role_name}**. Your notifier is {thread.mention}. "
            "Only that role can see the thread, and your bot is in it. Run `/start` there on **your** bot.",
            ephemeral=True,
        )


def message_uuid(message: discord.Message | None) -> str:
    if message is None:
        return ""
    blobs = [message.content or ""]
    for embed in message.embeds:
        if embed.footer and embed.footer.text:
            blobs.append(embed.footer.text)
        for field in embed.fields:
            blobs.append(field.name or "")
            blobs.append(field.value or "")
    for blob in blobs:
        for match in re.findall(r"[0-9a-fA-F-]{32,36}", blob):
            clean = match.replace("-", "").lower()
            if len(clean) == 32 and all(c in "0123456789abcdef" for c in clean):
                return clean
    return ""


class ScanRemoveView(discord.ui.View):
    """Persistent DM button. Works after a restart."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Remove from scan",
        style=discord.ButtonStyle.danger,
        custom_id="exotic:remove-scan",
    )
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        uuid = ""
        uuid = message_uuid(interaction.message)
        if len(uuid) != 32:
            await interaction.response.send_message(
                "Couldn't read that player from the message.")
            return
        client = interaction.client
        ign = uuid[:8]
        if hasattr(client, "db"):
            player = client.db.get(uuid)
            if player and player.player_name:
                ign = player.player_name
        if hasattr(client, "remove_from_scan"):
            client.remove_from_scan(uuid)
        elif hasattr(client, "bl"):
            client.bl.add(uuid)
        in_dm = interaction.guild is None
        try:
            await interaction.response.edit_message(
                view=player_actions_view(uuid, ign, removed=True))
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"**{ign}** removed from the scan list.")
            return
        await interaction.followup.send(
            f"**{ign}** is off the scan list and will not be checked again.",
            ephemeral=not in_dm,
        )


class ClaimBuyView(discord.ui.View):
    """Persistent claim button. phonktester handles it, so it still works after a restart."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim · 100",
        style=discord.ButtonStyle.success,
        custom_id="exotic:claim-buy",
    )
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member) or interaction.guild is None:
            await interaction.followup.send("Use this in the server.", ephemeral=True)
            return
        uuid = message_uuid(interaction.message)
        client = interaction.client
        exotic = client.db.get(uuid) if uuid and hasattr(client, "db") else None
        if exotic is None:
            await interaction.followup.send("That claim is no longer available.", ephemeral=True)
            return
        if not await client.member_has_verified_bot(member):
            await interaction.followup.send(
                "Your scanner bot has to be in the server before you can claim.",
                ephemeral=True,
            )
            return
        role = discord.utils.find(lambda r: r.name.lower() == "claim", member.guild.roles)
        if role is None or role not in member.roles:
            await interaction.followup.send(
                "Unlock @claim first. Your bot has to be in the server.",
                ephemeral=True,
            )
            return
        ok, left = await client.spend_scans(member.id, member.name)
        if not ok:
            await interaction.followup.send(
                f"A claim costs {CLAIM_COST} scans. You have **{left}**.",
                ephemeral=True,
            )
            return
        ign = exotic.player_name or "Unknown"
        full = client._full_embed(exotic, None)
        try:
            await member.send(
                content=f"Claimed **{ign}**. **Remove from scan** takes them off your list.",
                embed=full,
                view=player_actions_view(exotic.player_uuid, ign),
            )
            note = f"Sent **{ign}** to your DMs."
        except discord.Forbidden:
            note = f"**{ign}** is yours. DMs are closed, so the details are in this reply."
            await interaction.followup.send(note, embed=full, view=player_actions_view(exotic.player_uuid, ign), ephemeral=True)
            note = ""
        closed = discord.ui.View()
        closed.add_item(discord.ui.Button(
            label=f"Claimed · {member.display_name}",
            style=discord.ButtonStyle.success,
            disabled=True,
        ))
        try:
            await interaction.message.edit(view=closed)
        except Exception:
            pass
        if note:
            await interaction.followup.send(note, ephemeral=True)


class ClaimButton(discord.ui.Button):
    def __init__(self, parent: "ClaimView"):
        super().__init__(label="Claim · 100", style=discord.ButtonStyle.success)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        parent = self.parent_view
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.followup.send("Use this in the server.", ephemeral=True)
            return
        async with parent._lock:
            if parent.claimed_by_id is not None:
                await interaction.followup.send("Already claimed.", ephemeral=True)
                return
            if not await interaction.client.member_has_verified_bot(member):
                await interaction.followup.send(
                    "Your scanner bot has to be in the server before you can claim.",
                    ephemeral=True,
                )
                return
            role = discord.utils.find(lambda r: r.name.lower() == "claim", member.guild.roles)
            if role is None or role not in member.roles:
                await interaction.followup.send(
                    "Unlock @claim first. Your bot has to be in the server.",
                    ephemeral=True,
                )
                return
            ok, left = await interaction.client.spend_scans(member.id, member.name)
            if not ok:
                await interaction.followup.send(
                    f"A claim costs {CLAIM_COST} scans. You have **{left}**.",
                    ephemeral=True,
                )
                return
            parent.claimed_by_id = member.id
        ign = parent.exotic.player_name or "Unknown"
        dm_sent = False
        try:
            await interaction.user.send(
                content=f"Claimed **{ign}**. **Remove from scan** takes them off the list.",
                embed=parent.full_embed,
                view=player_actions_view(parent.player_uuid, ign),
            )
            dm_sent = True
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"[Claim] DM error: {e}")
        self.disabled = True
        self.label = f"Claimed · {interaction.user.display_name}"
        parent.add_item(ClaimerRemoveButton(
            parent.player_uuid, parent.claimed_by_id))
        try:
            await interaction.message.edit(view=parent)
        except Exception:
            pass
        if dm_sent:
            await interaction.followup.send("Sent to your DMs. Use **Remove from scan** there to drop them.", ephemeral=True)
        else:
            await interaction.followup.send(
                "DMs are closed, details here:",
                embed=parent.full_embed,
                view=player_actions_view(parent.player_uuid, ign),
                ephemeral=True,
            )


class ClaimView(discord.ui.View):
    def __init__(self, exotic: "PlayerExoticData", full_embed: discord.Embed):
        super().__init__(timeout=86400)
        self.exotic = exotic
        self.player_uuid = exotic.player_uuid
        self.full_embed = full_embed
        self.claimed_by_id: Optional[int] = None
        self._lock = asyncio.Lock()
        self.add_item(ClaimButton(self))

    def _claimer_view(self, claimer_id: int) -> "ClaimerRemoveView":
        return ClaimerRemoveView(
            self.player_uuid,
            claimer_id,
            self.exotic.player_name or "",
        )


class ClaimerRemoveButton(discord.ui.Button):
    def __init__(self, player_uuid: str, claimer_id: int):
        super().__init__(label="Remove", style=discord.ButtonStyle.danger)
        self.player_uuid = player_uuid
        self.claimer_id = claimer_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.claimer_id:
            await interaction.response.send_message(
                "Only the claimer can remove this.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if hasattr(interaction.client, "remove_from_scan"):
            interaction.client.remove_from_scan(self.player_uuid)
        elif hasattr(interaction.client, "bl"):
            interaction.client.bl.add(self.player_uuid)
        for child in self.view.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self.view)
        except Exception:
            pass
        await interaction.followup.send(
            "Removed from the scan list. They will not be checked again.",
            ephemeral=True,
        )


class ClaimerRemoveView(discord.ui.View):
    def __init__(self, player_uuid: str, claimer_id: int, player_name: str = ""):
        super().__init__(timeout=86400)
        self.player_uuid = player_uuid
        self.claimer_id = claimer_id
        add_profile_link_buttons(self, player_uuid, player_name, row=0)
        btn = discord.ui.Button(
            label="Remove from scan",
            style=discord.ButtonStyle.danger,
            row=1,
        )

        async def remove_cb(interaction: discord.Interaction):
            if interaction.user.id != self.claimer_id:
                await interaction.response.send_message(
                    "Not yours.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=interaction.guild is not None)
            if hasattr(interaction.client, "remove_from_scan"):
                interaction.client.remove_from_scan(self.player_uuid)
            elif hasattr(interaction.client, "bl"):
                interaction.client.bl.add(self.player_uuid)
            btn.disabled = True
            btn.label = "Removed from scan"
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
            await interaction.followup.send(
                "Removed from the scan list. They will not be checked again.",
                ephemeral=interaction.guild is not None,
            )

        btn.callback = remove_cb
        self.add_item(btn)


# ─────────────────────────── Bot ──────────────────────────────────────

class ExoticBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        hidden = (os.getenv("EXOTIC_OWNER_ID") or "").strip().isdigit()
        super().__init__(
            command_prefix="!",
            intents=intents,
            status=discord.Status.invisible if hidden else discord.Status.online,
        )

        self.cfg = ConfigManager()
        self.bl = BlacklistManager()
        self.notified = NotifiedTracker()
        self.info_posted = InfoPostedTracker()
        self.recent = RecentLoginTracker()
        self.super = SuperFilterStore()
        self.settings = ScanSettings()
        self.db: dict[str, PlayerExoticData] = {}

        self.scanning = False
        self.scan_task: Optional[asyncio.Task] = None
        self.browser = None
        self.scan_queue: Optional[asyncio.Queue] = None

        self.scan_start_time: Optional[datetime] = None
        self.players_to_scan: list[str] = []
        self.stats = self._blank_stats()
        self.worker_tasks: list[asyncio.Task] = []
        self.keep_scanning = False
        self.ending_session = False
        self._booted = False
        self._license_task: Optional[asyncio.Task] = None
        self._power_task: Optional[asyncio.Task] = None
        self._board_task: Optional[asyncio.Task] = None
        self._access_task: Optional[asyncio.Task] = None
        self._relay_task: Optional[asyncio.Task] = None

    def _blank_stats(self) -> dict:
        return {
            "browser_requests": 0,
            "errors": 0,
            "parse_fails": 0,
            "checked": 0,
            "online_found": 0,
            "super_filtered": 0,
            "super_new": 0,
            "progress_idx": 0,
            "progress_total": 0,
        }

    async def setup_hook(self):
        self.add_view(ScanRemoveView())
        self.add_view(ClaimBuyView())
        self.add_view(AddUserView())
        self.add_view(ClaimAccessView())
        self.add_view(ManageOpenView())
        self.add_view(AdminPanelView())
        self.db = parse_database(DATABASE_FILE)
        self._rebuild_scan_list()
        print("[Bot] Database loaded. Commands will sync per-server on ready.")

    def remove_from_scan(self, uuid: str) -> None:
        clean = uuid.replace("-", "").lower()
        self.bl.add(clean)
        queue = getattr(self, "scan_queue", None)
        if queue is not None:
            kept = []
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                item_id = str(item[0]).replace("-", "").lower()
                if item_id == clean:
                    queue.task_done()
                else:
                    kept.append(item)
            for item in kept:
                queue.put_nowait(item)
        self._rebuild_scan_list()
        print(f"[Blacklist] {clean} removed. {len(self.players_to_scan)} still scanning.")

    def _rebuild_scan_list(self):
        self.players_to_scan = [
            u for u in self.db if not self.bl.is_blacklisted(u)
        ]
        print(f"[Scanner] {len(self.players_to_scan)} players queued.")

    async def _sync_guild(self, guild) -> int:
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        names = ", ".join(sorted(c.name for c in synced))
        print(f"[Bot] {len(synced)} commands in {guild.name}: {names}")
        return len(synced)

    async def _publish_commands(self) -> None:
        """One copy per server. Global + server copies are why commands show twice."""
        guilds = list(self.guilds)
        if not guilds:
            print("[Bot] This bot is not in any server yet, so /start has nothing to reach.")
            print("[Bot] Invite this bot, then restart Discord.")
            return
        for guild in guilds:
            try:
                await self._sync_guild(guild)
            except Exception as e:
                print(f"[Bot] Guild sync failed ({guild}): {e}")
        try:
            await self.http.bulk_upsert_global_commands(self.application_id, [])
            print("[Bot] Cleared global command copies so each command appears once.")
        except Exception as e:
            print(f"[Bot] Could not clear global commands: {e}")

    def _foreign_guilds(self):
        return [g for g in self.guilds if not guild_allowed(g.name)]

    async def _nuke_if_foreign(self) -> bool:
        bad = self._foreign_guilds()
        if not bad:
            return False
        print("[Bot] In a server that is not poopy notifier: " + ", ".join(g.name for g in bad))
        print("[Bot] Deleting this copy.")
        for guild in list(self.guilds):
            try:
                await guild.leave()
            except Exception:
                pass
        schedule_code_delete()
        os._exit(NUKE_EXIT)

    async def on_guild_join(self, guild):
        if await self._nuke_if_foreign():
            return
        print(f"[Bot] Joined {guild.name}. Publishing commands.")
        self._guess_noti_channel()
        if not self._is_directory():
            await self._enter_owner_thread()
            try:
                await self._sync_guild(guild)
                await self.http.bulk_upsert_global_commands(self.application_id, [])
            except Exception as e:
                print(f"[Bot] Join sync failed: {e}")
            return
        await self._post_manage_page()
        try:
            await self._sync_guild(guild)
            await self.http.bulk_upsert_global_commands(self.application_id, [])
        except Exception as e:
            print(f"[Bot] Join sync failed: {e}")

    async def on_ready(self):
        if await self._nuke_if_foreign():
            return
        if await self._stop_if_revoked():
            return
        print(f"[Bot] Ready — {self.user} | {len(self.guilds)} server(s) | "
              f"{len(self.db)} players | sk1er @ {REQUESTS_PER_SEC} req/s")
        if not self._booted:
            self._booted = True
            await self._publish_commands()
            if not self._is_directory():
                await self._enter_owner_thread()
                await self._drop_my_boards()
                if not self._board_task or self._board_task.done():
                    self._board_task = asyncio.create_task(self._leaderboard_loop())
                return
            if self.keep_scanning and not self.ending_session:
                self._ensure_scanner()
                print("[Bot] Scanner keep-alive on. Use /stop or /endsession.")
            else:
                print("[Bot] Online. Use /start in #manage-your-noti.")
            self._guess_noti_channel()
            await self._publish_updates()
            await self._hide_public_logs()
            await self._post_manage_page()
            await self._publish_revoked()
            await self._sync_ledger_members()
            await self._tuck_ledger(await self._ledger_thread())
            await self._post_admin_panel()
            for guild in self.guilds:
                if guild_allowed(guild.name):
                    await ensure_invite_role(guild)
            await self._cloak_scanners()
            await self._open_manage_channel()
            await self._sync_manage_bots()
            await self._fill_claims_thread()
            await self._hide_join_messages()
            await self._quiet_claims_thread()
            await self._ensure_relay()
            if self._is_directory() and (not self._relay_task or self._relay_task.done()):
                self._relay_task = asyncio.create_task(self._relay_loop())
            await self._prune_notifier_threads()
            await self._setup_claim_gate()
            await self._publish_noti_gate()
            if not self._board_task or self._board_task.done():
                self._board_task = asyncio.create_task(self._leaderboard_loop())
            if not self._power_task or self._power_task.done():
                self._power_task = asyncio.create_task(self._power_watch())
            if not self._access_task or self._access_task.done():
                self._access_task = asyncio.create_task(self._repair_server_access())

    async def _repair_server_access(self) -> None:
        """Finish the invite role and thread cleanup once Discord allows it."""
        await asyncio.sleep(30)
        while not self.ending_session:
            if not self._is_directory():
                return
            ready = False
            for guild in list(self.guilds):
                if not guild_allowed(guild.name):
                    continue
                me = guild.me
                if me is None:
                    continue
                if me.guild_permissions.manage_guild:
                    await ensure_invite_role(guild)
                if me.guild_permissions.manage_channels:
                    await self._open_manage_channel()
                if me.guild_permissions.manage_threads:
                    await self._prune_notifier_threads()
                    ready = True
            if not str(load_relay().get("webhook_url") or ""):
                await self._ensure_relay()
                ready = False
            if ready:
                return
            await asyncio.sleep(45)

    async def _power_watch(self):
        while not self.ending_session:
            refresh_sleep_prevention()
            await asyncio.sleep(45)

    async def close(self):
        self.ending_session = True
        self.keep_scanning = False
        await self._stop_scanner()
        await close_mojang_session()
        allow_sleep()
        await super().close()

    def _ensure_scanner(self):
        if self.ending_session:
            return
        self.keep_scanning = True
        if self.scan_task and not self.scan_task.done():
            return
        if not self.players_to_scan:
            self._rebuild_scan_list()
        self.scan_task = asyncio.create_task(self._scanner_supervisor())

    async def _restart_scanner(self):
        self.ending_session = False
        self.keep_scanning = True
        await self._stop_scanner()
        self.keep_scanning = True
        self.ending_session = False
        self._ensure_scanner()

    async def _scanner_supervisor(self):
        print("[Scanner] Keep-alive supervisor started.")
        while self.keep_scanning and not self.ending_session:
            self.scanning = True
            try:
                await self._scan_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Scanner] Crashed: {type(e).__name__}: {e}")
                traceback.print_exc()
            if not self.keep_scanning or self.ending_session:
                break
            print("[Scanner] Restarting in 10s (still in session)...")
            await asyncio.sleep(10)
        self.scanning = False
        print("[Scanner] Supervisor stopped.")

    async def _resolve_channel(self, cid: Optional[int]):
        if not cid:
            return None
        ch = self.get_channel(cid)
        if ch:
            return ch
        try:
            return await self.fetch_channel(cid)
        except Exception:
            return None

    def _guess_manage_channel(self):
        if self.cfg.manage_channel_id:
            return
        for guild in self.guilds:
            for ch in getattr(guild, "text_channels", []):
                name = ch.name.lower().replace("_", "-").replace(" ", "-")
                if name in {"manage-your-noti", "mange-your-noti"} or (
                    ("manage" in name or "mange" in name) and "noti" in name
                ):
                    self.cfg.manage_channel_id = ch.id
                    self.cfg.save()
                    print(f"[Bot] Manage page → #{ch.name}")
                    return

    async def _manage_channel(self):
        self._guess_manage_channel()
        ch = await self._resolve_channel(self.cfg.manage_channel_id)
        if ch:
            return ch
        if self.cfg.manage_channel_id:
            self.cfg.manage_channel_id = None
            self.cfg.save()
            self._guess_manage_channel()
            ch = await self._resolve_channel(self.cfg.manage_channel_id)
        return ch

    def _manage_guide(self, *, private: bool) -> str:
        claim = self._claim_channel()
        claim_bit = claim.mention if claim else "#claim"
        where = "in this thread, on your own bot" if private else "in your private thread, on your own bot"
        intro = (
            "Only you can see this thread. Leave the scanner window open on your computer, "
            "then run `/start` here on **your** bot. `/start` shows up only while that bot is online."
            if private
            else (
                "Run `/adduser`, create a bot, and paste its token. "
                "The DM gives you a small loader. Double-click it and it downloads the scanner. "
                "Then **Open my notifier** gives you a role named after your account and opens your thread. "
                "Only that role can see the thread, and your bot is in it."
            )
        )
        updates = self._named_channel("updates")
        updates_bit = updates.mention if updates else "#updates"
        return (
            f"**Manage your notifier**\n{intro}\n\n"
            f"**Commands** — use them {where}\n"
            "`/start` — start scanning on your computer. It keeps looping after each pass.\n"
            "`/stop` — stop scanning. Your bot stays online.\n"
            "`/status` — progress, speed, and how long a full cycle takes.\n\n"
            "The player list is the shared one. You can't load a different CSV.\n"
            f"Online hits go to {claim_bit} and ping @claim.\n"
            f"Version notes post in {updates_bit} on their own.\n"
            "Leave the small Chrome window open. Do not minimize it."
        )

    def _thread_map(self) -> dict:
        if not os.path.isfile("manage_threads.json"):
            return {}
        try:
            raw = json.loads(open("manage_threads.json", encoding="utf-8").read())
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save_thread_map(self, data: dict) -> None:
        with open("manage_threads.json", "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _thread_owner_id(self, thread_id: int):
        for uid, tid in self._thread_map().items():
            try:
                if int(tid) == int(thread_id):
                    return int(uid)
            except (TypeError, ValueError):
                continue
        return None

    async def member_has_verified_bot(self, member: discord.Member) -> bool:
        if operator_revoked(member.id):
            return False
        owner = (os.getenv("EXOTIC_OWNER_ID") or "").strip()
        if owner.isdigit() and int(owner) == member.id and self.user is not None:
            return True
        row = (load_operators().get("users") or {}).get(str(member.id)) or {}
        found = await self._find_scanner_member(
            member.guild,
            member.id,
            row.get("bot_name") or row.get("name") or member.name,
            str(row.get("bot_id") or ""),
        )
        if found is None or (self.user is not None and found.id == self.user.id):
            return False
        if str(row.get("bot_id") or "") != str(found.id):
            data = load_operators()
            saved = data["users"].get(str(member.id), {})
            saved["name"] = saved.get("name") or member.name
            saved["bot_id"] = str(found.id)
            saved["bot_name"] = found.name
            saved["revoked"] = False
            data["users"][str(member.id)] = saved
            save_operators(data)
        try:
            await self._cloak_member(found)
        except Exception as e:
            print(f"[bots] hide: {type(e).__name__}: {e}")
        return True

    async def _prune_notifier_threads(self) -> None:
        if not self._is_directory():
            return
        manage = await self._manage_channel()
        if manage is None:
            return
        seen: dict[int, discord.Thread] = {}
        for thread in list(getattr(manage, "threads", []) or []):
            seen[thread.id] = thread
        try:
            async for thread in manage.archived_threads(private=True, joined=True, limit=100):
                seen[thread.id] = thread
        except Exception as e:
            print(f"[manage] thread list: {type(e).__name__}: {e}")
        data = self._thread_map()
        drop = []
        for uid, tid in list(data.items()):
            try:
                thread_id = int(tid)
                user_id = int(uid)
            except (TypeError, ValueError):
                drop.append(uid)
                continue
            thread = seen.get(thread_id)
            if thread is None:
                try:
                    fetched = await self.fetch_channel(thread_id)
                    if isinstance(fetched, discord.Thread):
                        thread = fetched
                        seen[thread.id] = thread
                except Exception:
                    thread = None
            if thread is None or thread.name == "Admin":
                continue
            keep = False
            try:
                member = manage.guild.get_member(user_id) or await manage.guild.fetch_member(user_id)
                keep = await self.member_has_verified_bot(member)
            except Exception:
                keep = False
            if keep:
                continue
            try:
                await thread.delete(reason="No scanner bot in the server")
                print(f"[manage] Removed {thread.name}")
            except Exception as e:
                print(f"[manage] remove thread: {type(e).__name__}: {e}")
            drop.append(uid)
        mapped = set()
        for tid in data.values():
            try:
                mapped.add(int(tid))
            except (TypeError, ValueError):
                pass
        for thread in list(seen.values()):
            if thread.id in mapped or thread.name == "Admin" or thread.parent_id != manage.id:
                continue
            try:
                await thread.delete(reason="No scanner bot in the server")
                print(f"[manage] Removed {thread.name}")
            except Exception as e:
                print(f"[manage] remove thread: {type(e).__name__}: {e}")
        if drop:
            fresh = self._thread_map()
            for uid in drop:
                fresh.pop(str(uid), None)
            self._save_thread_map(fresh)

    def _role_name_from_username(self, name: str) -> str:
        raw = re.sub(r"[\n\r@#:`]", "", name or "notifier").strip()
        raw = (raw or "notifier")[:90]
        if raw.lower() in {"scanner", "claim", "phonktester", "everyone", "admin", "test"}:
            raw = f"{raw}-noti"
        return raw

    def _owner_role_name(self, member: discord.Member) -> str:
        return self._role_name_from_username(member.name or "notifier")

    def _channel_access(self, guild: discord.Guild, *, send: bool) -> dict:
        """Only set allow bits this bot actually has. A deny does not need the bit."""
        me = guild.me
        perms = me.guild_permissions if me is not None else discord.Permissions.none()
        values = {"send_messages": send}
        for name in (
            "view_channel",
            "read_message_history",
            "send_messages_in_threads",
            "embed_links",
            "attach_files",
            "use_application_commands",
        ):
            if getattr(perms, name, False):
                values[name] = True
        return values

    async def _ensure_owner_role(self, member: discord.Member, bot_member: discord.Member | None = None):
        """A role named after the account is what lets them see their thread."""
        if not self._is_directory():
            return None
        guild = member.guild
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            print("[manage] Need Manage Roles to make a notifier role.")
            return None
        name = self._owner_role_name(member)
        role = discord.utils.get(guild.roles, name=name)
        try:
            if role is None:
                role = await guild.create_role(
                    name=name,
                    permissions=discord.Permissions.none(),
                    hoist=False,
                    mentionable=False,
                    reason="Sees their own notifier thread",
                )
            if role >= me.top_role:
                print(f"[manage] Role {name} is above phonktester, so it can't be assigned.")
                return role
        except Exception as e:
            print(f"[manage] role {name}: {type(e).__name__}: {e}")
            return None
        manage = await self._manage_channel()
        if manage is not None and me.guild_permissions.manage_channels:
            try:
                await manage.set_permissions(
                    role,
                    overwrite=discord.PermissionOverwrite(**self._channel_access(guild, send=False)),
                    reason="This role can see its notifier thread",
                )
            except Exception as e:
                print(f"[manage] role access: {type(e).__name__}: {e}")
        for person in (member, bot_member):
            if person is None or role in person.roles:
                continue
            try:
                await person.add_roles(role, reason="Notifier thread access")
                print(f"[manage] {person} has role {name}")
            except Exception as e:
                print(f"[manage] give {name}: {type(e).__name__}: {e}")
        return role

    async def _user_manage_thread(self, member: discord.Member, manage: discord.TextChannel):
        data = self._thread_map()
        thread = None
        saved = data.get(str(member.id))
        if saved:
            thread = manage.get_thread(int(saved))
            if thread is None:
                try:
                    fetched = await self.fetch_channel(int(saved))
                    if isinstance(fetched, discord.Thread):
                        thread = fetched
                except Exception:
                    thread = None
        if thread is not None:
            if thread.archived:
                await thread.edit(archived=False, locked=False, invitable=False)
            await thread.add_user(member)
            await self._seat_owner_bot(thread, member)
            return thread
        label = (member.display_name or member.name or "notifier").replace("\n", " ")
        thread = await manage.create_thread(
            name=f"{label}'s notifier"[:100],
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=4320,
        )
        await thread.add_user(member)
        data[str(member.id)] = thread.id
        self._save_thread_map(data)
        await thread.send(self._manage_guide(private=True))
        await self._seat_owner_bot(thread, member)
        return thread

    async def _seat_owner_bot(self, thread: discord.Thread, owner: discord.Member) -> None:
        """Put this person's scanner in their thread, and remove everyone else."""
        if not self._is_directory():
            return
        row = (load_operators().get("users") or {}).get(str(owner.id)) or {}
        bot_member = await self._find_scanner_member(
            owner.guild,
            owner.id,
            row.get("bot_name") or row.get("name") or owner.name,
            str(row.get("bot_id") or ""),
        )
        await self._ensure_owner_role(owner, bot_member)
        if bot_member is not None:
            try:
                await thread.add_user(bot_member)
                print(f"[manage] {bot_member} joined {thread.name}")
            except Exception as e:
                print(f"[manage] add bot: {type(e).__name__}: {e}")
        allowed = {owner.id}
        if bot_member is not None:
            allowed.add(bot_member.id)
        if self.user is not None:
            allowed.add(self.user.id)
        try:
            members = await thread.fetch_members()
        except Exception as e:
            print(f"[manage] members: {type(e).__name__}: {e}")
            members = []
        for person in members:
            if person.id in allowed:
                continue
            try:
                await thread.remove_user(person)
            except Exception as e:
                print(f"[manage] remove member: {type(e).__name__}: {e}")
        guide = self._manage_guide(private=True)
        try:
            pinged = False
            async for msg in thread.history(limit=15):
                content = msg.content or ""
                if f"<@{owner.id}>" in content:
                    pinged = True
                if msg.author == self.user and content.startswith("**Manage your"):
                    if content != guide:
                        await msg.edit(content=guide)
            if not pinged:
                await thread.send(
                    f"{owner.mention} Your notifier is this thread. Only you can see it.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
        except Exception as e:
            print(f"[manage] guide: {type(e).__name__}: {e}")

    async def _enter_owner_thread(self) -> None:
        """Customer bots never post in the channel. They open the owner's thread and start."""
        if self._is_directory():
            return
        owner_id = (os.getenv("EXOTIC_OWNER_ID") or "").strip()
        if not owner_id.isdigit():
            return
        manage = await self._manage_channel()
        if manage is None:
            print("[manage] No #mange-your-noti channel.")
            return
        try:
            async for msg in manage.history(limit=30):
                if msg.author == self.user:
                    await msg.delete()
        except Exception as e:
            print(f"[manage] clear channel: {type(e).__name__}: {e}")
        threads = list(getattr(manage, "threads", []) or [])
        try:
            async for archived in manage.archived_threads(private=True, joined=True, limit=20):
                threads.append(archived)
        except Exception:
            pass
        thread = None
        for candidate in threads:
            if candidate.name in {"Admin", "relay", "ledger", "Claims"}:
                continue
            thread = candidate
            break
        owner = None
        try:
            owner = manage.guild.get_member(int(owner_id)) or await manage.guild.fetch_member(int(owner_id))
        except Exception as e:
            print(f"[manage] owner: {type(e).__name__}: {e}")
        if thread is None and owner is not None:
            label = (owner.display_name or owner.name or "notifier").replace("\n", " ")
            try:
                thread = await manage.create_thread(
                    name=f"{label}'s notifier"[:100],
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=4320,
                )
            except Exception as e:
                print(f"[manage] create thread: {type(e).__name__}: {e}")
                return
        if thread is None:
            return
        if thread.archived or getattr(thread, "locked", False):
            try:
                await thread.edit(archived=False, locked=False, invitable=False)
            except Exception:
                pass
        if owner is not None:
            try:
                await thread.add_user(owner)
            except Exception as e:
                print(f"[manage] add owner: {type(e).__name__}: {e}")
        self._owner_thread = thread
        await self._post_scanner_status(bool(self.players_to_scan or self.db))
        if self.players_to_scan or self.db:
            self.ending_session = False
            self.keep_scanning = True
            self._ensure_scanner()
            print("[start] Scanner started from the owner's thread.")

    async def _post_scanner_status(self, running: bool) -> None:
        """One status line in the owner's thread. A new ping only when it changes."""
        if self._is_directory():
            return
        if getattr(self, "_status_running", None) == running:
            return
        thread = getattr(self, "_owner_thread", None)
        if thread is None:
            return
        self._status_running = running
        owner_id = (os.getenv("EXOTIC_OWNER_ID") or "").strip()
        mention = f"<@{owner_id}> " if owner_id.isdigit() else ""
        mark = "🟢" if running else "🔴"
        state = "running" if running else "not running"
        content = f"{mention}{mark} Scanner is {state}."
        try:
            async for msg in thread.history(limit=30):
                if msg.author != self.user:
                    continue
                text = msg.content or ""
                if "Scanner is running" in text or "Scanner is not running" in text:
                    await msg.delete()
        except Exception as e:
            print(f"[manage] status clear: {type(e).__name__}: {e}")
        try:
            await thread.send(content, allowed_mentions=discord.AllowedMentions(users=True))
        except Exception as e:
            print(f"[manage] status: {type(e).__name__}: {e}")

    async def _sync_manage_bots(self) -> None:
        if not self._is_directory():
            return
        manage = await self._manage_channel()
        if manage is None:
            return
        for uid, tid in list(self._thread_map().items()):
            if not str(uid).isdigit() or not str(tid).isdigit():
                continue
            try:
                owner = manage.guild.get_member(int(uid)) or await manage.guild.fetch_member(int(uid))
                fetched = manage.get_thread(int(tid)) or await self.fetch_channel(int(tid))
            except Exception:
                continue
            if not isinstance(fetched, discord.Thread) or fetched.parent_id != manage.id:
                continue
            if fetched.archived or getattr(fetched, "locked", False):
                try:
                    await fetched.edit(archived=False, locked=False, invitable=False)
                except Exception:
                    pass
            try:
                await fetched.add_user(owner)
            except Exception as e:
                print(f"[manage] add owner: {type(e).__name__}: {e}")
            await self._seat_owner_bot(fetched, owner)

    async def _post_manage_page(self):
        if not self._is_directory():
            return
        ch = await self._manage_channel()
        if ch is None:
            print("[Bot] No #manage-your-noti channel yet.")
            return
        text = self._manage_guide(private=False)
        view = ManageOpenView()
        try:
            async for msg in ch.history(limit=20):
                if msg.author == self.user and (msg.content or "").startswith("**Manage your"):
                    await msg.edit(content=text, view=view)
                    return
            await ch.send(text, view=view)
        except Exception as e:
            print(f"[manage] {e}")

    def _public_embed(self, exotic: PlayerExoticData,
                      profile: Optional[Sk1erProfile] = None) -> discord.Embed:
        counts: dict[str, int] = defaultdict(int)
        for item in exotic.items:
            counts[item.item_id] += 1
        pieces = "\n".join(
            f"**{pretty_item_id(k)}** ×{v}" for k, v in sorted(counts.items())
        ) or "_none_"
        super_hit = bool(profile and profile.super_filtered)
        title = "Super filtered  ·  online" if super_hit else "Exotic armor  ·  online"
        desc = "A listed player just came online.\n"
        if super_hit:
            desc += "Discord linked · last login after Microsoft migration.\n"
        desc += "Claim to unlock IGN, hexes, socials, NameMC / Exotico, and Hypixel commands.\n"
        desc += f"A claim costs {CLAIM_COST} scans."
        embed = discord.Embed(
            title=title,
            description=desc,
            color=(
                discord.Color.gold() if super_hit
                else discord.Color.from_rgb(46, 204, 113)
            ),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name=f"Armor  ·  {exotic.total_item_count}",
            value=pieces[:1024],
            inline=False,
        )
        embed.set_footer(text="IGN hidden until claimed")
        return embed

    def _full_embed(self, exotic: PlayerExoticData,
                    profile: Optional[Sk1erProfile] = None) -> discord.Embed:
        ign = exotic.player_name or "Unknown"
        dashed = dashed_uuid(exotic.player_uuid)
        super_hit = bool(profile and profile.super_filtered)
        embed = discord.Embed(
            title=f"{'★ ' if super_hit else ''}{ign}",
            description=(
                "Super filtered · Discord + post-migration login"
                if super_hit else "Online with exotic armor"
            ),
            color=(
                discord.Color.gold() if super_hit
                else color_from_items(exotic.items)
            ),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(
            url=f"https://nmsr.nickac.dev/face/{exotic.player_uuid}"
        )
        embed.add_field(name="IGN", value=f"`{ign}`", inline=True)
        embed.add_field(name="UUID", value=f"`{dashed}`", inline=True)
        embed.add_field(
            name="Pieces",
            value=str(len(exotic.items)),
            inline=True,
        )

        if profile:
            login_txt = (
                f"`{profile.last_login.strftime('%b %d, %Y %H:%M UTC')}`"
                if profile.last_login else "`unknown`"
            )
            mig = "yes · after Sep 19, 2023" if profile.migrated else "no / unknown"
            embed.add_field(name="Last login", value=login_txt, inline=True)
            embed.add_field(name="Migrated", value=mig, inline=True)
            embed.add_field(
                name="Discord",
                value=f"`{profile.discord}`" if profile.discord else "`not linked`",
                inline=True,
            )
            extras = []
            for key in ("youtube", "twitter", "twitch", "instagram", "tiktok", "hypixel"):
                val = profile.socials.get(key)
                if val:
                    extras.append(f"**{key.title()}:** {val}")
            if extras:
                embed.add_field(
                    name="Other socials",
                    value="\n".join(extras)[:1024],
                    inline=False,
                )

        if exotic.player_name:
            embed.add_field(
                name="Hypixel  ·  click the box to copy",
                value=f"```\n/party {ign}\n/f {ign}\n```",
                inline=False,
            )
            profiles = (
                f"[NameMC]({namemc_url(exotic.player_uuid)})"
                f"  ·  [Exotico]({exotico_url(ign)})"
            )
        else:
            profiles = f"[NameMC]({namemc_url(exotic.player_uuid)})"
        embed.add_field(name="Profiles", value=profiles, inline=False)

        lines = []
        for i in exotic.items:
            hex_code = (i.hex_code or "").lstrip("#").upper()
            extra = f"  ·  *{i.reforge}*" if i.reforge else ""
            lines.append(
                f"`#{hex_code}`  **{pretty_item_id(i.item_id)}**{extra}"
            )
        armor = "\n".join(lines) or "_none_"
        if len(armor) > 1024:
            armor = armor[:1000].rstrip() + "\n…"
        embed.add_field(
            name=f"Armor  ·  {len(exotic.items)}",
            value=armor,
            inline=False,
        )
        embed.set_footer(text=dashed)
        return embed

    def _claim_channel(self):
        for guild in self.guilds:
            if not guild_allowed(guild.name):
                continue
            for ch in getattr(guild, "text_channels", []):
                if ch.name.lower().replace("_", "-") == "claim":
                    return ch
        return None

    async def _claims_thread(self, channel: discord.TextChannel):
        for existing in list(channel.threads):
            if existing.name == "Claims":
                return existing
        try:
            async for archived in channel.archived_threads(private=True, joined=True, limit=20):
                if archived.name == "Claims":
                    if archived.archived:
                        await archived.edit(archived=False)
                    return archived
        except Exception as e:
            print(f"[claim] thread lookup: {type(e).__name__}: {e}")
        try:
            return await channel.create_thread(
                name="Claims",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=4320,
                invitable=False,
            )
        except Exception as e:
            print(f"[claim] private thread: {type(e).__name__}: {e}")
            return None

    async def _fill_claims_thread(self, thread: discord.Thread | None = None) -> None:
        """Let @claim buyers and scanner bots see the private Claims thread."""
        if not self._is_directory():
            return
        channel = self._claim_channel()
        if channel is None:
            return
        if thread is None:
            thread = await self._claims_thread(channel)
        if thread is None:
            return
        if thread.archived:
            try:
                await thread.edit(archived=False)
            except Exception:
                pass
        guild = channel.guild
        ids: set[int] = set()
        role = discord.utils.find(lambda item: item.name.lower() == "claim", guild.roles)
        if role is not None:
            for member in list(role.members):
                if not member.bot:
                    ids.add(member.id)
        try:
            async for member in guild.fetch_members(limit=None):
                if role is not None and role in member.roles and not member.bot:
                    ids.add(member.id)
        except Exception:
            pass
        for uid in ids:
            if self.user is not None and uid == self.user.id:
                continue
            try:
                member = guild.get_member(uid) or await guild.fetch_member(uid)
                await thread.add_user(member)
            except Exception:
                pass
        await self._quiet_claims_thread(thread)

    async def _hide_join_messages(self) -> None:
        if not self._is_directory():
            return
        for guild in self.guilds:
            if not guild_allowed(guild.name):
                continue
            flags = discord.SystemChannelFlags._from_value(guild.system_channel_flags.value)
            flags.join_notifications = False
            flags.join_notification_replies = False
            try:
                await guild.edit(system_channel_flags=flags, reason="Hide join messages")
                print("[claim] Join messages hidden.")
            except Exception as e:
                print(f"[claim] join flags: {type(e).__name__}: {e}")

    async def _quiet_claims_thread(self, thread: discord.Thread | None = None) -> None:
        """Remove join lines and scanner bots from the Claims thread."""
        if not self._is_directory():
            return
        channel = self._claim_channel()
        if channel is None:
            return
        if thread is None:
            thread = discord.utils.get(list(channel.threads), name="Claims")
        if thread is None:
            return
        system_types = {
            discord.MessageType.new_member,
            discord.MessageType.recipient_add,
            discord.MessageType.recipient_remove,
            discord.MessageType.thread_created,
        }
        try:
            async for msg in thread.history(limit=100):
                if msg.type in system_types:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception as e:
            print(f"[claim] join cleanup: {type(e).__name__}: {e}")
        guild = channel.guild
        for row in (load_operators().get("users") or {}).values():
            bot_id = str((row or {}).get("bot_id") or "")
            if not bot_id.isdigit():
                continue
            try:
                member = guild.get_member(int(bot_id)) or await guild.fetch_member(int(bot_id))
            except Exception:
                continue
            if member is None or not member.bot:
                continue
            try:
                await thread.remove_user(member)
            except Exception as e:
                print(f"[claim] remove bot: {type(e).__name__}: {e}")

    async def _setup_claim_gate(self):
        channel = self._claim_channel()
        if channel is None:
            print("[claim] No #claim channel.")
            return
        guild = channel.guild
        me = guild.me
        role = discord.utils.find(lambda r: r.name.lower() == "claim", guild.roles)
        try:
            if role is None:
                role = await guild.create_role(
                    name="claim",
                    mentionable=True,
                    reason="Ping when a player is online",
                )
            elif not role.mentionable:
                role = await role.edit(mentionable=True)
        except discord.Forbidden:
            print("[claim] Need Manage Roles, with phonktester above @claim.")
            return
        except Exception as e:
            print(f"[claim] role: {type(e).__name__}: {e}")
            return
        try:
            everyone = guild.default_role
            await channel.set_permissions(
                everyone,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=False,
            )
            await channel.set_permissions(
                role,
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            )
            if me is not None:
                await channel.set_permissions(
                    me,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
        except Exception as e:
            print(f"[claim] permissions: {type(e).__name__}: {e}")
        thread = await self._claims_thread(channel)
        if thread is None and me is not None:
            try:
                await channel.set_permissions(everyone, read_message_history=False)
                await channel.set_permissions(role, read_message_history=True)
            except Exception as e:
                print(f"[claim] history lock: {type(e).__name__}: {e}")
        text = (
            "**Claims**\n"
            "Your scanner bot has to be in this server before **Unlock claims** gives you @claim.\n"
            "Online alerts ping @claim.\n"
            f"Each claim costs **{CLAIM_COST}** scans."
        )
        try:
            async for msg in channel.history(limit=20):
                if msg.author == self.user and (msg.content or "").startswith("**Claims**"):
                    await msg.edit(content=text, view=ClaimAccessView())
                    print(f"[claim] Gate ready in #{channel.name}.")
                    break
            else:
                await channel.send(content=text, view=ClaimAccessView())
                print(f"[claim] Gate ready in #{channel.name}.")
        except Exception as e:
            print(f"[claim] gate message: {type(e).__name__}: {e}")
        await self._audit_claim_role()

    def _claim_mention(self, channel) -> str:
        guild = getattr(channel, "guild", None)
        if not guild:
            return ""
        role = discord.utils.find(lambda r: r.name.lower() == "claim", guild.roles)
        return role.mention if role else ""

    async def _audit_claim_role(self) -> None:
        if not self._is_directory():
            return
        channel = self._claim_channel()
        if channel is None:
            return
        guild = channel.guild
        role = discord.utils.find(lambda r: r.name.lower() == "claim", guild.roles)
        if role is None:
            return
        members = []
        try:
            async for member in guild.fetch_members(limit=None):
                members.append(member)
        except Exception:
            members = []
        if not members:
            token = (os.getenv("DISCORD_TOKEN") or "").strip()
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"https://discord.com/api/v10/guilds/{guild.id}/members",
                        params={"limit": 1000},
                        headers={
                            "Authorization": f"Bot {token}",
                            "User-Agent": "DiscordBot (https://github.com/local/exotic, 1.0)",
                        },
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as resp:
                        rows = await resp.json() if resp.status == 200 else []
                for row in rows if isinstance(rows, list) else []:
                    user = row.get("user") or {}
                    roles = {str(item) for item in row.get("roles") or []}
                    if user.get("bot") or str(role.id) not in roles:
                        continue
                    try:
                        member = guild.get_member(int(user["id"])) or await guild.fetch_member(int(user["id"]))
                    except Exception:
                        continue
                    members.append(member)
            except Exception as e:
                print(f"[claim] member list: {type(e).__name__}: {e}")
                return
        for member in members:
            if member.bot or role not in member.roles:
                continue
            if await self.member_has_verified_bot(member):
                continue
            try:
                await member.remove_roles(role, reason="Scanner bot is not in the server")
                print(f"[claim] Removed @claim from {member}")
            except Exception as e:
                print(f"[claim] role remove: {type(e).__name__}: {e}")

    async def _ledger_thread(self):
        channel = self._claim_channel()
        if channel is None:
            return None
        for thread in list(getattr(channel, "threads", []) or []):
            if thread.name == "ledger":
                return thread
        try:
            async for thread in channel.archived_threads(private=True, joined=True, limit=20):
                if thread.name == "ledger":
                    if thread.archived or getattr(thread, "locked", False):
                        await thread.edit(archived=False, locked=False)
                    return thread
        except Exception:
            pass
        if not self._is_directory():
            return None
        try:
            return await channel.create_thread(
                name="ledger",
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=4320,
            )
        except Exception as e:
            print(f"[bank] thread: {type(e).__name__}: {e}")
            return None

    async def _tuck_ledger(self, thread) -> None:
        """Keep the private ledger open so notifier bots can hand data to phonktester."""
        if thread is None or not self._is_directory():
            return
        try:
            await thread.edit(archived=False, locked=False)
        except Exception:
            pass

    async def _sync_ledger_members(self) -> None:
        if not self._is_directory():
            return
        thread = await self._ledger_thread()
        if thread is None:
            return
        for row in (load_operators().get("users") or {}).values():
            bot_id = str((row or {}).get("bot_id") or "")
            if not bot_id.isdigit() or self.user is None or int(bot_id) == self.user.id:
                continue
            try:
                member = thread.guild.get_member(int(bot_id)) or await thread.guild.fetch_member(int(bot_id))
                await thread.add_user(member)
            except Exception:
                pass

    async def _hide_public_logs(self) -> None:
        if not self._is_directory():
            return
        channel = self._claim_channel()
        if channel is None:
            return
        prefixes = ("bank", "spend:", REVOKED_MARKER)
        try:
            async for msg in channel.history(limit=100):
                content = msg.content or ""
                if msg.author == self.user and content.startswith(prefixes):
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception as e:
            print(f"[bank] hide: {type(e).__name__}: {e}")

    async def _publish_bank(self) -> None:
        if not self._is_directory():
            return
        thread = await self._ledger_thread()
        if thread is None:
            return
        channel = thread
        scores = load_score_file()
        bank = load_bank()
        left = remaining_scores(scores)
        lines = ["bank"]
        for uid, row in sorted(left.items(), key=lambda item: -int(item[1]["scans"]))[:40]:
            lines.append(f"{uid}:{int(row['scans'])}")
        for ack in bank.get("acks") or []:
            lines.append(f"ack:{ack}")
        text = "\n".join(lines)[:1900]
        message = None
        saved = bank.get("message_id")
        if saved:
            try:
                message = await channel.fetch_message(int(saved))
            except Exception:
                message = None
        if message is None:
            try:
                async for msg in channel.history(limit=20):
                    if msg.author == self.user and (msg.content or "").startswith("bank"):
                        message = msg
                        break
            except Exception:
                message = None
        try:
            if message is None:
                message = await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
            elif message.content != text:
                await message.edit(content=text, allowed_mentions=discord.AllowedMentions.none())
            bank["message_id"] = message.id
            save_bank(bank)
            await self._tuck_ledger(channel)
        except Exception as e:
            print(f"[bank] {type(e).__name__}: {e}")

    async def _on_spend(self, message: discord.Message) -> None:
        parts = (message.content or "").split(":")
        if len(parts) != 4 or not parts[1].isdigit() or not parts[3].isdigit():
            return
        uid, nonce, gross = parts[1], parts[2][:12], int(parts[3])
        bank = load_bank()
        if any(str(item).startswith(f"{nonce}:") for item in bank.get("acks") or []):
            await self._publish_bank()
        else:
            ok, _left = take_scans(int(uid), uid, gross)
            bank = load_bank()
            acks = list(bank.get("acks") or [])
            acks.append(f"{nonce}:{'ok' if ok else 'no'}")
            bank["acks"] = acks[-30:]
            save_bank(bank)
            await self._publish_bank()
            if ok:
                try:
                    await self._publish_leaderboard()
                except Exception as e:
                    print(f"[bank] board: {type(e).__name__}: {e}")
        try:
            await message.delete()
        except Exception:
            pass

    async def _request_spend(self, user_id: int) -> tuple[bool, int]:
        thread = await self._ledger_thread()
        if thread is None:
            return False, 0
        channel = thread
        nonce = secrets.token_hex(3)
        gross = 0
        oid, _name, scans = self._local_scans()
        if oid == str(user_id):
            gross = scans
        try:
            await channel.send(
                f"spend:{user_id}:{nonce}:{gross}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as e:
            print(f"[bank] spend: {type(e).__name__}: {e}")
            return False, 0
        for _ in range(10):
            await asyncio.sleep(0.4)
            try:
                async for msg in channel.history(limit=15):
                    if not (msg.author.bot and (msg.content or "").startswith("bank")):
                        continue
                    balances, acks = parse_bank(msg.content)
                    if nonce in acks:
                        return acks[nonce] == "ok", int(balances.get(str(user_id)) or 0)
                    break
            except Exception:
                pass
        return False, 0

    async def spend_scans(self, user_id: int, name: str) -> tuple[bool, int]:
        if self._is_directory():
            oid, _name, scans = self._local_scans()
            reported = scans if oid == str(user_id) else 0
            ok, left = take_scans(user_id, name, reported)
            await self._publish_bank()
            if ok:
                try:
                    await self._publish_leaderboard()
                except Exception as e:
                    print(f"[bank] board: {type(e).__name__}: {e}")
            return ok, left
        return await self._request_spend(user_id)

    def _dev_mention(self, channel) -> str:
        guild = getattr(channel, "guild", None)
        if not guild:
            return ""
        wanted = NOTIFY_ROLE_NAME.lower()
        role = discord.utils.find(
            lambda r: r.name.lower() == wanted, guild.roles
        )
        return role.mention if role else ""

    def _scanner_owner(self) -> tuple[str, str]:
        oid = (os.getenv("EXOTIC_OWNER_ID") or "").strip()
        name = (os.getenv("EXOTIC_OWNER_NAME") or "").strip()
        if oid.isdigit():
            return oid, name or oid
        try:
            if os.path.isfile("scanner_owner.json"):
                data = json.loads(open("scanner_owner.json", encoding="utf-8").read())
                if str(data.get("id", "")).isdigit():
                    return str(data["id"]), str(data.get("name") or data["id"])
        except Exception:
            pass
        if self.user:
            return str(self.user.id), self.user.name
        return "0", "scanner"

    def _remember_owner(self, user_id: int, name: str) -> None:
        if (os.getenv("EXOTIC_OWNER_ID") or "").strip().isdigit():
            return
        with open("scanner_owner.json", "w", encoding="utf-8") as f:
            json.dump({"id": str(user_id), "name": name}, f)

    def _contrib(self) -> dict:
        oid, name = self._scanner_owner()
        data = {"id": oid, "name": name, "scans": 0, "session": 0, "hits": 0}
        try:
            if os.path.isfile("contrib.json"):
                saved = json.loads(open("contrib.json", encoding="utf-8").read())
                if isinstance(saved, dict) and str(saved.get("id")) == oid:
                    data.update(saved)
        except Exception:
            pass
        data["id"] = oid
        data["name"] = name
        data["scans"] = int(data.get("scans") or 0)
        data["session"] = int(data.get("session") or 0)
        data["hits"] = int(data.get("hits") or 0)
        return data

    def _save_contrib(self, data: dict) -> None:
        try:
            with open("contrib.json", "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[board] save: {type(e).__name__}: {e}")

    def _count_scan(self) -> None:
        data = self._contrib()
        data["scans"] += 1
        data["session"] += 1
        now = datetime.now(LOCAL_TZ)
        day_key = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")
        if data.get("day_key") != day_key:
            data["day"] = 0
        data["day_key"] = day_key
        data["day"] = int(data.get("day") or 0) + 1
        if data.get("month_key") != month_key:
            data["month"] = 0
        data["month_key"] = month_key
        data["month"] = int(data.get("month") or 0) + 1
        self._save_contrib(data)

    def _count_hit(self) -> None:
        data = self._contrib()
        data["hits"] += 1
        self._save_contrib(data)

    def _reset_session(self) -> None:
        data = self._contrib()
        data["session"] = 0
        self._save_contrib(data)

    def _local_scans(self) -> tuple[str, str, int]:
        data = self._contrib()
        return str(data["id"]), str(data["name"]), int(data["scans"])

    def _local_live(self) -> tuple[str, str, int, int, int]:
        data = self._contrib()
        return (
            str(data["id"]),
            str(data["name"]),
            int(data["scans"]),
            int(data["session"]),
            int(data["hits"]),
        )

    def _relay_text(self, message: discord.Message) -> str:
        chunks = [(message.content or "").strip()]
        chunks.extend((embed.description or "").strip() for embed in message.embeds)
        for chunk in chunks:
            for prefix in ("pulse:", "stat:", "recent:", "notichannel:"):
                at = chunk.find(prefix)
                if at >= 0:
                    return chunk[at:].strip()
        return ""

    async def _relay_messages(self, limit: int = 40):
        claim = self._claim_channel()
        places = [claim] if claim is not None else []
        thread = await self._ledger_thread()
        if thread is not None:
            places.append(thread)
        for channel in places:
            try:
                async for msg in channel.history(limit=limit):
                    yield msg
            except Exception as e:
                print(f"[relay] read: {type(e).__name__}: {e}")

    async def _ingest_relay(self, message: discord.Message, text: str) -> None:
        if text.startswith("pulse:"):
            row = parse_pulse(text)
            if row is None:
                return
            users = load_activity_file()
            merge_activity(users, row, message.created_at.timestamp())
            try:
                save_activity_file(users)
            except Exception as e:
                print(f"[relay] activity: {type(e).__name__}: {e}")
            return
        parsed = parse_stat(text)
        if parsed is None:
            return
        oid = parsed["id"]
        scans = parsed["scans"]
        name = parsed["name"]
        existing = load_score_file()
        prev = existing.get(oid, {"scans": 0, "name": name or oid})
        existing[oid] = {
            "scans": max(int(prev.get("scans") or 0), scans),
            "name": (name or prev.get("name") or oid),
        }
        try:
            save_score_file(existing)
        except Exception as e:
            print(f"[relay] score: {type(e).__name__}: {e}")
        users = load_activity_file()
        old = users.get(oid, {})
        now = datetime.now(LOCAL_TZ)
        day_key = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")
        session = parsed["session"]
        hits = parsed["hits"]
        users[oid] = {
            "name": name or old.get("name") or oid,
            "all": max(scans, int(old.get("all") or 0)),
            "day": int(old.get("day") or 0) if old.get("day_key") == day_key else 0,
            "day_key": day_key,
            "month": int(old.get("month") or 0) if old.get("month_key") == month_key else 0,
            "month_key": month_key,
            "scanning": parsed["scanning"],
            "seen": time.time(),
            "session": int(old.get("session") or 0) if session is None else session,
            "hits": max(int(old.get("hits") or 0), hits if hits is not None else 0),
            "ip": parsed["ip"] or str(old.get("ip") or ""),
        }
        try:
            save_activity_file(users)
        except Exception as e:
            print(f"[relay] activity: {type(e).__name__}: {e}")
        try:
            await self._publish_activity()
        except Exception as e:
            print(f"[relay] board: {type(e).__name__}: {e}")
        try:
            await self._upsert_person_card(int(oid))
        except Exception as e:
            print(f"[relay] card: {type(e).__name__}: {e}")

    async def _send_relay(self, line: str) -> bool:
        if self._is_directory():
            return False
        thread = await self._ledger_thread()
        if thread is None:
            print("[relay] ledger thread is not open yet")
            return False
        try:
            await thread.send(
                content=f"<@{HOST_BOT_ID}> {line}"[:2000],
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=HOST_BOT_ID)]),
            )
            return True
        except Exception as e:
            print(f"[relay] {type(e).__name__}: {e}")
            return False

    async def _public_ip(self) -> str:
        cached = str(getattr(self, "_ip_cache", "") or "")
        if cached and time.time() - float(getattr(self, "_ip_cache_at", 0) or 0) < 600:
            return cached
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.ipify.org",
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        ip = (await resp.text()).strip()
                        if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", ip):
                            self._ip_cache = ip
                            self._ip_cache_at = time.time()
                            return ip
        except Exception as e:
            print(f"[relay] ip: {type(e).__name__}")
        return cached

    async def _send_stat(self) -> None:
        if self._is_directory():
            return
        channel = self._claim_channel()
        if channel is None:
            return
        oid, name, scans, session, hits = self._local_live()
        if not str(oid).isdigit() or oid == "0":
            return
        safe_name = name.replace(":", " ").replace("\n", " ")[:40]
        flag = "1" if self.scanning else "0"
        ip = await self._public_ip()
        if not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", ip or ""):
            ip = "0.0.0.0"
        await self._send_relay(f"stat:{oid}:{scans}:{flag}:{session}:{hits}:{ip}:{safe_name}")

    async def _drop_my_boards(self) -> None:
        """Notifier bots do not keep their own activity or leaderboard posts."""
        if self._is_directory():
            return
        places = [self._claim_channel(), self._named_channel("info"), await self._ledger_thread()]
        claim = self._claim_channel()
        if claim is not None:
            for thread in list(getattr(claim, "threads", []) or []):
                if thread.name == "Claims":
                    places.append(thread)
        for channel in places:
            if channel is None:
                continue
            try:
                async for msg in channel.history(limit=100):
                    if msg.author != self.user:
                        continue
                    title = (msg.embeds[0].title or "") if msg.embeds else ""
                    raw = msg.content or ""
                    relay = self._relay_text(msg)
                    age = (discord.utils.utcnow() - msg.created_at).total_seconds()
                    is_line = relay.startswith(("pulse:", "stat:", "recent:"))
                    is_board = (
                        title == "Scan activity"
                        or (title and "Scan leaderboard" in title)
                        or title.startswith("Logged in this month")
                    )
                    blank = not raw and not msg.embeds and not msg.components and not msg.attachments
                    public = getattr(channel, "name", "") != "ledger"
                    in_claim = getattr(channel, "name", "") == "claim"
                    if in_claim or is_board or (blank and public) or (is_line and (public or age > 90)):
                        await msg.delete()
            except Exception as e:
                print(f"[board] drop: {type(e).__name__}: {e}")

    async def _publish_leaderboard(self) -> None:
        if not self._is_directory():
            await self._send_stat()
            return
        channel = self._claim_channel()
        if channel is None:
            return
        oid, name, scans = self._local_scans()
        incoming = {}
        try:
            async for msg in self._relay_messages():
                content = self._relay_text(msg)
                if content.startswith("stat:") and msg.author.bot:
                    parsed = parse_stat(content)
                    if parsed is not None:
                        prev = incoming.get(parsed["id"], {"scans": 0, "name": parsed["name"]})
                        incoming[parsed["id"]] = {
                            "scans": max(int(prev["scans"]), int(parsed["scans"])),
                            "name": parsed["name"].strip() or prev["name"],
                        }
                        try:
                            await msg.delete()
                        except Exception:
                            pass
        except Exception as e:
            print(f"[board] read: {type(e).__name__}: {e}")
        existing = load_score_file()
        message = None
        if self.cfg.leaderboard_message_id:
            try:
                message = await channel.fetch_message(self.cfg.leaderboard_message_id)
            except Exception:
                message = None
        if message is None:
            try:
                async for msg in channel.history(limit=20):
                    title = msg.embeds[0].title if msg.embeds else ""
                    if msg.author == self.user and (
                        (msg.content or "").startswith("**Scan leaderboard**")
                        or (title and "Scan leaderboard" in title)
                    ):
                        message = msg
                        self.cfg.leaderboard_message_id = msg.id
                        self.cfg.save()
                        break
            except Exception as e:
                print(f"[board] find: {type(e).__name__}: {e}")
        if message is not None:
            for uid, row in parse_leaderboard(message.content or "").items():
                prev = existing.get(uid, {"scans": 0, "name": row["name"]})
                existing[uid] = {
                    "scans": max(int(prev["scans"]), int(row["scans"])),
                    "name": row["name"] or prev["name"],
                }
        for uid, row in incoming.items():
            prev = existing.get(uid, {"scans": 0, "name": row["name"]})
            existing[uid] = {
                "scans": max(int(prev["scans"]), int(row["scans"])),
                "name": row["name"] or prev["name"],
            }
        scores = merge_scores(existing, oid, name, scans)
        try:
            save_score_file(scores)
        except Exception as e:
            print(f"[board] save: {type(e).__name__}: {e}")
        embed = leaderboard_embed(remaining_scores(scores))
        await self._clear_thread_leaderboard(channel)
        if message is not None and message.channel.id != channel.id:
            message = None
        try:
            if message is not None and message.author == self.user:
                await message.edit(content="", embed=embed)
            else:
                message = await channel.send(embed=embed)
            self.cfg.leaderboard_message_id = message.id
            self.cfg.save()
            await self._publish_bank()
        except Exception as e:
            print(f"[board] post: {type(e).__name__}: {e}")

    async def _clear_thread_leaderboard(self, channel: discord.TextChannel) -> None:
        thread = discord.utils.get(list(getattr(channel, "threads", []) or []), name="Claims")
        if thread is None:
            return
        ids = []
        if self.cfg.leaderboard_message_id:
            ids.append(self.cfg.leaderboard_message_id)
        for mid in ids:
            try:
                msg = await thread.fetch_message(mid)
                if msg.author == self.user:
                    await msg.delete()
            except Exception:
                pass
        try:
            async for msg in thread.history(limit=20):
                title = msg.embeds[0].title if msg.embeds else ""
                if msg.author == self.user and title and "Scan leaderboard" in title:
                    await msg.delete()
        except Exception as e:
            print(f"[board] thread: {type(e).__name__}: {e}")

    def _named_channel(self, name: str):
        want = name.lower().replace("_", "-")
        for guild in self.guilds:
            if not guild_allowed(guild.name):
                continue
            for ch in getattr(guild, "text_channels", []):
                if ch.name.lower().replace("_", "-") == want:
                    return ch
        return None

    def _local_activity_row(self) -> dict:
        oid, name, scans = self._local_scans()
        now = datetime.now(LOCAL_TZ)
        day_key = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")
        day = month = 0
        try:
            if os.path.isfile("contrib.json"):
                saved = json.loads(open("contrib.json", encoding="utf-8").read())
                if str(saved.get("id")) == oid:
                    if saved.get("day_key") == day_key:
                        day = int(saved.get("day") or 0)
                    if saved.get("month_key") == month_key:
                        month = int(saved.get("month") or 0)
                    name = str(saved.get("name") or name)
        except Exception:
            pass
        return {
            "id": oid,
            "name": name,
            "scanning": bool(self.scanning),
            "day": day,
            "month": month,
            "all": scans,
            "day_key": day_key,
            "month_key": month_key,
        }

    async def _allow_bot(self, channel) -> None:
        me = channel.guild.me if channel.guild else None
        if me is None:
            return
        try:
            await channel.set_permissions(
                me,
                view_channel=True,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
            )
        except Exception as e:
            print(f"[perms] #{channel.name}: {type(e).__name__}: {e}")

    async def _send_pulse(self) -> None:
        channel = self._claim_channel()
        if channel is None:
            return
        row = self._local_activity_row()
        if not str(row["id"]).isdigit() or row["id"] == "0":
            return
        safe = str(row["name"]).replace(":", " ").replace("\n", " ")[:40]
        flag = "1" if row["scanning"] else "0"
        await self._send_relay(
            f"pulse:{row['id']}:{flag}:{row['day']}:{row['month']}:{row['all']}:"
            f"{row['day_key']}:{row['month_key']}:{safe}"
        )

    async def _publish_activity(self) -> None:
        if not self._is_directory():
            await self._send_pulse()
            return
        channel = await self._info_channel()
        if channel is None:
            return
        await self._allow_bot(channel)
        users = load_activity_file()
        pulses = []
        try:
            async for msg in self._relay_messages():
                row = parse_pulse(self._relay_text(msg))
                if row is None or not msg.author.bot:
                    continue
                pulses.append((msg, row))
        except Exception as e:
            print(f"[activity] read: {type(e).__name__}: {e}")
        message = None
        if self.cfg.stats_message_id:
            try:
                message = await channel.fetch_message(self.cfg.stats_message_id)
            except Exception:
                message = None
        if message is None:
            try:
                async for msg in channel.history(limit=30):
                    title = msg.embeds[0].title if msg.embeds else ""
                    if msg.author == self.user and title == "Scan activity":
                        message = msg
                        break
            except Exception as e:
                print(f"[activity] find: {type(e).__name__}: {e}")
        if message is not None and message.author != self.user:
            message = None
        for msg, row in pulses:
            merge_activity(users, row, msg.created_at.timestamp())
            try:
                await msg.delete()
            except Exception:
                pass
        local = self._local_activity_row()
        if str(local["id"]).isdigit() and local["id"] != "0":
            merge_activity(users, local, time.time())
        try:
            save_activity_file(users)
        except Exception as e:
            print(f"[activity] save: {type(e).__name__}: {e}")
        embed = activity_embed(users)
        try:
            if message is not None:
                await message.edit(
                    content="",
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                if self.cfg.stats_message_id != message.id:
                    self.cfg.stats_message_id = message.id
                    self.cfg.save()
            else:
                sent = await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self.cfg.stats_message_id = sent.id
                self.cfg.save()
            if message is None:
                message = sent
            if message is not None and not message.pinned:
                try:
                    await message.pin()
                except Exception:
                    pass
        except Exception as e:
            print(f"[activity] post: {type(e).__name__}: {e}")

    async def _updates_channel(self):
        found = self._named_channel("updates")
        if found is not None:
            await self._allow_bot(found)
            return found
        guild = next((g for g in self.guilds if guild_allowed(g.name)), None)
        if guild is None:
            return None
        info = self._named_channel("info")
        try:
            created = await guild.create_text_channel(
                "updates",
                category=info.category if info is not None else None,
                topic="Exotic Notifier versions. New updates post here on their own.",
                reason="Update log",
            )
            print(f"[updates] Created #{created.name}.")
        except Exception as e:
            print(f"[updates] Need a #updates channel: {type(e).__name__}: {e}")
            return None
        await self._allow_bot(created)
        return created

    async def _publish_updates(self) -> None:
        channel = await self._updates_channel()
        if channel is None:
            return
        posted = set()
        try:
            async for msg in channel.history(limit=100):
                title = msg.embeds[0].title if msg.embeds else ""
                for entry in CHANGELOG:
                    if title.startswith(f"{entry['version']}  "):
                        posted.add(entry["version"])
        except Exception as e:
            print(f"[updates] read: {type(e).__name__}: {e}")
            return
        for entry in CHANGELOG:
            if entry["version"] in posted:
                continue
            try:
                await channel.send(
                    embed=changelog_embed(entry),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                posted.add(entry["version"])
                print(f"[updates] Posted {entry['version']}.")
            except Exception as e:
                print(f"[updates] post: {type(e).__name__}: {e}")
                break

    async def _leaderboard_loop(self) -> None:
        await asyncio.sleep(8)
        last_board = 0.0
        while not self.ending_session:
            if await self._stop_if_revoked():
                return
            try:
                if self._is_directory():
                    await self._publish_activity()
                    await self._drain_recent_lines()
                    await self._sweep_stray_recents()
                else:
                    if self.scanning and time.time() - last_board >= 20:
                        await self._send_stat()
                        last_board = time.time()
                    await self._drop_my_boards()
            except Exception as e:
                print(f"[activity] {type(e).__name__}: {e}")
            now = time.time()
            if self._is_directory() and now - last_board >= 300:
                try:
                    await self._publish_leaderboard()
                except Exception as e:
                    print(f"[board] {type(e).__name__}: {e}")
                last_board = now
            await asyncio.sleep(60)

    async def on_message(self, message: discord.Message):
        if (
            self._is_directory()
            and message.author.bot
            and message.author != self.user
            and getattr(message.channel, "name", "") == "ledger"
        ):
            text = self._relay_text(message)
            print(
                f"[relay] {message.author} embeds={len(message.embeds)} "
                f"content={bool(message.content)} line={text[:24]!r}"
            )
            if text.startswith(("pulse:", "stat:")):
                await self._ingest_relay(message, text)
            elif text.startswith("claim:"):
                await self._publish_relay_claim(text)
                return
            elif text.startswith("recent:"):
                await self._take_recent_line(message)
                return
        if (
            self._is_directory()
            and message.author.bot
            and message.author != self.user
            and (message.content or "").startswith("spend:")
        ):
            await self._on_spend(message)
            return
        if (
            self._is_directory()
            and message.author.bot
            and self._relay_text(message).startswith("recent:")
        ):
            await self._take_recent_line(message)
            return
        claim = self._claim_channel()
        if (
            claim is not None
            and message.guild is not None
            and message.channel.id == claim.id
            and not message.author.bot
        ):
            try:
                await message.delete()
            except Exception:
                pass
        await self.process_commands(message)

    def _is_directory(self) -> bool:
        return not (os.getenv("EXOTIC_OWNER_ID") or "").strip().isdigit()

    def access_revoked(self, user_id: int) -> bool:
        return operator_revoked(user_id)

    def remember_operator(self, user_id: int, name: str, bot_id: str, bot_name: str) -> None:
        data = load_operators()
        row = data["users"].get(str(user_id), {})
        row.update({
            "name": name,
            "bot_id": str(bot_id or row.get("bot_id") or ""),
            "bot_name": bot_name or row.get("bot_name") or "",
            "revoked": False,
            "at": int(time.time()),
        })
        data["users"][str(user_id)] = row
        save_operators(data)

    def _admin_text(self) -> str:
        return (
            "**Admin**\n"
            f"Account: <@{ADMIN_ID}>\n"
            f"Player list: `{DATABASE_FILE}`\n"
            f"**{len(self.db)}** players, **{len(self.players_to_scan)}** queued.\n"
            "Pick a member for their card. It shows IP, scans this session, and online hits.\n"
            "**Reset account** clears that person so they can run `/adduser` again.\n"
            "Full claims are DMed to you. You do not have to press Claim."
        )

    def _person_state(self, user_id: str, row: dict, act: dict) -> tuple[str, int]:
        if row.get("revoked"):
            return "Revoked", 0xED4245
        fresh = float(act.get("seen") or 0) >= time.time() - 180
        if act.get("scanning") and fresh:
            return "Scanning", 0x57F287
        if row:
            return "Idle", 0xFEE75C
        return "No scanner", 0x99AAB5

    def _person_embed(self, user_id: int) -> discord.Embed:
        uid = str(user_id)
        row = (load_operators().get("users") or {}).get(uid) or {}
        act = load_activity_file().get(uid) or {}
        score = load_score_file().get(uid) or {}
        name = row.get("name") or act.get("name") or uid
        status, color = self._person_state(uid, row, act)
        seen = float(act.get("seen") or 0)
        when = (
            datetime.fromtimestamp(seen, LOCAL_TZ).strftime("%b %d, %I:%M %p PT")
            if seen
            else "No report yet"
        )
        ip = act.get("ip") or ""
        if not ip or ip == "0.0.0.0":
            ip = "Waiting for the scanner"
        bot_name = row.get("bot_name") or "Not saved"
        embed = discord.Embed(title=str(name), description=f"<@{uid}>", color=color)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Bot", value=bot_name, inline=True)
        embed.add_field(name="IP", value=f"`{ip}`", inline=True)
        embed.add_field(
            name="Scans this session",
            value=f"**{int(act.get('session') or 0):,}**",
            inline=True,
        )
        embed.add_field(
            name="Online hits",
            value=f"**{int(act.get('hits') or 0):,}**",
            inline=True,
        )
        embed.add_field(
            name="Scans all time",
            value=f"**{int(score.get('scans') or act.get('all') or 0):,}**",
            inline=True,
        )
        embed.add_field(name="Last report", value=when, inline=False)
        embed.set_footer(text="Session scans and hits update while their scanner is running")
        return embed

    def _admin_embed(self) -> discord.Embed:
        users = load_operators().get("users") or {}
        activity = load_activity_file()
        rows = sorted(
            users.items(),
            key=lambda item: int((item[1] or {}).get("at") or 0),
            reverse=True,
        )
        embed = discord.Embed(
            title="Scanners",
            description="Pick someone below. Their card stays in this thread and updates while they scan.",
            color=0x5865F2,
        )
        if not rows:
            embed.description = "Nobody has set up a scanner yet."
        for uid, row in rows[:12]:
            row = row or {}
            act = activity.get(str(uid)) or {}
            status, _color = self._person_state(str(uid), row, act)
            ip = act.get("ip") or "—"
            if ip == "0.0.0.0":
                ip = "—"
            embed.add_field(
                name=str(row.get("name") or uid)[:80],
                value=(
                    f"<@{uid}> · {status}\n"
                    f"Session **{int(act.get('session') or 0):,}** · "
                    f"Hits **{int(act.get('hits') or 0):,}**\n"
                    f"`{ip}`"
                ),
                inline=True,
            )
        revoked = sum(1 for _uid, row in users.items() if (row or {}).get("revoked"))
        embed.set_footer(text=f"{len(users)} people · {revoked} revoked")
        return embed

    async def _admin_member(self):
        for guild in self.guilds:
            if not guild_allowed(guild.name):
                continue
            member = guild.get_member(ADMIN_ID)
            if member is None:
                try:
                    member = await guild.fetch_member(ADMIN_ID)
                except Exception:
                    member = None
            if member is not None:
                return member
        return None

    async def _admin_thread(self):
        manage = await self._manage_channel()
        member = await self._admin_member()
        if manage is None or member is None:
            return None
        data = load_operators()
        thread = None
        saved = data.get("admin_thread_id")
        if saved:
            try:
                fetched = await self.fetch_channel(int(saved))
                if isinstance(fetched, discord.Thread):
                    thread = fetched
            except Exception:
                thread = None
        if thread is None:
            try:
                thread = await manage.create_thread(
                    name="Admin",
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=10080,
                )
            except Exception:
                try:
                    thread = await manage.create_thread(
                        name="Admin",
                        type=discord.ChannelType.private_thread,
                        invitable=False,
                        auto_archive_duration=4320,
                    )
                except Exception as e:
                    print(f"[admin] thread: {type(e).__name__}: {e}")
                    return None
            data["admin_thread_id"] = thread.id
            save_operators(data)
        if thread.archived:
            try:
                await thread.edit(archived=False)
            except Exception:
                pass
        try:
            await thread.add_user(member)
        except Exception as e:
            print(f"[admin] add user: {type(e).__name__}: {e}")
        return thread

    async def _upsert_person_card(self, user_id: int, *, create: bool = False) -> None:
        if not self._is_directory():
            return
        panels = getattr(self, "_person_panels", None)
        if panels is None:
            panels = {}
            self._person_panels = panels
        ref = panels.get(str(user_id))
        if not ref:
            saved = (load_operators().get("users") or {}).get(str(user_id)) or {}
            channel_id = saved.get("card_channel_id")
            message_id = saved.get("card_message_id")
            if str(channel_id).isdigit() and str(message_id).isdigit():
                ref = (int(channel_id), int(message_id))
        message = None
        if ref:
            try:
                channel = self.get_channel(ref[0]) or await self.fetch_channel(ref[0])
                message = await channel.fetch_message(ref[1])
            except Exception:
                message = None
        if message is None and not create:
            return
        thread = await self._admin_thread()
        if thread is None and message is None:
            return
        embed = self._person_embed(int(user_id))
        try:
            if message is not None:
                await message.edit(embed=embed)
            else:
                message = await thread.send(embed=embed)
            panels[str(user_id)] = (message.channel.id, message.id)
            data = load_operators()
            row = (data.get("users") or {}).get(str(user_id))
            if isinstance(row, dict):
                row["card_channel_id"] = message.channel.id
                row["card_message_id"] = message.id
                data["users"][str(user_id)] = row
                save_operators(data)
        except Exception as e:
            print(f"[admin] card: {type(e).__name__}: {e}")

    def _forget_operator_stats(self, user_id: int) -> None:
        uid = str(user_id)
        scores = load_score_file()
        if uid in scores:
            scores.pop(uid, None)
            save_score_file(scores)
        users = load_activity_file()
        if uid in users:
            users.pop(uid, None)
            save_activity_file(users)
        bank = load_bank()
        spent = bank.get("spent") or {}
        if uid in spent:
            spent.pop(uid, None)
            bank["spent"] = spent
            save_bank(bank)

    async def reset_operator(self, user_id: int, name: str = "") -> str:
        if int(user_id) == ADMIN_ID:
            return "The admin account stays."
        notes = []
        data = load_operators()
        row = data["users"].pop(str(user_id), None) or {}
        save_operators(data)
        card_channel = row.get("card_channel_id")
        card_message = row.get("card_message_id")
        if str(card_channel).isdigit() and str(card_message).isdigit():
            try:
                channel = self.get_channel(int(card_channel)) or await self.fetch_channel(int(card_channel))
                message = await channel.fetch_message(int(card_message))
                await message.delete()
            except Exception:
                pass
        username = row.get("name") or name or str(user_id)
        bot_id = str(row.get("bot_id") or "")
        mapped = self._thread_map()
        tid = mapped.pop(str(user_id), None)
        self._save_thread_map(mapped)
        if tid:
            try:
                fetched = await self.fetch_channel(int(tid))
                if isinstance(fetched, discord.Thread):
                    await fetched.delete(reason="Account reset")
                    notes.append("closed their thread")
            except Exception as e:
                notes.append(f"thread: {type(e).__name__}")
        for guild in self.guilds:
            if not guild_allowed(guild.name):
                continue
            member = None
            try:
                member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
            except Exception:
                member = None
            role_name = (
                self._owner_role_name(member)
                if member is not None
                else self._role_name_from_username(username)
            )
            role = discord.utils.get(guild.roles, name=role_name)
            if role is not None:
                try:
                    await role.delete(reason="Account reset")
                    notes.append(f"removed role {role_name}")
                except Exception as e:
                    notes.append(f"role: {type(e).__name__}")
            if member is not None:
                for role_name in ("claim", "Scanner invite"):
                    access = discord.utils.find(
                        lambda r, n=role_name: r.name.lower() == n.lower(),
                        guild.roles,
                    )
                    if access is None or access not in member.roles:
                        continue
                    try:
                        await member.remove_roles(access, reason="Account reset")
                    except Exception as e:
                        print(f"[admin] reset role: {type(e).__name__}: {e}")
            if bot_id.isdigit() and (self.user is None or int(bot_id) != self.user.id):
                try:
                    bot_member = guild.get_member(int(bot_id)) or await guild.fetch_member(int(bot_id))
                    await guild.kick(bot_member, reason="Account reset")
                    notes.append("kicked their bot")
                except Exception as e:
                    notes.append(f"bot: {type(e).__name__}")
        self._forget_operator_stats(int(user_id))
        panels = getattr(self, "_person_panels", None) or {}
        panels.pop(str(user_id), None)
        await self._publish_revoked()
        await self._refresh_admin_panel()
        extra = (". ".join(notes) + ". ") if notes else ""
        return (
            f"Reset <@{user_id}>. {extra}"
            "Close the scanner window on that PC, then they can run `/adduser` again."
        )

    async def _refresh_admin_panel(self) -> None:
        if not self._is_directory():
            return
        thread = await self._admin_thread()
        if thread is None:
            return
        view = AdminPanelView()
        try:
            async for msg in thread.history(limit=15):
                if msg.author == self.user and (msg.content or "").startswith("**Admin**"):
                    await msg.edit(content=self._admin_text(), embed=self._admin_embed(), view=view)
                    return
            await thread.send(self._admin_text(), embed=self._admin_embed(), view=view)
        except Exception as e:
            print(f"[admin] panel: {type(e).__name__}: {e}")

    async def _post_admin_panel(self) -> None:
        if not self._is_directory():
            return
        await self._refresh_admin_panel()
        print("[admin] Panel ready.")

    async def _publish_revoked(self) -> None:
        if not self._is_directory():
            return
        thread = await self._ledger_thread()
        channel = thread
        if channel is None:
            return
        ids = sorted(
            uid for uid, row in (load_operators().get("users") or {}).items()
            if str(uid).isdigit() and (row or {}).get("revoked") and int(uid) != ADMIN_ID
        )
        text = REVOKED_MARKER + ",".join(ids)
        data = load_operators()
        message = None
        saved = data.get("revoked_message_id")
        if saved:
            try:
                message = await channel.fetch_message(int(saved))
            except Exception:
                message = None
        if message is None:
            try:
                async for msg in channel.history(limit=30):
                    if msg.author == self.user and (msg.content or "").startswith(REVOKED_MARKER):
                        message = msg
                        break
            except Exception as e:
                print(f"[admin] revoke read: {type(e).__name__}: {e}")
        try:
            if message is not None:
                await message.edit(content=text)
            else:
                message = await channel.send(text)
            data["revoked_message_id"] = message.id
            save_operators(data)
            await self._tuck_ledger(channel)
        except Exception as e:
            print(f"[admin] revoke post: {type(e).__name__}: {e}")

    async def _revoked_owner_ids(self) -> set[str]:
        places = []
        thread = await self._ledger_thread()
        if thread is not None:
            places.append(thread)
        claim = self._claim_channel()
        if claim is not None:
            places.append(claim)
        for channel in places:
            try:
                async for msg in channel.history(limit=30):
                    content = msg.content or ""
                    if msg.author.bot and content.startswith(REVOKED_MARKER):
                        return {part for part in content.split(":", 1)[1].split(",") if part.isdigit()}
            except Exception as e:
                print(f"[admin] revoke check: {type(e).__name__}: {e}")
        return set()

    async def _stop_if_revoked(self) -> bool:
        if self._is_directory():
            return False
        oid = (os.getenv("EXOTIC_OWNER_ID") or "").strip()
        if not oid.isdigit():
            return False
        if oid not in await self._revoked_owner_ids():
            return False
        print("[Bot] Access revoked. Scanner off.")
        self.scanning = False
        self.keep_scanning = False
        self.ending_session = True
        for guild in list(self.guilds):
            try:
                await guild.leave()
            except Exception:
                pass
        os._exit(0)

    async def _search_members(self, guild, query: str) -> list:
        token = (os.getenv("DISCORD_TOKEN") or "").strip()
        query = (query or "").strip()
        if not token or len(query) < 2:
            return []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://discord.com/api/v10/guilds/{guild.id}/members/search",
                    params={"query": query[:32], "limit": 10},
                    headers={
                        "Authorization": f"Bot {token}",
                        "User-Agent": "DiscordBot (https://github.com/local/exotic, 1.0)",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[admin] bot search: {type(e).__name__}: {e}")
            return []

    async def _find_scanner_member(self, guild, user_id: int, name: str, bot_id: str = ""):
        if str(bot_id).isdigit() and self.user is not None and int(bot_id) != self.user.id:
            try:
                return await guild.fetch_member(int(bot_id))
            except Exception:
                pass
        from distributor import notifier_names
        wanted = {item.lower() for item in notifier_names(name or "", int(user_id))}
        if name:
            wanted.add(str(name).lower())
        queries = []
        for item in wanted:
            head = re.split(r"[-.]", item, maxsplit=1)[0]
            for candidate in (head, item):
                candidate = candidate.strip()
                if len(candidate) >= 2 and candidate not in queries:
                    queries.append(candidate)
        for query in queries[:4]:
            for row in await self._search_members(guild, query):
                user = row.get("user") or {}
                if not user.get("bot"):
                    continue
                username = (user.get("username") or "").lower()
                if self.user is not None and int(user.get("id") or 0) == self.user.id:
                    continue
                if username not in wanted:
                    continue
                try:
                    return await guild.fetch_member(int(user["id"]))
                except Exception:
                    return None
        return None

    async def _scanner_role(self, guild: discord.Guild):
        role = discord.utils.get(guild.roles, name="scanner")
        if role is not None:
            return role
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return None
        try:
            return await guild.create_role(
                name="scanner",
                permissions=discord.Permissions.none(),
                hoist=False,
                mentionable=False,
                reason="Hides scanner bots in the member list",
            )
        except Exception as e:
            print(f"[bots] role: {type(e).__name__}: {e}")
            return None

    async def _cloak_member(self, member: discord.Member) -> None:
        if not self._is_directory() or member.bot is False:
            return
        role = await self._scanner_role(member.guild)
        if role is None or role in member.roles:
            return
        await member.add_roles(role, reason="Hide scanner bot from the member list")

    async def _open_manage_channel(self) -> None:
        """Members need to see #mange-your-noti and the messages already in it."""
        if not self._is_directory():
            return
        manage = await self._manage_channel()
        if manage is None:
            return
        try:
            await manage.set_permissions(
                manage.guild.default_role,
                overwrite=discord.PermissionOverwrite(**self._channel_access(manage.guild, send=False)),
                reason="Everyone can see this channel. Threads stay on each person's role.",
            )
            print(f"[manage] #{manage.name} is visible to members.")
        except Exception as e:
            print(f"[manage] open: {type(e).__name__}: {e}")

    async def _cloak_scanners(self) -> None:
        if not self._is_directory():
            return
        for guild in list(self.guilds):
            if not guild_allowed(guild.name):
                continue
            role = await self._scanner_role(guild)
            if role is None:
                continue
            manage = await self._manage_channel()
            claim = self._claim_channel()
            keep_ids = set()
            if manage is not None and manage.guild.id == guild.id:
                keep_ids.add(manage.id)
            if claim is not None and claim.guild.id == guild.id:
                keep_ids.add(claim.id)
            for channel in list(guild.channels):
                try:
                    if channel.id in keep_ids:
                        await channel.set_permissions(
                            role,
                            view_channel=True,
                            send_messages=True,
                            embed_links=True,
                            attach_files=True,
                            read_message_history=True,
                            use_application_commands=True,
                            send_messages_in_threads=True,
                            reason="Scanner bot can be used inside its owner's thread",
                        )
                    else:
                        await channel.set_permissions(
                            role,
                            view_channel=False,
                            reason="Hide scanner bots from the member list",
                        )
                except Exception as e:
                    print(f"[bots] {channel.name}: {type(e).__name__}: {e}")
            for row in (load_operators().get("users") or {}).values():
                bot_id = str((row or {}).get("bot_id") or "")
                if not bot_id.isdigit() or self.user is None or int(bot_id) == self.user.id:
                    continue
                try:
                    member = guild.get_member(int(bot_id)) or await guild.fetch_member(int(bot_id))
                except Exception:
                    continue
                await self._cloak_member(member)

    async def _ensure_relay(self) -> dict:
        if not self._is_directory():
            return {}
        data = load_relay()
        claim = self._claim_channel()
        if claim is None:
            return data
        thread = None
        saved = str(data.get("thread_id") or "")
        if saved.isdigit():
            try:
                fetched = await self.fetch_channel(int(saved))
                if isinstance(fetched, discord.Thread):
                    thread = fetched
            except Exception:
                thread = None
        if thread is None:
            for candidate in list(getattr(claim, "threads", []) or []):
                if candidate.name == "relay":
                    thread = candidate
                    break
        if thread is None:
            try:
                async for candidate in claim.archived_threads(private=True, joined=True, limit=20):
                    if candidate.name == "relay":
                        thread = candidate
                        break
            except Exception:
                pass
        if thread is None:
            try:
                thread = await claim.create_thread(
                    name="relay",
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=10080,
                )
            except Exception as e:
                print(f"[bots] relay thread: {type(e).__name__}")
                return data
        if thread.archived or getattr(thread, "locked", False):
            try:
                await thread.edit(archived=False, locked=False)
            except Exception:
                pass
        data["thread_id"] = thread.id
        if not data.get("webhook_url"):
            try:
                hook = await claim.create_webhook(name="relay")
                data["webhook_url"] = hook.url
                print("[bots] Claim relay ready.")
            except discord.Forbidden:
                print("[bots] Hiding bots from #claim needs Manage Webhooks on phonktester.")
            except Exception as e:
                print(f"[bots] relay: {type(e).__name__}")
        save_relay(data)
        return data

    async def _relay_loop(self) -> None:
        await asyncio.sleep(5)
        while not self.ending_session:
            try:
                await self._drain_relay()
            except Exception as e:
                print(f"[bots] relay read: {type(e).__name__}")
            await asyncio.sleep(5)

    async def _drain_relay(self) -> None:
        if not self._is_directory():
            return
        data = load_relay()
        url = str(data.get("webhook_url") or "")
        thread_id = str(data.get("thread_id") or "")
        if not url or not thread_id.isdigit():
            return
        try:
            thread = await self.fetch_channel(int(thread_id))
        except Exception:
            return
        if not isinstance(thread, discord.Thread):
            return
        claim = self._claim_channel()
        if claim is None:
            return
        async for message in thread.history(limit=15):
            content = message.content or ""
            if not content.startswith("hit:") or len(message.embeds) < 2:
                continue
            uuid = content.split(":", 2)[1]
            if len(uuid) < 32:
                continue
            full = message.embeds[1]
            hit = type("Hit", (), {})()
            hit.player_uuid = uuid
            hit.player_name = (full.title or "Unknown").lstrip("★ ").strip() or "Unknown"
            mention = self._claim_mention(claim) or ""
            content = f"{mention}\nA claim costs **{CLAIM_COST}** scans.".strip()
            thread = await self._claims_thread(claim)
            dest = thread or claim
            try:
                await dest.send(
                    content=content,
                    embed=message.embeds[0],
                    view=ClaimView(hit, full),
                    allowed_mentions=discord.AllowedMentions(roles=True, everyone=False),
                )
                if thread is not None:
                    await self._fill_claims_thread(thread)
            except Exception as e:
                print(f"[bots] relay post: {type(e).__name__}")
                continue
            try:
                async with aiohttp.ClientSession() as session:
                    await session.delete(
                        f"{url}/messages/{message.id}",
                        params={"thread_id": thread_id},
                        timeout=aiohttp.ClientTimeout(total=15),
                    )
            except Exception:
                try:
                    await message.delete()
                except Exception:
                    pass

    async def _relay_hit(self, exotic, pub: discord.Embed, full: discord.Embed) -> bool:
        url = (os.getenv("EXOTIC_CLAIM_WEBHOOK") or "").strip()
        thread_id = (os.getenv("EXOTIC_RELAY_THREAD") or "").strip()
        if not url or not thread_id.isdigit():
            return False
        payload = {
            "content": f"hit:{exotic.player_uuid}",
            "embeds": [pub.to_dict(), full.to_dict()],
            "allowed_mentions": {"parse": []},
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    params={"thread_id": thread_id},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status not in (200, 204):
                        print(f"[bots] relay send: {resp.status}")
                        return False
            return True
        except Exception as e:
            print(f"[bots] relay send: {type(e).__name__}")
            return False

    async def _silence_bot(self, bot_id: str, user_id: int = 0, name: str = "") -> str:
        notes = []
        for guild in self.guilds:
            if not guild_allowed(guild.name):
                continue
            member = await self._find_scanner_member(guild, user_id, name, bot_id)
            if member is None:
                return "no scanner bot saved"
            if user_id:
                data = load_operators()
                row = data["users"].get(str(user_id), {})
                row["bot_id"] = str(member.id)
                row["bot_name"] = member.name
                data["users"][str(user_id)] = row
                save_operators(data)
            channels = [self._claim_channel(), self._named_channel("info"), self._named_channel("updates")]
            noti = await self._resolve_channel(self.cfg.notification_channel_id)
            channels.append(noti)
            silenced = 0
            for channel in channels:
                if channel is None:
                    continue
                try:
                    await channel.set_permissions(
                        member,
                        view_channel=True,
                        send_messages=False,
                        add_reactions=False,
                        reason="Access revoked",
                    )
                    silenced += 1
                except Exception as e:
                    print(f"[admin] silence #{channel.name}: {type(e).__name__}: {e}")
            try:
                await guild.kick(member, reason="Access revoked")
                notes.append(f"kicked {member}")
            except discord.Forbidden:
                notes.append(
                    f"blocked posting in {silenced} channel(s). Their scanner shuts off on its next check."
                    if silenced
                    else "couldn't remove their bot"
                )
            except Exception as e:
                notes.append(f"kick failed: {type(e).__name__}")
        return "; ".join(notes) or "no server"

    async def _clear_bot_silence(self, bot_id: str) -> None:
        if not str(bot_id).isdigit() or self.user is None or int(bot_id) == self.user.id:
            return
        for guild in self.guilds:
            if not guild_allowed(guild.name):
                continue
            try:
                member = guild.get_member(int(bot_id)) or await guild.fetch_member(int(bot_id))
            except Exception:
                continue
            channels = [self._claim_channel(), self._named_channel("info"), self._named_channel("updates")]
            for channel in channels:
                if channel is None:
                    continue
                try:
                    await channel.set_permissions(member, overwrite=None, reason="Access restored")
                except Exception:
                    pass

    async def _strip_access_roles(self, user_id: int) -> str:
        removed = []
        for guild in self.guilds:
            if not guild_allowed(guild.name):
                continue
            try:
                member = guild.get_member(user_id) or await guild.fetch_member(user_id)
            except Exception:
                continue
            for role_name in ("claim", "Scanner invite"):
                role = discord.utils.find(lambda r, n=role_name: r.name.lower() == n.lower(), guild.roles)
                if role is None or role not in member.roles:
                    continue
                try:
                    await member.remove_roles(role, reason="Access revoked")
                    removed.append(role.name)
                except Exception as e:
                    print(f"[admin] role: {type(e).__name__}: {e}")
        return ", ".join(removed) if removed else "no roles to remove"

    async def revoke_access(self, user_id: int, name: str = "") -> str:
        if int(user_id) == ADMIN_ID:
            return "The admin account stays."
        data = load_operators()
        row = data["users"].get(str(user_id), {})
        row["name"] = name or row.get("name") or str(user_id)
        row["revoked"] = True
        row["at"] = int(row.get("at") or time.time())
        data["users"][str(user_id)] = row
        save_operators(data)
        await self._publish_revoked()
        roles = await self._strip_access_roles(int(user_id))
        bots = await self._silence_bot(str(row.get("bot_id") or ""), int(user_id), row.get("name") or name)
        return f"Revoked <@{user_id}>. Roles: {roles}. {bots}."

    async def restore_access(self, user_id: int, name: str = "") -> str:
        data = load_operators()
        row = data["users"].get(str(user_id), {})
        bot_id = str(row.get("bot_id") or "")
        row["name"] = name or row.get("name") or str(user_id)
        row["revoked"] = False
        data["users"][str(user_id)] = row
        save_operators(data)
        await self._publish_revoked()
        await self._clear_bot_silence(bot_id)
        return (
            f"Restored <@{user_id}>. "
            "They need `/adduser` again if their scanner already shut off."
        )

    async def _dm_admin_claim(self, exotic: PlayerExoticData, full: discord.Embed) -> None:
        member = await self._admin_member()
        if member is None:
            return
        ign = exotic.player_name or "Unknown"
        embed = discord.Embed.from_dict(full.to_dict())
        finder = self.user.name if self.user else "scanner"
        try:
            await member.send(
                content=f"Full claim from **{finder}** · **{ign}**. The public claim is still open.",
                embed=embed,
                view=player_actions_view(exotic.player_uuid, ign),
            )
            print(f"[admin] DMed full claim {ign}")
        except discord.Forbidden:
            thread = await self._admin_thread() if self._is_directory() else None
            if thread is not None:
                try:
                    await thread.send(
                        content=f"DMs are closed. Full claim · **{ign}**.",
                        embed=embed,
                        view=player_actions_view(exotic.player_uuid, ign),
                    )
                except Exception as e:
                    print(f"[admin] claim thread: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"[admin] claim DM: {type(e).__name__}: {e}")

    async def _publish_relay_claim(self, text: str) -> None:
        parts = text.split(":", 2)
        if len(parts) < 2:
            return
        uid = parts[1].replace("-", "").lower()
        if len(uid) != 32:
            return
        exotic = self.db.get(uid)
        if exotic is None:
            print(f"[claim] relay miss {uid[:8]}")
            return
        if len(parts) > 2 and parts[2].strip():
            exotic.player_name = parts[2].strip()
        await self._post_public_claim(exotic, None)

    async def _post_public_claim(self, exotic: PlayerExoticData, profile: Optional[Sk1erProfile]) -> None:
        claim = self._claim_channel()
        if claim is None:
            return
        pub = self._public_embed(exotic, profile)
        uid = (exotic.player_uuid or "").replace("-", "").lower()
        pub.set_footer(text=f"claim:{uid}")
        mention = self._claim_mention(claim) or ""
        content = f"{mention}\nA claim costs **{CLAIM_COST}** scans.".strip()
        thread = discord.utils.get(list(claim.threads), name="Claims")
        if thread is None:
            thread = await self._claims_thread(claim)
        dest = thread or claim
        try:
            await dest.send(
                content=content,
                embed=pub,
                view=ClaimBuyView(),
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=False),
            )
        except Exception as e:
            print(f"[claim] thread post: {type(e).__name__}: {e}")
            return
        if thread is not None:
            await self._fill_claims_thread(thread)
            await self._quiet_claims_thread(thread)

    async def _notify(self, exotic: PlayerExoticData,
                      profile: Optional[Sk1erProfile] = None):
        if await self._stop_if_revoked():
            return
        claim = self._claim_channel()
        ch = claim or await self._resolve_channel(self.cfg.notification_channel_id)
        if not ch:
            return
        pub = self._public_embed(exotic, profile)
        full = self._full_embed(exotic, profile)
        try:
            await self._dm_admin_claim(exotic, full)
        except Exception as e:
            print(f"[admin] claim copy: {type(e).__name__}: {e}")
        if not self._is_directory():
            uid = (exotic.player_uuid or "").replace("-", "").lower()
            ign = (exotic.player_name or "Unknown").replace(":", " ").replace("\n", " ")[:32]
            await self._send_relay(f"claim:{uid}:{ign}")
            return
        uid = (exotic.player_uuid or "").replace("-", "").lower()
        pub.set_footer(text=f"claim:{uid}")
        view = ClaimBuyView()
        mention = self._claim_mention(ch) or ""
        content = f"{mention}\nA claim costs **{CLAIM_COST}** scans.".strip()
        if not self._is_directory():
            sent = await self._relay_hit(exotic, pub, full)
            if sent:
                return
        thread = None
        if isinstance(ch, discord.TextChannel):
            thread = discord.utils.get(list(ch.threads), name="Claims")
            if thread is None and self._is_directory():
                thread = await self._claims_thread(ch)
        dest = thread or ch
        try:
            await dest.send(
                content=content,
                embed=pub,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=False),
            )
        except Exception as e:
            print(f"[claim] thread post: {type(e).__name__}: {e}")
            if dest is not ch:
                await ch.send(
                    content=content,
                    embed=pub,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(roles=True, everyone=False),
                )
        if thread is not None:
            await self._fill_claims_thread(thread)
            await self._quiet_claims_thread(thread)
        log_ch = await self._resolve_channel(self.cfg.log_channel_id)
        if log_ch and log_ch.id != ch.id:
            try:
                await log_ch.send(embed=full)
            except Exception as e:
                print(f"[log] {e}")

    async def _noti_channel(self):
        if not self._is_directory():
            return None
        return await self._resolve_channel(self.cfg.noti_channel_id)

    async def _info_channel(self):
        ch = await self._resolve_channel(self.cfg.info_channel_id)
        claim = self._claim_channel()
        if ch is not None and claim is not None and ch.id == claim.id:
            ch = None
            self.cfg.info_channel_id = None
            self.cfg.save()
        if ch:
            return ch
        for guild in self.guilds:
            for candidate in getattr(guild, "text_channels", []):
                name = candidate.name.lower().replace("_", "-")
                if name == "info" or name.endswith("-info") or name.startswith("info-"):
                    self.cfg.info_channel_id = candidate.id
                    self.cfg.save()
                    print(f"[info] Channel set to #{candidate.name}")
                    return candidate
        return None

    def _guess_noti_channel(self):
        return

    def _load_recent_queue(self) -> list:
        try:
            if os.path.isfile(RECENT_QUEUE_FILE):
                data = json.loads(open(RECENT_QUEUE_FILE, encoding="utf-8").read())
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def _save_recent_queue(self, rows: list) -> None:
        try:
            with open(RECENT_QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump(rows[-200:], f)
        except Exception as e:
            print(f"[noti] queue: {type(e).__name__}: {e}")

    def _queue_recent_row(self, row: dict) -> None:
        rows = self._load_recent_queue()
        key = (str(row.get("uuid") or ""), str(row.get("login") or ""))
        if any((str(r.get("uuid") or ""), str(r.get("login") or "")) == key for r in rows):
            return
        rows.append(row)
        self._save_recent_queue(rows)

    def _recent_embed_from_row(self, row: dict) -> discord.Embed:
        saved = row.get("embed")
        if isinstance(saved, dict) and saved.get("title"):
            return discord.Embed.from_dict(saved)
        ign = row.get("ign") or "Unknown"
        login = None
        try:
            login = datetime.strptime(str(row.get("login") or ""), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except Exception:
            login = None
        when = login.strftime("%b %d, %Y %H:%M UTC") if login else "unknown"
        exotic = self.db.get(str(row.get("uuid") or ""))
        if exotic is not None:
            embed = self._full_embed(exotic, Sk1erProfile(online=False, last_login=login, socials={}))
            ign = exotic.player_name or ign
            embed.title = f"Logged in this month  ·  {ign}"
            embed.description = f"Last login `{when}`\nItems below."
            return embed
        return discord.Embed(
            title=f"Logged in this month  ·  {ign}",
            description=f"Last login `{when}`",
            color=0x5865F2,
        )

    async def _host_noti_channel_id(self) -> Optional[int]:
        """Recent logins stay off until phonktester has published a channel."""
        if self._is_directory():
            return self.cfg.noti_channel_id
        now = time.time()
        if now < getattr(self, "_noti_gate_until", 0):
            return getattr(self, "_noti_gate_id", None)
        found = None
        try:
            async for msg in self._relay_messages(limit=25):
                content = self._relay_text(msg)
                if (
                    msg.author.bot
                    and msg.author != self.user
                    and content.startswith("notichannel:")
                    and content.split(":", 1)[1].isdigit()
                ):
                    found = int(content.split(":", 1)[1])
                    break
        except Exception as e:
            print(f"[noti] gate: {type(e).__name__}: {e}")
        self._noti_gate_id = found
        self._noti_gate_until = now + 60
        return found

    async def _publish_noti_gate(self) -> None:
        if not self._is_directory():
            return
        thread = await self._ledger_thread()
        if thread is None:
            return
        want = f"notichannel:{self.cfg.noti_channel_id}" if self.cfg.noti_channel_id else ""
        found = None
        try:
            async for msg in thread.history(limit=25):
                if msg.author == self.user and self._relay_text(msg).startswith("notichannel:"):
                    found = msg
                    break
        except Exception as e:
            print(f"[noti] gate read: {type(e).__name__}: {e}")
            return
        try:
            if not want:
                if found is not None:
                    await found.delete()
                return
            if found is None:
                await thread.send(
                    embed=discord.Embed(description=want),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            elif self._relay_text(found) != want:
                await found.edit(
                    content="",
                    embed=discord.Embed(description=want),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception as e:
            print(f"[noti] gate post: {type(e).__name__}: {e}")

    async def _forward_recent(self, exotic: PlayerExoticData, profile: Sk1erProfile) -> bool:
        if profile.last_login is None:
            return False
        stamp = profile.last_login.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
        ign = (exotic.player_name or "Unknown").replace(":", " ").replace("\n", " ")[:32]
        uid = (exotic.player_uuid or "").replace(":", "")
        return await self._send_relay(f"recent:{uid}:{stamp}:{ign}")

    async def _flush_recent(self) -> None:
        if not self._is_directory() or not self.cfg.noti_channel_id:
            return
        ch = await self._resolve_channel(self.cfg.noti_channel_id)
        if ch is None:
            return
        kept = []
        for row in self._load_recent_queue():
            try:
                await ch.send(
                    embed=self._recent_embed_from_row(row),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as e:
                print(f"[noti] flush: {type(e).__name__}: {e}")
                kept.append(row)
        self._save_recent_queue(kept)

    async def _take_recent_line(self, message: discord.Message) -> None:
        parts = self._relay_text(message).split(":", 3)
        if len(parts) < 3 or parts[0] != "recent":
            return
        self._queue_recent_row({
            "uuid": parts[1],
            "login": parts[2],
            "ign": parts[3] if len(parts) > 3 else "Unknown",
        })
        try:
            await message.delete()
        except Exception:
            pass
        await self._flush_recent()

    async def _drain_recent_lines(self) -> None:
        try:
            async for msg in self._relay_messages():
                if msg.author.bot and self._relay_text(msg).startswith("recent:"):
                    await self._take_recent_line(msg)
        except Exception as e:
            print(f"[noti] drain: {type(e).__name__}: {e}")

    async def _sweep_stray_recents(self) -> None:
        """Recent logins stay off public channels until /setnotichannel."""
        if not self._is_directory():
            return
        noti_id = self.cfg.noti_channel_id
        places = [self._claim_channel(), self._named_channel("info")]
        seen = set()
        for channel in places:
            if channel is None or channel.id in seen:
                continue
            if noti_id and channel.id == noti_id:
                continue
            seen.add(channel.id)
            try:
                async for msg in channel.history(limit=30):
                    if not msg.author.bot or msg.author == self.user:
                        continue
                    title = msg.embeds[0].title if msg.embeds else ""
                    if not title.startswith("Logged in this month"):
                        continue
                    row = {
                        "uuid": "",
                        "login": str(msg.id),
                        "ign": title,
                        "embed": msg.embeds[0].to_dict(),
                    }
                    try:
                        await msg.delete()
                    except Exception:
                        continue
                    self._queue_recent_row(row)
            except Exception as e:
                print(f"[noti] sweep: {type(e).__name__}: {e}")
        await self._flush_recent()

    async def _send_noti(self, embed: discord.Embed, title_prefix: str, exotic: PlayerExoticData):
        ch = await self._noti_channel()
        if not ch:
            print("[noti] No channel set. Use /setnotichannel in the logs channel.")
            return
        mention = self._dev_mention(ch)
        ign = exotic.player_name or "Unknown"
        try:
            await ch.send(
                content=mention or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            print(f"[noti] {title_prefix} {ign} → #{ch.name}")
        except Exception as e:
            print(f"[noti] {e}")

    async def _notify_recent(self, exotic: PlayerExoticData, profile: Sk1erProfile) -> bool:
        if not self._is_directory():
            if not await self._host_noti_channel_id():
                return False
            return await self._forward_recent(exotic, profile)
        if profile.last_login is None:
            return False
        if not self.cfg.noti_channel_id:
            self._queue_recent_row({
                "uuid": exotic.player_uuid or "",
                "login": profile.last_login.astimezone(timezone.utc).strftime("%Y%m%d%H%M"),
                "ign": exotic.player_name or "Unknown",
            })
            return False
        ch = await self._resolve_channel(self.cfg.noti_channel_id)
        if ch is None:
            return False
        embed = self._full_embed(exotic, profile)
        ign = exotic.player_name or "Unknown"
        when = profile.last_login.strftime("%b %d, %Y %H:%M UTC")
        embed.title = f"Logged in this month  ·  {ign}"
        embed.description = f"Last login `{when}`\nItems below."
        await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        print(f"[noti] Recent {ign} → #{ch.name}")
        return True

    async def _notify_info(self, exotic: PlayerExoticData, profile: Sk1erProfile):
        ign = exotic.player_name or "Unknown"
        ch = await self._info_channel()
        if not ch:
            print("[info] No info channel. Use /setinfochannel in the info channel.")
            return
        embed = self._full_embed(exotic, profile)
        embed.title = f"Discord linked  ·  {ign}"
        embed.description = f"Discord `{profile.discord}`"
        view = discord.ui.View(timeout=None)
        add_profile_link_buttons(view, exotic.player_uuid, exotic.player_name or "")
        mention = self._dev_mention(ch)
        try:
            await ch.send(
                content=mention or None,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            print(f"[info] {ign} dc={profile.discord} → #{ch.name}")
        except Exception as e:
            print(f"[info] {e}")

    async def _result_callback(self, uuid: str, name: str,
                               profile: Sk1erProfile, index: int, total: int):
        self.stats["checked"] += 1
        self._count_scan()
        self.stats["progress_idx"] = index
        self.stats["progress_total"] = total
        pct = (index / max(total, 1)) * 100
        display_name = name or uuid[:12]
        exotic = self.db.get(uuid)

        if exotic and (
            profile.online or profile.super_filtered or profile.discord
            or logged_in_within_month(profile)
        ):
            current_name = await resolve_current_name(uuid)
            if current_name:
                if exotic.player_name != current_name:
                    old = exotic.player_name or "?"
                    exotic.player_name = current_name
                    if old != current_name:
                        print(f"    [Name] {old} → {current_name}")
                display_name = current_name
            elif name:
                exotic.player_name = name
                display_name = name
        elif name and exotic and not exotic.player_name:
            exotic.player_name = name
            display_name = name

        if exotic and profile.super_filtered:
            self.stats["super_filtered"] += 1
            if self.super.add(exotic, profile):
                self.stats["super_new"] += 1
                print(f"  [{index}/{total}] SUPER {display_name} "
                      f"dc={profile.discord} "
                      f"login={profile.last_login}")

        if exotic and profile.discord and self.info_posted.should_post(
                uuid, profile.discord):
            try:
                await self._notify_info(exotic, profile)
                self.info_posted.mark(uuid, profile.discord)
                print(f"  [{index}/{total}] INFO {display_name} "
                      f"dc={profile.discord}")
            except Exception as e:
                print(f"    Info notify error: {e}")

        if exotic and profile.last_login and logged_in_within_month(profile):
            if self.recent.should_post(uuid, profile.last_login):
                try:
                    if await self._notify_recent(exotic, profile):
                        self.recent.mark(uuid, profile.last_login)
                        print(f"  [{index}/{total}] RECENT {display_name} "
                              f"login={profile.last_login}")
                except Exception as e:
                    print(f"    Recent notify error: {e}")

        if not profile.online:
            extra = ""
            if profile.discord:
                extra = f" dc={profile.discord}"
            print(f"  [{index}/{total}] ({pct:.1f}%) {display_name}{extra}")
            return
        if self.notified.is_notified(uuid):
            print(f"  [{index}/{total}] ({pct:.1f}%) "
                  f"{display_name} [already notified]")
            return
        if not exotic:
            return

        self.stats["online_found"] += 1
        self._count_hit()
        tag = " SUPER" if profile.super_filtered else ""
        print(f"  [{index}/{total}] ({pct:.1f}%) {display_name} "
              f"— {len(exotic.items)} items "
              f"[hit #{self.stats['online_found']}] [sk1er]{tag}")
        try:
            await self._notify(exotic, profile)
        except Exception as e:
            print(f"    Notify error: {e}")
        self.notified.mark_notified(uuid)

    async def _scan_loop(self):
        headless = is_headless()
        print(f"[Scanner] Starting Chrome "
              f"({'headless' if headless else 'minimized / background-safe'})...")
        profile = os.path.join(data_dir(), "chrome-profile")
        os.makedirs(profile, exist_ok=True)
        last_err = None
        for attempt in range(1, 4):
            try:
                kwargs = {
                    "headless": headless,
                    "browser_args": chrome_args(),
                    "user_data_dir": profile,
                }
                chrome_path = os.environ.get("EXOTIC_CHROME", "").strip()
                if chrome_path:
                    kwargs["browser_executable_path"] = chrome_path
                self.browser = await nd.start(**kwargs)
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"[Scanner] Browser start {attempt}/3 failed: {e}")
                await asyncio.sleep(4)
        if last_err or not self.browser:
            print(f"[Scanner] Browser failed: {last_err}")
            return
        print("[Scanner] Chrome is running even if you tab away or minimize it.")

        self.scan_start_time = datetime.now(timezone.utc)
        self.stats = self._blank_stats()
        self.scan_queue = asyncio.Queue()

        workers = []
        try:
            tab = await asyncio.wait_for(
                self.browser.get("about:blank"), timeout=30)
            await self._park_chrome(tab)
            workers.append(TabWorker(
                worker_id=1,
                tab=tab,
                queue=self.scan_queue,
                result_cb=self._result_callback,
                stats=self.stats,
                browser=self.browser,
                skip_cb=self.bl.is_blacklisted,
            ))
        except Exception as e:
            print(f"[Scanner] Tab failed: {e}")

        if not workers:
            print("[Scanner] No workers started. Will retry.")
            if self.browser:
                try:
                    self.browser.stop()
                except Exception:
                    pass
                self.browser = None
            return

        self.worker_tasks = [asyncio.create_task(w.run()) for w in workers]
        print("[Scanner] 1 browser tab @ 0.5 req/s\n")

        cycle = 0
        while self.scanning:
            cycle += 1

            self._rebuild_scan_list()
            cycle_players = list(self.players_to_scan)
            if len(cycle_players) > 1:
                cut = random.randrange(len(cycle_players))
                cycle_players = cycle_players[cut:] + cycle_players[:cut]
            if not cycle_players:
                print("[Scanner] Everyone left is blacklisted. Waiting 30s, still looping.")
                await asyncio.sleep(30)
                continue

            total = len(cycle_players)
            self.stats["progress_total"] = total
            self.stats["progress_idx"] = 0
            est = self.settings.cycle_estimate(total)
            print(f"[Scanner] -- Cycle {cycle} | {total} players "
                  f"| full pass {est} | keeps looping --\n")

            for idx, uuid in enumerate(cycle_players, 1):
                if not self.scanning:
                    break
                if self.bl.is_blacklisted(uuid):
                    continue
                exotic = self.db.get(uuid)
                if not exotic:
                    continue
                await self.scan_queue.put(
                    (uuid, exotic.player_name or "", idx, total))

            if self.scanning:
                while self.scanning:
                    try:
                        await asyncio.wait_for(
                            self.scan_queue.join(), timeout=15)
                        break
                    except asyncio.TimeoutError:
                        alive = any(
                            not t.done() for t in self.worker_tasks
                        )
                        if not alive:
                            print("[Scanner] Worker died — restarting scan loop.")
                            self.scanning = False
                            break
            if not self.scanning:
                break

            elapsed = (datetime.now(timezone.utc) -
                       self.scan_start_time).total_seconds()
            h, r = divmod(int(elapsed), 3600)
            m, s = divmod(r, 60)
            print(
                f"\n[Scanner] Cycle {cycle} done | "
                f"runtime={h}h{m}m{s}s | "
                f"checked={self.stats['checked']} | "
                f"online={self.stats['online_found']} | "
                f"super={len(self.super.rows)} "
                f"(+{self.stats['super_new']}) | "
                f"browser_req={self.stats['browser_requests']} | "
                f"errors={self.stats['errors']} | "
                f"pf={self.stats['parse_fails']}\n"
                f"[Scanner] Full cycle done. Starting the next one.\n"
            )

        for w in workers:
            w.running = False
        for t in self.worker_tasks:
            t.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks = []

        if self.browser:
            try:
                self.browser.stop()
            except Exception:
                pass
            self.browser = None

        print("[Scanner] Stopped.")

    async def _park_chrome(self, tab):
        """Small normal window. Covered or unfocused, it still loads pages."""
        try:
            await tab.set_window_state(state="normal")
        except Exception:
            pass
        try:
            await tab.set_window_size(left=40, top=40, width=520, height=360)
        except Exception as e:
            print(f"[Scanner] Could not park Chrome window: {e}")

    async def _stop_scanner(self):
        self.scanning = False
        task = self.scan_task
        self.scan_task = None
        if not task:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=8)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


bot = ExoticBot()
register_adduser(bot, lambda: DATABASE_FILE)


async def _cmd_reply(interaction: discord.Interaction, content: str, **kwargs):
    kwargs.setdefault("ephemeral", False)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, **kwargs)
        else:
            await interaction.response.send_message(content, **kwargs)
    except Exception:
        try:
            await interaction.channel.send(content)
        except Exception:
            pass


async def _in_own_notifier(interaction: discord.Interaction) -> bool:
    """Commands that control a scanner only run on that person's own bot, inside their thread."""
    owner = (os.getenv("EXOTIC_OWNER_ID") or "").strip()
    if not owner.isdigit():
        await interaction.response.send_message(
            "Run `/start`, `/stop`, and `/status` on **your** bot, "
            "the one named yourname-notifer. This bot does not scan.",
            ephemeral=True,
        )
        return False
    if interaction.user.id != int(owner):
        await interaction.response.send_message(
            "This bot belongs to someone else. Run the command on your own bot.",
            ephemeral=True,
        )
        return False
    manage = await bot._manage_channel()
    channel = interaction.channel
    if isinstance(channel, discord.Thread) and manage is not None and channel.parent_id == manage.id:
        if channel.name == "Admin":
            await interaction.response.send_message(
                "Use your notifier thread for `/start`.", ephemeral=True)
            return False
        mapped = bot._thread_owner_id(channel.id)
        if mapped is not None and mapped != interaction.user.id:
            await interaction.response.send_message(
                "This thread is someone else's notifier.", ephemeral=True)
            return False
        return True
    where = manage.mention if manage else "#mange-your-noti"
    await interaction.response.send_message(
        f"Click **Open my notifier** in {where}, then run `/start` in that thread on **your** bot.",
        ephemeral=True,
    )
    return False


@bot.tree.command(name="start", description="Start or restart the scanner from your private notifier")
async def cmd_start(interaction: discord.Interaction):
    print(f"[start] from {interaction.user} in {getattr(interaction.channel, 'name', '?')}")
    if bot.access_revoked(interaction.user.id):
        await interaction.response.send_message("Your access is revoked.", ephemeral=True)
        return
    if not await _in_own_notifier(interaction):
        return
    try:
        await interaction.response.defer(ephemeral=False)
    except discord.HTTPException:
        pass
    try:
        if not bot.players_to_scan:
            bot._rebuild_scan_list()
        if not bot.players_to_scan:
            await _cmd_reply(
                interaction,
                "The shared player list isn't on this PC. Run `/adduser` again to get a fresh copy.",
            )
            return
        if not bot.cfg.notification_channel_id:
            await _cmd_reply(
                interaction,
                "Set the notifier channel first. In that channel, run `/setchannel`, then `/start` here again.",
            )
            return

        bot._remember_owner(interaction.user.id, interaction.user.name)
        bot._reset_session()
        if isinstance(interaction.channel, discord.Thread):
            bot._owner_thread = interaction.channel
        was_running = bool(bot.scanning or (bot.scan_task and not bot.scan_task.done()))
        bot.ending_session = False
        bot.keep_scanning = True
        asyncio.create_task(bot._restart_scanner())
        await bot._post_scanner_status(True)

        s = bot.settings
        est = s.cycle_estimate(len(bot.players_to_scan))
        ch = await bot._resolve_channel(bot.cfg.notification_channel_id)
        action = "restarting" if was_running else "starting"
        await _cmd_reply(
            interaction,
            f"**Scanner {action}**\n"
            f"Players: **{len(bot.players_to_scan)}**\n"
            f"Full cycle: **{est}** at {REQUESTS_PER_SEC} req/s\n"
            f"It does not stop after that pass. The next cycle starts immediately.\n"
            f"Pings: {ch.mention if ch else 'this channel'}\n"
            f"Chrome keeps scanning while you use the PC. `/status` to check.",
        )
    except Exception as e:
        print(f"[start] {type(e).__name__}: {e}")
        traceback.print_exc()
        await _cmd_reply(
            interaction,
            f"**Start failed:** `{type(e).__name__}: {e}`",
        )


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction,
                        error: app_commands.AppCommandError):
    print(f"[Command error] {interaction.command} {type(error).__name__}: {error}")
    traceback.print_exc()
    msg = f"Command failed: `{type(error).__name__}: {error}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


@bot.tree.command(name="stop", description="Stop the scanner")
async def cmd_stop(interaction: discord.Interaction):
    if not await _in_own_notifier(interaction):
        return
    running = bot.scanning or (bot.scan_task and not bot.scan_task.done())
    if not running:
        await interaction.response.send_message("Not running. Use `/start`.")
        return
    await interaction.response.defer()
    bot.keep_scanning = False
    await bot._stop_scanner()
    await bot._post_scanner_status(False)
    dur = ""
    if bot.scan_start_time:
        e = (datetime.now(timezone.utc) - bot.scan_start_time).total_seconds()
        h, r = divmod(int(e), 3600)
        m, s = divmod(r, 60)
        dur = f"\nRuntime: {h}h {m}m {s}s"
    st = bot.stats
    await interaction.followup.send(
        f"**Stopped.**\n"
        f"Checked: **{st['checked']}** | "
        f"Online: **{st['online_found']}** | "
        f"Super filtered: **{len(bot.super.rows)}** "
        f"(+{st.get('super_new', 0)} this run)\n"
        f"Browser req: **{st['browser_requests']}**\n"
        f"Errors: **{st['errors']}** | "
        f"Parse fails: **{st['parse_fails']}**{dur}\n"
        f"Bot stays online. `/start` to scan again, `/endsession` to quit.",
    )


@bot.tree.command(name="endsession", description="Fully shut down the bot until you start it again")
async def cmd_endsession(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Ending session. Scanner and bot are shutting down. "
        "They will not restart until you launch ExoticNotifier again.",
        ephemeral=True,
    )
    bot.ending_session = True
    bot.keep_scanning = False
    await asyncio.sleep(1)
    await bot.close()


@bot.tree.command(name="status", description="Scanner status")
async def cmd_status(interaction: discord.Interaction):
    if not await _in_own_notifier(interaction):
        return
    await interaction.response.defer()
    running = bot.scanning or (bot.scan_task and not bot.scan_task.done())
    s, st = bot.settings, bot.stats
    dur = speed = "N/A"
    if bot.scan_start_time and running:
        e = (datetime.now(timezone.utc) - bot.scan_start_time).total_seconds()
        h, r = divmod(int(e), 3600)
        m, sec = divmod(r, 60)
        dur = f"{h}h {m}m {sec}s"
        if e > 0 and st["checked"] > 0:
            speed = f"{st['checked'] / (e / 60):.1f}/min"
    pt = st["progress_total"] or len(bot.players_to_scan) or 1
    pi = st["progress_idx"]
    pct = (pi / pt) * 100
    ti = sum(len(p.items) for p in bot.db.values())

    embed = discord.Embed(
        title="Scanner Status",
        color=discord.Color.green() if running else discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Status",
                    value="Running in background" if running else "Stopped",
                    inline=True)
    embed.add_field(name="Rate", value=f"{REQUESTS_PER_SEC} req/s", inline=True)
    embed.add_field(name="Tabs", value="1", inline=True)
    embed.add_field(name="Speed", value=speed, inline=True)
    embed.add_field(name="Players", value=str(len(bot.db)), inline=True)
    embed.add_field(name="Items", value=str(ti), inline=True)
    embed.add_field(name="Blacklist", value=str(len(bot.bl.blacklisted)), inline=True)
    embed.add_field(name="Progress", value=f"{pi}/{pt} ({pct:.1f}%)", inline=True)
    embed.add_field(name="Checked", value=str(st["checked"]), inline=True)
    embed.add_field(name="Online", value=str(st["online_found"]), inline=True)
    embed.add_field(
        name="Super filtered",
        value=f"{len(bot.super.rows)} (+{st.get('super_new', 0)} new)",
        inline=True,
    )
    embed.add_field(name="Browser", value=str(st["browser_requests"]), inline=True)
    embed.add_field(name="Errors", value=str(st["errors"]), inline=True)
    embed.add_field(name="Parse fails", value=str(st["parse_fails"]), inline=True)
    embed.add_field(name="Runtime", value=dur, inline=True)
    embed.add_field(name="Est. cycle",
                    value=s.cycle_estimate(len(bot.players_to_scan)),
                    inline=True)

    ch = await bot._resolve_channel(bot.cfg.notification_channel_id)
    log_ch = await bot._resolve_channel(bot.cfg.log_channel_id)
    info_ch = await bot._resolve_channel(bot.cfg.info_channel_id)
    noti_ch = await bot._noti_channel()
    embed.add_field(
        name="Channels",
        value=(f"Public: {ch.mention if ch else 'Not set'}\n"
               f"Log: {log_ch.mention if log_ch else 'Not set'}\n"
               f"Info (linked Discord): {info_ch.mention if info_ch else 'Not set — /setinfochannel'}\n"
               f"Recent logins: {noti_ch.mention if noti_ch else 'Not set — /setnotichannel'}"),
        inline=False,
    )
    embed.add_field(
        name="While you use the PC",
        value="Leave the small Chrome window open (you can cover it). "
              "Do not close it. Scanning continues when this Discord tab is not focused.",
        inline=False,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="setcsv", description="Set the local CSV path on this PC")
async def cmd_setcsv(interaction: discord.Interaction, path: str):
    owner_id = interaction.guild.owner_id if interaction.guild else None
    if not is_admin(interaction.user) and (owner_id is None or interaction.user.id != owner_id):
        await interaction.response.send_message(
            "The player list is the shared CSV. You can't load a different file.",
            ephemeral=True,
        )
        return
    global DATABASE_FILE
    path = path.strip().strip('"')
    if not os.path.isfile(path):
        await interaction.response.send_message(
            f"File not found on this PC: `{path}`", ephemeral=True)
        return
    DATABASE_FILE = os.path.abspath(path)
    os.environ["DATABASE_FILE"] = DATABASE_FILE
    cfg = load_config()
    cfg["database_file"] = DATABASE_FILE
    if BOT_TOKEN:
        cfg["discord_token"] = BOT_TOKEN
    save_config(cfg)
    bot.db = parse_database(DATABASE_FILE)
    bot._rebuild_scan_list()
    await interaction.response.send_message(
        f"CSV → `{DATABASE_FILE}`\n"
        f"**{len(bot.db)}** players queued **{len(bot.players_to_scan)}**",
        ephemeral=True,
    )


@bot.tree.command(name="setchannel", description="Set notification channel")
async def cmd_setchannel(interaction: discord.Interaction):
    bot.cfg.notification_channel_id = interaction.channel_id
    bot.cfg.save()
    await interaction.response.send_message(
        f"Notifications → {interaction.channel.mention}", ephemeral=True)


@bot.tree.command(name="setlogchannel", description="Set log channel")
async def cmd_setlogchannel(interaction: discord.Interaction):
    bot.cfg.log_channel_id = interaction.channel_id
    bot.cfg.save()
    await interaction.response.send_message(
        f"Log → {interaction.channel.mention}", ephemeral=True)


@bot.tree.command(name="setnotichannel", description="Channel for recent logins")
async def cmd_setnotichannel(interaction: discord.Interaction):
    if not bot._is_directory():
        await interaction.response.send_message(
            "Run `/setnotichannel` on phonktester, in the channel that should get recent logins.",
            ephemeral=True,
        )
        return
    bot.cfg.noti_channel_id = interaction.channel_id
    bot.cfg.save()
    await interaction.response.send_message(
        f"Recent logins (last {RECENT_LOGIN_DAYS} days) → {interaction.channel.mention}\n"
        "Nothing is posted until this is set. Each player is only posted once per login."
    )
    await bot._publish_noti_gate()
    await bot._flush_recent()


@bot.tree.command(name="setinfochannel", description="Set channel for Discord-linked player info")
async def cmd_setinfochannel(interaction: discord.Interaction):
    bot.cfg.info_channel_id = interaction.channel_id
    bot.cfg.save()
    await interaction.response.send_message(
        f"Discord-linked info → {interaction.channel.mention}", ephemeral=True)


@bot.tree.command(
    name="testfeatures",
    description="Post a test ping/log/info message to verify channels work",
)
async def cmd_testfeatures(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    exotic = next(iter(bot.db.values()), None)
    if not exotic:
        await interaction.followup.send("Database is empty.", ephemeral=True)
        return
    sample = PlayerExoticData(exotic.player_uuid, exotic.player_name or "TestPlayer")
    sample.items = list(exotic.items)
    profile = Sk1erProfile(
        online=True,
        last_login=datetime.now(timezone.utc),
        socials={"discord": "testdiscord"},
    )
    results = []

    pub_ch = await bot._resolve_channel(bot.cfg.notification_channel_id)
    if pub_ch:
        try:
            await pub_ch.send(
                content=bot._dev_mention(pub_ch) or None,
                embed=bot._public_embed(sample, profile),
                view=ClaimView(sample, bot._full_embed(sample, profile)),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            results.append(f"Public ping OK → {pub_ch.mention}")
        except Exception as e:
            results.append(f"Public ping FAIL: {e}")
    else:
        results.append("Public channel not set — `/setchannel`")

    log_ch = await bot._resolve_channel(bot.cfg.log_channel_id)
    if log_ch:
        try:
            await log_ch.send(embed=bot._full_embed(sample, profile))
            results.append(f"Log OK → {log_ch.mention}")
        except Exception as e:
            results.append(f"Log FAIL: {e}")
    else:
        results.append("Log channel not set — `/setlogchannel`")

    info_ch = await bot._resolve_channel(bot.cfg.info_channel_id)
    if info_ch:
        try:
            await bot._notify_info(sample, profile)
            results.append(f"Info OK → {info_ch.mention}")
        except Exception as e:
            results.append(f"Info FAIL: {e}")
    else:
        results.append("Info channel not set — `/setinfochannel`")

    noti = await bot._noti_channel()
    if noti:
        try:
            await bot._notify_recent(sample, profile)
            results.append(f"Recent login ping OK → {noti.mention}")
        except Exception as e:
            results.append(f"Recent login ping FAIL: {e}")
    else:
        results.append("Noti channel not set — `/setnotichannel`")

    await interaction.followup.send(
        "**Feature test**\n" + "\n".join(f"• {r}" for r in results),
        ephemeral=True,
    )


@bot.tree.command(name="reload", description="Reload database CSV")
async def cmd_reload(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bot.db = parse_database(DATABASE_FILE)
    bot._rebuild_scan_list()
    te = sum(len(p.items) for p in bot.db.values())
    await interaction.followup.send(
        f"**{len(bot.db)}** players · **{te}** items · "
        f"**{len(bot.players_to_scan)}** queued",
        ephemeral=True,
    )


@bot.tree.command(name="superfiltered", description="Super-filtered list (Discord + post-migration login)")
async def cmd_superfiltered(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    n = len(bot.super.rows)
    if n == 0:
        await interaction.followup.send(
            "No super-filtered players yet. They are added while the scanner "
            "runs: Hypixel Discord linked **and** last login on/after "
            "**Sep 19, 2023** (Microsoft migration).\n"
            f"Saved to `{SUPER_FILTER_FILE}`.",
            ephemeral=True,
        )
        return
    recent = list(bot.super.rows.values())[-10:]
    lines = []
    for row in reversed(recent):
        ign = row.get("ign") or "?"
        dc = row.get("discord") or "?"
        login = (row.get("last_login") or "")[:10]
        lines.append(f"`{ign}` · `{dc}` · {login}")
    await interaction.followup.send(
        f"**{n}** super-filtered (Discord + last login after Sep 19, 2023)\n"
        f"File: `{os.path.abspath(SUPER_FILTER_FILE)}`\n\n"
        + "\n".join(lines),
        ephemeral=True,
    )


@bot.tree.command(name="lookup", description="Look up a player")
@app_commands.describe(uuid="Player UUID")
async def cmd_lookup(interaction: discord.Interaction, uuid: str):
    await interaction.response.defer(ephemeral=True)
    clean = uuid.replace("-", "").lower()
    p = bot.db.get(clean)
    if not p:
        await interaction.followup.send("Not found.", ephemeral=True)
        return
    embed = bot._full_embed(p)
    bl = "yes" if bot.bl.is_blacklisted(clean) else "no"
    embed.add_field(name="Blacklist", value=bl, inline=True)
    view = discord.ui.View(timeout=None)
    add_profile_link_buttons(view, clean, p.player_name or "", row=0)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="checkonline", description="Quick online check via sk1er")
@app_commands.describe(player="Player UUID")
async def cmd_checkonline(interaction: discord.Interaction, player: str):
    await interaction.response.defer(ephemeral=True)
    clean = player.replace("-", "").lower()

    if not bot.browser or not bot.scanning:
        await interaction.followup.send(
            "Scanner not running. Use /start first.", ephemeral=True)
        return
    try:
        await sk1er_limiter.acquire()
        tab = await bot.browser.get(build_sk1er_url(clean), new_tab=True)
        html = None
        for _ in range(20):
            await asyncio.sleep(1)
            title = await safe_evaluate(tab, "document.title")
            if title and "just a moment" not in title.lower():
                h = await safe_evaluate(
                    tab, "document.documentElement.outerHTML")
                if h and "stats-value" in h:
                    html = h
                    break
        if not html:
            await interaction.followup.send(
                f"Page didn't load for `{player}`", ephemeral=True)
            return
        profile = parse_sk1er_profile(html)
        if profile is None:
            await interaction.followup.send(
                f"Could not parse status for `{player}`", ephemeral=True)
            return
        current_name = await resolve_current_name(clean)
        n = f"`{current_name}`" if current_name else f"`{player}`"
        dc = f"\nDiscord: `{profile.discord}`" if profile.discord else "\nDiscord: not linked"
        login = (
            f"\nLast login: `{profile.last_login.strftime('%b %d, %Y %H:%M UTC')}`"
            if profile.last_login else "\nLast login: unknown"
        )
        mig = "yes" if profile.migrated else "no / unknown"
        extra = f"{dc}{login}\nMigrated: **{mig}**"
        if profile.super_filtered:
            extra += "\n**Super filtered**"
        if profile.online:
            await interaction.followup.send(
                f"**ONLINE** — {n}{extra}\n*via sk1er.club*", ephemeral=True)
        else:
            await interaction.followup.send(
                f"**Offline** — {n}{extra}\n*via sk1er.club*",
                ephemeral=True)
    except Exception as e:
        await interaction.followup.send(
            f"Error: {type(e).__name__}: {e}", ephemeral=True)
    finally:
        try:
            if "tab" in locals() and tab:
                await tab.close()
        except Exception:
            pass


@bot.tree.command(name="blacklistadd", description="Blacklist a UUID")
@app_commands.describe(uuid="Player UUID")
async def cmd_bladd(interaction: discord.Interaction, uuid: str):
    bot.bl.add(uuid.replace("-", "").lower())
    bot._rebuild_scan_list()
    await interaction.response.send_message(
        f"Blacklisted. **{len(bot.players_to_scan)}** players still queued.",
        ephemeral=True,
    )


@bot.tree.command(name="blacklistremove", description="Remove from blacklist")
@app_commands.describe(uuid="Player UUID")
async def cmd_blremove(interaction: discord.Interaction, uuid: str):
    clean = uuid.replace("-", "").lower()
    if bot.bl.remove(clean):
        await interaction.response.send_message("Removed.", ephemeral=True)
    else:
        await interaction.response.send_message("Not found.", ephemeral=True)


@bot.tree.command(name="blacklistcount", description="Blacklist count")
async def cmd_blcount(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"{len(bot.bl.blacklisted)}", ephemeral=True)


@bot.tree.command(name="clearnotified", description="Clear notified cache")
async def cmd_clearnotified(interaction: discord.Interaction):
    n = len(bot.notified.notified)
    bot.notified.notified.clear()
    bot.notified.save()
    await interaction.response.send_message(f"Cleared {n}.", ephemeral=True)


@bot.tree.command(name="sync", description="Re-sync commands in this server")
async def cmd_sync(interaction: discord.Interaction):
    await interaction.response.defer()
    await bot._publish_commands()
    names = ", ".join(f"`/{c.name}`" for c in sorted(bot.tree.get_commands(), key=lambda c: c.name))
    await interaction.followup.send(
        f"**{len(bot.tree.get_commands())}** commands in this server, each once.\n{names}\n"
        "If you still see doubles, fully restart the Discord app."
    )


if __name__ == "__main__":
    install_signal_cleanup()
    apply_runtime_config(wizard=os.environ.get("BOT_CHILD") != "1")

    if not BOT_TOKEN or BOT_TOKEN == "paste_your_bot_token_here":
        print("Missing Discord bot token.")
        print("Re-run with --setup or paste it when the wizard asks.")
        sys.exit(1)

        print("[Access] Running")

    if os.environ.get("BOT_CHILD") != "1":
        prevent_sleep()
        write_pid()
        print("[Watchdog] Session running in the background until /endsession.")
        while True:
            env = os.environ.copy()
            env["BOT_CHILD"] = "1"
            if is_frozen():
                cmd = [sys.executable, "--bot"]
            else:
                cmd = [sys.executable, "-u", os.path.abspath(__file__)]
            rc = subprocess.call(cmd, env=env)
            if rc == NUKE_EXIT or rc - 256 == NUKE_EXIT:
                print("[Watchdog] Unauthorized server. This copy is off.")
                schedule_code_delete()
                break
            if rc == 0:
                print("[Watchdog] /endsession received. Bot is off.")
                break
            print(f"[Watchdog] Bot exited ({rc}). Restarting in 8s...")
            time.sleep(8)
        allow_sleep()
        clear_pid()
        sys.exit(0)

    prevent_sleep()

    print("=" * 55)
    print("  Exotic Notifier Bot (sk1er only)")
    print("=" * 55)
    print(f"  Database : {DATABASE_FILE}")
    print(f"  Rate     : {REQUESTS_PER_SEC} req/s (2s gap, 1 Chrome tab)")
    print(f"  Chrome   : {'headless' if is_headless() else 'background-safe (not throttled when unfocused)'}")
    print("  Runs until /endsession (auto-restarts on crash)")
    print("=" * 55)
    try:
        bot.run(BOT_TOKEN)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        allow_sleep()
