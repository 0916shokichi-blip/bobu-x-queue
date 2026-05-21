#!/Users/higashishota/.venvs/x/bin/python3
"""x-submit.py — scan ~/.claude/x-queue/pending/, post due items via X API v2.

Internal helper. launchd invokes this every 5 min. Also called inline by
`x post --now`.

Rate-limit (HTTP 429) is treated as a deferred retry: the entry stays in
pending/ untouched (attempts not incremented) so the next scan tries again.
Other 4xx/5xx errors and exceptions count toward MAX_ATTEMPTS.
"""
from __future__ import annotations

import fcntl
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

import x_lib as xl
import x_ledger as xledger

MAX_ATTEMPTS = 3
TWEET_ENDPOINT = "https://api.x.com/2/tweets"
MEDIA_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"
LOCK = xl.QUEUE_ROOT / ".submit.lock"


class Deferred(Exception):
    """Transient block (429 rate limit / 402 no credits) — leave entry pending."""
    pass


def _check_deferrable(r) -> None:
    """Raise Deferred for HTTP statuses that mean 'wait, user/X side issue'."""
    if r.status_code == 429:
        raise Deferred(f"429 rate-limited: {r.text[:200]}")
    if r.status_code == 402:
        raise Deferred(f"402 credits-depleted: top up at console.x.com")


def upload_media(auth, image_path: Path) -> str:
    with image_path.open("rb") as f:
        r = requests.post(MEDIA_ENDPOINT, auth=auth, files={"media": f}, timeout=60)
    _check_deferrable(r)
    if r.status_code >= 400:
        raise RuntimeError(f"media upload {r.status_code}: {r.text[:300]}")
    return r.json()["media_id_string"]


def post_tweet(
    auth,
    text: str,
    media_id: str | None = None,
    quote_tweet_id: str | None = None,
    reply_to_id: str | None = None,
) -> dict:
    payload: dict = {"text": text}
    if media_id:
        payload["media"] = {"media_ids": [media_id]}
    if quote_tweet_id:
        payload["quote_tweet_id"] = quote_tweet_id
    if reply_to_id:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}
    r = requests.post(TWEET_ENDPOINT, auth=auth, json=payload, timeout=60)
    _check_deferrable(r)
    if r.status_code >= 400:
        raise RuntimeError(f"tweet post {r.status_code}: {r.text[:300]}")
    return r.json()["data"]


def process_entry(path: Path, auth, handle: str) -> str:
    data = json.loads(path.read_text())
    sched = datetime.fromisoformat(data["scheduled_at"])
    if sched.tzinfo is None:
        sched = sched.replace(tzinfo=xl.JST)
    now = datetime.now(xl.JST)
    if sched > now:
        return "not-due"

    try:
        media_id = None
        if data.get("image_path"):
            img = Path(data["image_path"])
            if not img.is_absolute():
                # New format (2026-05-21〜): relative to QUEUE_ROOT.
                img = xl.QUEUE_ROOT / img
            if not img.exists():
                # Fallback for legacy absolute Mac paths: resolve by basename
                # against the queue _media/ dir so the GHA runner can find it.
                alt = xl.QUEUE_ROOT / "_media" / img.name
                if alt.exists():
                    img = alt
                else:
                    raise FileNotFoundError(f"image missing: {img}")
            media_id = upload_media(auth, img)
        result = post_tweet(
            auth,
            data["text"],
            media_id=media_id,
            quote_tweet_id=data.get("quote_tweet_id"),
            reply_to_id=data.get("reply_to_id"),
        )
        tweet_id = result["id"]
        data["posted_at"] = now.isoformat()
        data["tweet_id"] = tweet_id
        data["tweet_url"] = f"https://x.com/{handle}/status/{tweet_id}"
        xl.DONE.mkdir(parents=True, exist_ok=True)
        (xl.DONE / path.name).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        path.unlink()

        # ledger updates
        try:
            xledger.inc_write(1)
            xledger.record_post(data["text"], now.replace(tzinfo=None), tweet_id)
        except Exception as e:
            xl.log("submit", f"ledger update failed (non-fatal): {e}")

        xl.log("submit", f"posted {path.name} -> {data['tweet_url']}")
        print(data["tweet_url"])  # stdout: URL for easy copy / browser open
        return "posted"
    except Deferred as e:
        # do NOT inc attempts; just wait for next scan (rate limit or credits)
        xl.log("submit", f"deferred {path.name} | {e}")
        return "deferred"
    except Exception as e:
        data["attempts"] = data.get("attempts", 0) + 1
        data["last_error"] = str(e)[:500]
        if data["attempts"] >= MAX_ATTEMPTS:
            xl.FAILED.mkdir(parents=True, exist_ok=True)
            (xl.FAILED / path.name).write_text(json.dumps(data, ensure_ascii=False, indent=2))
            path.unlink()
            xl.log("submit", f"FAILED {path.name} attempts={data['attempts']} | {e}")
            return "failed"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        xl.log("submit", f"retry {data['attempts']}/{MAX_ATTEMPTS} {path.name} | {e}")
        return "retry"


def main() -> None:
    if not xl.PENDING.exists():
        return
    entries = sorted(xl.PENDING.glob("*.json"))
    if not entries:
        return

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fp = LOCK.open("w")
    have_lock = False
    try:
        try:
            fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            have_lock = True
        except BlockingIOError:
            xl.log("submit", "another submit running; skip")
            return

        env = xl.load_env()
        auth = xl.build_auth(env)
        xl.log("submit", f"scan: {len(entries)} pending")
        for path in entries:
            try:
                status = process_entry(path, auth, env["X_HANDLE"])
                print(f"{path.name}: {status}", file=sys.stderr)
            except Exception as e:
                xl.log("submit", f"crash on {path.name}: {e}")
    finally:
        if have_lock:
            try:
                fcntl.flock(lock_fp, fcntl.LOCK_UN)
            except Exception:
                pass
        lock_fp.close()


if __name__ == "__main__":
    main()
