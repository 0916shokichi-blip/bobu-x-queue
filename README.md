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
├── x-submit.py    # pending → 投稿 → done/move (GitHub Actions が呼ぶ)
├── x-retro.py     # done の metrics pull
├── x_lib.py       # 共通 helper (OAuth / path / log) — env var で path override 可
├── x_guard.py     # G1-G7 投稿前 guard
└── x_ledger.py    # quota / continuity
.github/workflows/
├── submit.yml     # 5 分毎 cron + commit/push back (Public repo = 完全無料)
└── retro.yml      # 毎日 18:30 UTC (03:30 JST) metrics pull
```

## 投稿時刻精度

**±5-30 分の遅延を想定**:
- GitHub Actions schedule trigger は high load 時に最大 30 分 skip される (公式 docs 明記)
- 5 分 cron + delay = 実投稿時刻は予約時刻 + 5-30 分
- シリーズ投稿 (夕飯前 18:00-19:00 帯狙い) はこの精度で実用上問題なし
- 即時性が必要な引用RT は Mac 側 `x quote --now` で X API 直叩き経路を使う (race-free、cron 経由しない)

## Secrets (GitHub Settings → Secrets and variables → Actions)

| key | 用途 |
|---|---|
| X_CONSUMER_KEY | OAuth 1.0a Consumer Key |
| X_CONSUMER_SECRET | OAuth 1.0a Consumer Secret |
| X_ACCESS_TOKEN | OAuth 1.0a Access Token (@bobu_reflect 紐付き) |
| X_ACCESS_TOKEN_SECRET | OAuth 1.0a Access Token Secret |

Mac の `~/.claude/secrets/x-bobu_reflect.env` と同期、divergent (片方 rotate 忘れ) は `x status` 末尾の fingerprint と Actions log の fingerprint 目視比較で検出。

## script 同期について

`x_lib.py` / `x_ledger.py` / `x_guard.py` / `x-submit.py` / `x-retro.py` / `x-enqueue.py` は **`~/.claude/scripts/` と `~/projects/bobu-x-queue/scripts/` の 2 箇所に存在**:

- `~/.claude/scripts/` — Mac local 用 (`x` dispatcher が import + `x-now.py` で immediate 経路を実行)
- `~/projects/bobu-x-queue/scripts/` — GitHub Actions runner 用 (`x-submit.py` / `x-retro.py` を呼ぶ)

**改修時は両方更新が必要**。`cp ~/.claude/scripts/x_lib.py ~/projects/bobu-x-queue/scripts/` で同期、commit 直前に `diff` で identical 確認。

## 実行フロー

### 通常 (スケジュール投稿)

1. Mac で `~/.claude/scripts/x post "..." --at <時刻>` → `~/projects/bobu-x-queue/queue/pending/` に JSON + 画像 _media/ にコピー + auto commit + push
2. GitHub Actions submit.yml (5 分毎) が pending を scan、`scheduled_at <= now` の entry を pickup
3. X API v2 で tweet 送信 → done/ に move + tweet_url 追記 → commit + push back
4. 失敗時: 402/429 は Deferred (entry 保持)、他 4xx/5xx は attempts +1、3 回失敗で failed/
5. retro.yml (daily 03:30 JST) が done/ の 7-30 日経過 entry に metrics pull → JSON 更新 → commit + push

### 即時投稿 (`--now`、race-free)

引用RT 等で「瞬間性が必要」な時:

1. Mac で `x quote URL "..." --now` → **pending/ をスキップ**して `x-now.py` が直接 X API 呼び
2. 成功時に done/ に entry 作成 + `.continuity.json` / `.quota.json` 更新 + git push

**Why race-free**: pending/ に書かないので Actions cron が同じ entry を pickup する余地がない。

## 緊急 recovery 手順

### GitHub Actions が complete failure (UI 停止 / outage) した時

```bash
# 1. Mac local で x-submit.py を直接実行 (X_QUEUE_ROOT 経由で bobu-x-queue を見る)
cd ~/projects/bobu-x-queue
git pull --rebase --autostash
X_QUEUE_ROOT="$(pwd)/queue" ~/.venvs/x/bin/python3 scripts/x-submit.py

# 2. 結果を commit + push
git add queue/
git commit -m "emergency/submit: manual flush (Actions outage)"
git push
```

### secret rotate 漏れで Actions だけ 401 になった時

memory `secret_rotation_safe_order` の「多経路拡張手順」参照。`x status` 末尾の fingerprint と Actions log fingerprint を比較。

### failed/ に entry が溜まり続ける時

`~/.claude/scripts/x-failed-check.sh` (daily 08:00 launchd) が osascript notification を出す。手動で `x failed` で詳細確認、各 JSON の `last_error` を読んで処置:

- 401 → secret rotate 経路 (上記)
- 422 → text が X API の制約違反 (1 タグ多すぎ / 重複 / URL 制限) → entry を `cancel` + 起草し直し
- 5xx → 一時障害、`mv queue/failed/<file>.json queue/pending/` で復帰

## 関連

Mac 側の運用 / x CLI 詳細 / 起草 skill は `~/.claude/` 配下 (private)。memory: `x_cli_operational_setup` / `x_cli_claude_executes_directly` / `cli_dispatcher_launchd_compatibility` / `secret_rotation_safe_order`。
