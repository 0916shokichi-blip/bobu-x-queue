# bobu-x-queue

@bobu_reflect の予約投稿クラウド cron。Mac 不要で投稿が走る。

## 構造

```
queue/
├── pending/   # 投稿待ち JSON (scheduled_at が未来)
├── done/      # 投稿済み + tweet_url + metrics
├── failed/    # 3 回失敗で諦めたもの
└── _media/    # 投稿用画像
scripts/
├── x-submit.py    # pending → 投稿 → done/move
├── x-retro.py     # done の metrics pull (impressions / link_clicks)
├── x_lib.py       # 共通 helper (OAuth / path / log)
├── x_guard.py     # G1-G7 投稿前 guard
└── x_ledger.py    # quota / continuity 追跡
.github/workflows/
├── submit.yml     # 5 分毎 cron (Public repo = 完全無料)
└── retro.yml      # 毎日 18:30 UTC (03:30 JST) metrics pull
```

## 実行フロー

1. **enqueue** (Mac 側、`~/.claude/scripts/x post --at <時刻>`): pending/ に JSON 書く + 画像を _media/ にコピー + auto commit + push
2. **scan** (Actions、5 分毎): pending/ から scheduled_at <= now な entry を取り出す
3. **投稿** (Actions): X API v2 で tweet 送信 → done/ に move + tweet_url 追記 → commit + push
4. **失敗時** (Actions): 402/429 は Deferred (entry 保持)、他 4xx/5xx は attempts +1、3 回失敗で failed/
5. **retro** (Actions、daily): done/ の 7-30 日経過 entry に metrics pull → JSON 更新 → commit + push

## Secrets (GitHub Settings → Secrets and variables → Actions)

| key | 用途 |
|---|---|
| X_CONSUMER_KEY | OAuth 1.0a Consumer Key |
| X_CONSUMER_SECRET | OAuth 1.0a Consumer Secret |
| X_ACCESS_TOKEN | OAuth 1.0a Access Token (@bobu_reflect 紐付き) |
| X_ACCESS_TOKEN_SECRET | OAuth 1.0a Access Token Secret |

## 関連

Mac 側の運用 / x CLI 詳細 / 起草 skill は `~/.claude/` 配下 (private)。
