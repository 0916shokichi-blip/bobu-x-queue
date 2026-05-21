#!/Users/higashishota/.venvs/x/bin/python3
"""x-retro.py — pull metrics for posted tweets into done/ entries.

For each eligible done/ entry, fetches BOTH public_metrics (likes/RT/etc)
and non_public_metrics (impression / url_link_clicks / user_profile_clicks).
The non_public_metrics endpoint requires OAuth user-context (same auth as
posting) and is restricted to tweets ≤ 30 days old, authored by the same
user.

This is the philosophy-chat-value-relevant metric: url_link_clicks tells
us which post actually drove philosophy-chat traffic, not which one got
"engagement".

Eligibility:
- has tweet_id and posted_at
- 7 ≤ age ≤ 30 days  (default; --force lifts the floor)
- metrics_pulled_at not yet set

Budget: 100 reads/month on Free tier. MAX_PER_RUN caps each invocation;
running daily, ~1 new eligible entry per day → ~30 reads/month.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

import x_lib as xl
import x_ledger as xledger

GET_ENDPOINT = "https://api.x.com/2/tweets/{id}"
PARAMS = {"tweet.fields": "public_metrics,non_public_metrics"}

MIN_AGE_DAYS = 7
MAX_AGE_DAYS = 30
MAX_PER_RUN = 10  # capped tight to protect 100 reads/month budget


def needs_metrics(data: dict, now: datetime, force: bool) -> bool:
    if not data.get("tweet_id"):
        return False
    if data.get("metrics_pulled_at"):
        return False
    posted_raw = data.get("posted_at")
    if not posted_raw:
        return False
    posted = datetime.fromisoformat(posted_raw)
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=xl.JST)
    age = now - posted
    if force:
        return age <= timedelta(days=MAX_AGE_DAYS)
    return timedelta(days=MIN_AGE_DAYS) <= age <= timedelta(days=MAX_AGE_DAYS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ignore the 7-day floor (testing)")
    ap.add_argument("--limit", type=int, default=MAX_PER_RUN, help=f"max per run (default {MAX_PER_RUN})")
    args = ap.parse_args()

    if not xl.DONE.exists():
        return
    now = datetime.now(xl.JST)
    candidates = []
    for p in sorted(xl.DONE.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if needs_metrics(d, now, args.force):
            candidates.append(p)
    if not candidates:
        xl.log("retro", "no candidates")
        print("(no candidates)", file=sys.stderr)
        return

    candidates = candidates[: args.limit]
    env = xl.load_env()
    auth = xl.build_auth(env)
    xl.log("retro", f"pulling {len(candidates)} entries")

    pulled = 0
    for path in candidates:
        data = json.loads(path.read_text())
        tweet_id = data["tweet_id"]
        try:
            r = requests.get(
                GET_ENDPOINT.format(id=tweet_id),
                params=PARAMS,
                auth=auth,
                timeout=30,
            )
            if r.status_code == 429:
                xl.log("retro", f"rate-limited; stop run after {pulled} pulls")
                break
            if r.status_code >= 400:
                xl.log("retro", f"FAILED {path.name} {r.status_code} {r.text[:200]}")
                continue
            body = r.json()
            tweet = body.get("data") or {}
            data["public_metrics"] = tweet.get("public_metrics", {})
            data["non_public_metrics"] = tweet.get("non_public_metrics", {})
            data["metrics_pulled_at"] = now.isoformat()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            pulled += 1
            xledger.inc_read(1)
            pm = data["public_metrics"]
            np = data["non_public_metrics"]
            xl.log(
                "retro",
                f"ok {path.name} likes={pm.get('like_count')} "
                f"rt={pm.get('retweet_count')} reply={pm.get('reply_count')} "
                f"imp={np.get('impression_count')} clicks={np.get('url_link_clicks')} "
                f"profile={np.get('user_profile_clicks')}",
            )
        except Exception as e:
            xl.log("retro", f"crash {path.name} {e}")


if __name__ == "__main__":
    main()
