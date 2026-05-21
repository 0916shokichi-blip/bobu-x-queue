"""x_ledger — CLI-local state (continuity history + monthly quota).

Lives at:
  ~/.claude/x-queue/.continuity.json   (last ~90 days of posted text)
  ~/.claude/x-queue/.quota.json        (monthly write/read counters)

Gate-log (~/projects/philosophy-chat/.agents/director/gate-log.md) is owned
by the /x-post skill, not by this layer.

API:
  load_continuity() / record_post(text, posted_at, tweet_id)
  load_quota() / inc_write() / inc_read(n)
  recent_posts(days=7) -> [(text, posted_at)]
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import fcntl

_QUEUE_ROOT = Path(os.environ.get("X_QUEUE_ROOT", str(Path.home() / ".claude" / "x-queue")))
CONTINUITY = _QUEUE_ROOT / ".continuity.json"
QUOTA = _QUEUE_ROOT / ".quota.json"

from datetime import timezone

JST = timezone(timedelta(hours=9))
KEEP_DAYS = 90


def _atomic_update(path: Path, default: dict, mutator) -> dict:
    """Read-modify-write under flock; create if missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(default, ensure_ascii=False, indent=2))
    fp = path.open("r+")
    try:
        fcntl.flock(fp, fcntl.LOCK_EX)
        try:
            data = json.loads(fp.read() or "{}")
        except json.JSONDecodeError:
            data = dict(default)
        data = mutator(data)
        fp.seek(0)
        fp.truncate()
        fp.write(json.dumps(data, ensure_ascii=False, indent=2))
        fp.flush()
    finally:
        fcntl.flock(fp, fcntl.LOCK_UN)
        fp.close()
    return data


# ---- continuity ----

def record_post(text: str, posted_at: datetime, tweet_id: str | None = None) -> None:
    def mut(data):
        posts = data.get("posts", [])
        posts.append(
            {
                "text": text,
                "posted_at": posted_at.isoformat(),
                "tweet_id": tweet_id,
            }
        )
        # prune entries older than KEEP_DAYS
        cutoff = posted_at - timedelta(days=KEEP_DAYS)
        posts = [p for p in posts if datetime.fromisoformat(p["posted_at"]) >= cutoff]
        data["posts"] = posts
        return data

    _atomic_update(CONTINUITY, {"posts": []}, mut)


def recent_posts(days: int = 7) -> list[tuple[str, datetime]]:
    if not CONTINUITY.exists():
        return []
    try:
        data = json.loads(CONTINUITY.read_text())
    except Exception:
        return []
    cutoff = datetime.now(JST) - timedelta(days=days)
    out: list[tuple[str, datetime]] = []
    for p in data.get("posts", []):
        try:
            ts = datetime.fromisoformat(p["posted_at"])
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=JST)
        if ts >= cutoff:
            out.append((p.get("text", ""), ts))
    return out


# ---- quota ----

FREE_WRITE_BUDGET = 500
FREE_READ_BUDGET = 100


def _current_month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(JST)
    return now.strftime("%Y-%m")


def inc_write(n: int = 1) -> dict:
    return _bump("write", n)


def inc_read(n: int = 1) -> dict:
    return _bump("read", n)


def _bump(kind: str, n: int) -> dict:
    month = _current_month_key()
    def mut(data):
        m = data.get(month, {"write": 0, "read": 0})
        m[kind] = m.get(kind, 0) + n
        data[month] = m
        # prune months older than 12
        cutoff = datetime.now(JST).replace(tzinfo=None) - timedelta(days=370)
        for k in list(data.keys()):
            try:
                if datetime.strptime(k, "%Y-%m") < cutoff:
                    del data[k]
            except ValueError:
                pass
        return data
    return _atomic_update(QUOTA, {}, mut)


def load_quota() -> dict:
    month = _current_month_key()
    if not QUOTA.exists():
        return {"month": month, "write": 0, "read": 0}
    try:
        data = json.loads(QUOTA.read_text())
    except Exception:
        return {"month": month, "write": 0, "read": 0}
    m = data.get(month, {"write": 0, "read": 0})
    return {"month": month, "write": m.get("write", 0), "read": m.get("read", 0)}


def quota_pressure() -> tuple[float, float]:
    q = load_quota()
    return (
        q["write"] / FREE_WRITE_BUDGET,
        q["read"] / FREE_READ_BUDGET,
    )
