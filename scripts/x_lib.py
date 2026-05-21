"""Shared helpers for X API CLI tools.

Loads OAuth 1.0a credentials from env file (local) or os.environ (CI), and
exposes path/auth/log helpers. Path roots are env-overridable so the same
code runs both on Mac (~/.claude/x-queue) and GitHub Actions ($GITHUB_WORKSPACE/queue).

- load_env() -> dict
- build_auth(env) -> OAuth1
- log(name, msg) -> append to LOG_DIR/x-<name>.log
- tweet_id_from(s) -> extract numeric id from URL or pass-through
- series_buffer(prefix) -> summarize pending entries
- JST timezone
- PENDING / DONE / FAILED / DELETED paths
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from requests_oauthlib import OAuth1

QUEUE_ROOT = Path(os.environ.get("X_QUEUE_ROOT", str(Path.home() / ".claude" / "x-queue")))
PENDING = QUEUE_ROOT / "pending"
DONE = QUEUE_ROOT / "done"
FAILED = QUEUE_ROOT / "failed"
DELETED = QUEUE_ROOT / "deleted"

LOG_DIR = Path(os.environ.get("X_LOG_DIR", str(Path.home() / ".claude" / "logs" / "routines")))
ENV_FILE = Path(os.environ.get("X_ENV_FILE", str(Path.home() / ".claude" / "secrets" / "x-bobu_reflect.env")))

JST = timezone(timedelta(hours=9))

REQUIRED_KEYS = [
    "X_CONSUMER_KEY",
    "X_CONSUMER_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    for key in REQUIRED_KEYS + ["X_HANDLE", "PHILOSOPHY_CHAT_URL"]:
        if os.environ.get(key):
            env[key] = os.environ[key]
    missing = [r for r in REQUIRED_KEYS if not env.get(r)]
    if missing:
        raise SystemExit(f"missing required keys: {missing}")
    env.setdefault("X_HANDLE", "bobu_reflect")
    return env


def build_auth(env: dict[str, str]) -> OAuth1:
    return OAuth1(
        env["X_CONSUMER_KEY"],
        env["X_CONSUMER_SECRET"],
        env["X_ACCESS_TOKEN"],
        env["X_ACCESS_TOKEN_SECRET"],
    )


def log(name: str, msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(JST).isoformat()
    with (LOG_DIR / f"x-{name}.log").open("a") as f:
        f.write(f"[{ts}] {msg}\n")


_URL_ID_RE = re.compile(r"(?:status/|/i/web/status/)(\d+)")


def tweet_id_from(s: str) -> str:
    """Accept tweet URL or bare numeric id; return numeric id."""
    s = s.strip()
    if s.isdigit():
        return s
    m = _URL_ID_RE.search(s)
    if m:
        return m.group(1)
    raise SystemExit(f"could not extract tweet id from: {s!r}")


def series_buffer(prefix: str = "x-post-series") -> dict:
    """
    Summarize pending entries matching source prefix (default = series posts).
    Returns: {count, latest_at (or None), next_slot (or None)}

    next_slot = latest_at + 1 day, same time. If no pending, falls back to
    tomorrow 08:00 JST as the conventional series slot.
    """
    import json
    from datetime import timedelta as _td, time as _time

    if not PENDING.exists():
        latest = None
        count = 0
    else:
        scheds = []
        for p in PENDING.glob("*.json"):
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            if (d.get("source") or "").startswith(prefix):
                try:
                    dt = datetime.fromisoformat(d["scheduled_at"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=JST)
                    scheds.append(dt)
                except (KeyError, ValueError):
                    continue
        count = len(scheds)
        latest = max(scheds) if scheds else None

    if latest is not None:
        next_slot = latest + _td(days=1)
    else:
        now = datetime.now(JST)
        tomorrow = (now + _td(days=1)).date()
        next_slot = datetime.combine(tomorrow, _time(8, 0), tzinfo=JST)

    return {"count": count, "latest_at": latest, "next_slot": next_slot}
