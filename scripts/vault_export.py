"""vault_export.py — done エントリを bobu-x-queue/vault-export/ に MD として書く。

x-submit.py が投稿成功時に 1 件 export、submit.yml が毎回 backfill を回して
取りこぼしを救う。出力 dir は repo 内のため commit 経由で Mac に配信 → Mac 側で
rsync して Obsidian vault (~/Documents/メイン/projects/X-posts/) に渡す。

リーン版: x_lib への依存を排除し、QUEUE_ROOT は env var or repo 内 path で resolve。
GH Actions runner (Ubuntu) と Mac の両方で動く。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE_ROOT = Path(os.environ.get("X_QUEUE_ROOT", str(REPO_ROOT / "queue")))
DONE = QUEUE_ROOT / "done"
VAULT_EXPORT_ROOT = Path(os.environ.get(
    "X_VAULT_EXPORT_ROOT",
    str(REPO_ROOT / "vault-export"),
))


def _slugify(text: str, limit: int = 24) -> str:
    s = re.sub(r"[\s　]+", "-", text.strip())
    s = re.sub(r"[^\w\-ぁ-んァ-ヴー一-龥]", "", s)
    return s[:limit] or "post"


def _resolve_unique(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    for i in range(2, 100):
        c = p.with_name(f"{stem}__{i}{suffix}")
        if not c.exists():
            return c
    raise RuntimeError(f"too many name collisions: {p}")


def _entry_mode(d: dict) -> str:
    return d.get("mode") or ("quote" if d.get("quote_tweet_id") else "post")


def _stamp_prefix(posted_at: datetime, mode: str) -> str:
    stamp = posted_at.strftime("%Y-%m-%dT%H-%M-%S")
    return f"{stamp}__quote__" if mode == "quote" else f"{stamp}__"


def _already_exported(posted_at: datetime, mode: str) -> bool:
    month_dir = VAULT_EXPORT_ROOT / posted_at.strftime("%Y-%m")
    if not month_dir.exists():
        return False
    return any(month_dir.glob(f"{_stamp_prefix(posted_at, mode)}*.md"))


def _md_body(data: dict, image_rel: str | None) -> str:
    mode = _entry_mode(data)
    fm = [
        "---",
        f"posted_at: {data.get('posted_at', '')}",
        f"tweet_url: {data.get('tweet_url', '')}",
        f"type: {mode}",
        f"source: {data.get('source', '')}",
    ]
    if data.get("quote_tweet_id"):
        fm.append(f"quote_of: https://x.com/i/status/{data['quote_tweet_id']}")
    fm.append("---")

    body = ["", data.get("text", ""), ""]
    if image_rel:
        body += [f"![[{image_rel}]]", ""]
    if data.get("tweet_url"):
        body += [f"[X で開く]({data['tweet_url']})", ""]
    if data.get("quote_tweet_id"):
        body += [f"引用元: https://x.com/i/status/{data['quote_tweet_id']}", ""]
    return "\n".join(fm + body)


def export_entry(data: dict) -> Path:
    """1 件 export。posted_at 必須。md path を返す。"""
    posted_at_iso = data.get("posted_at")
    if not posted_at_iso:
        raise ValueError("posted_at missing")
    posted_at = datetime.fromisoformat(posted_at_iso)

    month_dir = VAULT_EXPORT_ROOT / posted_at.strftime("%Y-%m")
    assets_dir = VAULT_EXPORT_ROOT / "_assets"
    month_dir.mkdir(parents=True, exist_ok=True)

    stamp = posted_at.strftime("%Y-%m-%dT%H-%M-%S")
    mode = _entry_mode(data)
    slug = _slugify(data.get("text", ""))
    suffix = f"__quote__{slug}" if mode == "quote" else f"__{slug}"
    md_path = _resolve_unique(month_dir / f"{stamp}{suffix}.md")

    image_rel = None
    img_path_str = data.get("image_path")
    if img_path_str:
        src = Path(img_path_str)
        if not src.is_absolute():
            src = QUEUE_ROOT / src
        if not src.exists():
            alt = QUEUE_ROOT / "_media" / Path(img_path_str).name
            if alt.exists():
                src = alt
        if src.exists():
            assets_dir.mkdir(parents=True, exist_ok=True)
            dst = _resolve_unique(assets_dir / f"{md_path.stem}{src.suffix}")
            shutil.copy2(src, dst)
            image_rel = f"_assets/{dst.name}"

    md_path.write_text(_md_body(data, image_rel), encoding="utf-8")
    return md_path


def backfill() -> dict:
    """done/*.json 全件を vault-export/ に書き出し (既存 skip、idempotent)。"""
    stats = {"done_total": 0, "skipped": 0, "exported": 0, "failed": 0, "errors": []}
    if not DONE.exists():
        return stats
    for p in sorted(DONE.glob("*.json")):
        stats["done_total"] += 1
        try:
            data = json.loads(p.read_text())
            posted_at_iso = data.get("posted_at")
            if not posted_at_iso:
                stats["errors"].append(f"{p.name}: posted_at missing")
                stats["failed"] += 1
                continue
            posted_at = datetime.fromisoformat(posted_at_iso)
            if _already_exported(posted_at, _entry_mode(data)):
                stats["skipped"] += 1
                continue
            export_entry(data)
            stats["exported"] += 1
        except Exception as e:
            stats["errors"].append(f"{p.name}: {e}")
            stats["failed"] += 1
    return stats


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="vault export CLI")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("backfill", help="done/*.json 全件を vault-export/ に書き出し")
    args = ap.parse_args()

    if args.cmd == "backfill":
        s = backfill()
        print(json.dumps(s, indent=2, ensure_ascii=False))
        sys.exit(0 if s["failed"] == 0 else 1)
    else:
        ap.print_help()
        sys.exit(2)
