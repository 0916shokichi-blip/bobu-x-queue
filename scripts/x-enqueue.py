#!/Users/higashishota/.venvs/x/bin/python3
"""x-enqueue.py — append X post to ~/.claude/x-queue/pending/.

Internal helper. Users / skills should call `x post / quote / reply` instead.

Refactored for V6 (persona guard), image copy-on-enqueue (entry self-contained),
and CTA + continuity checks. Failure modes are surfaced as:

  - "error" guards: refuse to enqueue (exit 2), unless --force.
  - "warn" guards: print to stderr, still enqueue.

Usage:
  x-enqueue.py --text "..." [--image PATH] [--at WHEN]
               [--quote-of URL_OR_ID] [--reply-to URL_OR_ID]
               [--source TAG] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import string
import sys
from datetime import datetime, timedelta
from pathlib import Path

import x_lib as xl
import x_guard as xg
import x_ledger as xledger

PENDING = xl.PENDING
MEDIA = xl.QUEUE_ROOT / "_media"
JST = xl.JST


def parse_when(s: str) -> datetime:
    s = s.strip()
    now = datetime.now(JST)
    if s == "now":
        return now  # immediately due — works for both --now sync flush and launchd 5-min scan
    m = re.match(r"^\+(\d+)([hmd])$", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        key = {"h": "hours", "m": "minutes", "d": "days"}[unit]
        return now + timedelta(**{key: n})
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        return cand
    s_norm = s.replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s_norm)
    except ValueError as e:
        raise SystemExit(f"unrecognized time format: {s!r} ({e})")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt


def slugify(text: str, max_len: int = 30) -> str:
    s = re.sub(r"\s+", "-", text.strip())
    s = re.sub(
        r"[^\w\-぀-ゟ゠-ヿ一-鿿]",
        "",
        s,
        flags=re.UNICODE,
    )
    return s[:max_len] or "post"


def rand_suffix(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def derive_mode(quote_of: str | None, reply_to: str | None) -> str:
    if quote_of:
        return "quote"
    if reply_to:
        return "reply"
    return "post"


def load_cta_url() -> str | None:
    # Optional value from env file; absence means no CTA check.
    try:
        env = xl.load_env()
        url = env.get("PHILOSOPHY_CHAT_URL", "").strip()
        return url or None
    except SystemExit:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--image", default=None)
    ap.add_argument("--at", default="now")
    ap.add_argument("--quote-of", default=None, help="URL or id of tweet to quote-RT")
    ap.add_argument("--reply-to", default=None, help="URL or id of tweet to reply to")
    ap.add_argument("--source", default="manual")
    ap.add_argument("--force", action="store_true", help="skip guard errors")
    args = ap.parse_args()

    if args.quote_of and args.reply_to:
        raise SystemExit("--quote-of and --reply-to are mutually exclusive")

    mode = derive_mode(args.quote_of, args.reply_to)

    # Guards — compare against both posted-history and currently-pending texts
    cta_url = load_cta_url() if mode == "post" else None
    prior = list(xledger.recent_posts(days=7))
    if PENDING.exists():
        nowdt = datetime.now()
        for p in PENDING.glob("*.json"):
            try:
                d = json.loads(p.read_text())
                t = d.get("text") or ""
                if t:
                    prior.append((t, nowdt))
            except Exception:
                continue
    report = xg.check_text(
        args.text,
        mode=mode,
        cta_url=cta_url,
        prior_texts=prior,
    )
    if report:
        print(xg.render(report), file=sys.stderr)
    if xg.has_error(report) and not args.force:
        print("[abort] guard errors — use --force to override", file=sys.stderr)
        sys.exit(2)

    # Image copy (entry self-contained)
    image_path = None
    image_orig = None
    if args.image:
        src = Path(args.image).expanduser().resolve()
        if not src.exists():
            raise SystemExit(f"image not found: {src}")
        image_orig = str(src)
        # placeholder; finalized after we know the entry filename
        image_path = "PENDING_COPY"

    quote_id = xl.tweet_id_from(args.quote_of) if args.quote_of else None
    reply_id = xl.tweet_id_from(args.reply_to) if args.reply_to else None

    sched = parse_when(args.at)
    base_stem = f"{sched.strftime('%Y-%m-%dT%H-%M-%S')}__{slugify(args.text)}__{rand_suffix()}"
    PENDING.mkdir(parents=True, exist_ok=True)

    if image_path == "PENDING_COPY":
        MEDIA.mkdir(parents=True, exist_ok=True)
        src = Path(image_orig)
        dst = MEDIA / f"{base_stem}{src.suffix}"
        shutil.copy2(src, dst)
        image_path = str(dst)

    entry = {
        "mode": mode,
        "text": args.text,
        "image_path": image_path,
        "image_orig": image_orig,
        "quote_tweet_id": quote_id,
        "reply_to_id": reply_id,
        "scheduled_at": sched.isoformat(),
        "source": args.source,
        "created_at": datetime.now(JST).isoformat(),
        "attempts": 0,
        "guard_report": [{"level": l, "code": c, "msg": m} for l, c, m in report],
    }
    fpath = PENDING / f"{base_stem}.json"
    fpath.write_text(json.dumps(entry, ensure_ascii=False, indent=2))

    print(f"queued: {fpath.name}")
    print(f"  mode:  {mode}")
    print(f"  at:    {sched.isoformat()}")
    preview = args.text if len(args.text) <= 60 else args.text[:60] + "..."
    print(f"  text:  {preview}")
    if image_path:
        print(f"  image: {image_path}")
    if quote_id:
        print(f"  quote: {quote_id}")
    if reply_id:
        print(f"  reply: {reply_id}")


if __name__ == "__main__":
    main()
