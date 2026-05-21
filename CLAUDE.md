# bobu-x-queue (Claude 作業メモ)

@bobu_reflect の予約投稿クラウド cron。Public repo + GitHub Actions schedule（5 分毎）で投稿エンジンが Mac 不要で走る。設計詳細は `README.md`、本ファイルは Claude が編集時に踏むべき判断軸。

## 状態（2026-05-21〜）

- **setup 当日**: 旧 launchd 経路（`~/.claude/x-queue/`）から本 repo 経路に完全統合（`ad62e43 migrate:` 参照）
- **投稿エンジン**: GitHub Actions `submit.yml`（cron `*/5 * * * *`）= Mac 落としても投稿が走る
- **CLI 入口**: `~/.claude/scripts/x`（dispatcher）→ scripts/x-{enqueue,submit,retro}.py
- **シリーズ運用**: 「映す世界を間違えた」daily 22:00 JST 固定（`x post --source x-post-series`）
- **健全性 watch**: 5/21 22:43 時点で schedule trigger run は **ゼロ**（全 4 件 workflow_dispatch）= 今日 setup 当日の初動 cron 未起動の可能性、明朝に schedule run が出るか観察必須

## 仕組み（Claude が触る時の前提）

```
Mac:                          GitHub Actions:
  x post --image PNG          submit.yml (cron 5min)
    ↓                            ↓ checkout main
  x-enqueue.py                 x-submit.py
    ↓                            ↓ pending/*.json 走査
  queue/pending/*.json         ↓ scheduled_at <= now なら投稿
    + queue/_media/*.png         ↓ done/ に move + tweet_url 追記
    ↓ git add + commit + push    ↓ git commit + push back
  GitHub repo                  GitHub repo
```

- queue 状態は git で同期（pending/done/failed/_media 全部 commit 対象、`.gitignore` は `.env*` / `secrets/` / `logs/` のみ）
- 重複投稿ガード: `.submit.lock` flock + `concurrency: x-queue-write` (GHA) + `attempts < MAX_ATTEMPTS=3`

## 次の一歩

1. **schedule cron 死活観察**: 翌朝 (5/22) までに `gh run list --workflow=submit.yml --json event` で `"event":"schedule"` が出るか確認。出なければ public repo + Actions 権限 + cron 構文を再点検
2. **retro 振り返り運用**: `x retro` 手動 or `retro.yml` 03:30 JST 自動で done/*.json の metrics 更新。投稿後 7-30 日の振り返り 0 件状態を抜ける（現状 0）
3. **シリーズ「映す世界を間違えた」継続**: 残 pending 8 件（5/22-6/8 22:00 default）、最遅予約 6/8 → 最低 6/9 までに次 batch 起草
4. **x-now.py 動線**: `x post/quote --now` は Mac 側 X API 直叩き（GHA 経路 bypass、即時性が要る引用RT 用）

## 編集時の判断軸

- **`scripts/x_lib.py` の `QUEUE_ROOT`**: env var で path override 可。Mac と GHA runner で異なるパスに展開されるので、JSON 内の絶対パスは GHA で解決不可
- **`image_path` の絶対パス問題（解決済、2026-05-21）**: 2 段階で fix。(1) `5bfae47` x-submit.py に basename fallback、(2) x-enqueue.py が今後 `_media/...` 相対パスで書く根本修正 + x-submit.py が `is_absolute()` で新旧分岐。既存の絶対パス pending JSON は fallback で投稿可、新規 enqueue は最初から相対 = Mac/Linux 両方で動く
- **Secrets rotation**: `X_CONSUMER_KEY` 等の指紋（先頭 8 文字）が `x status` で出る = GHA log と突合し divergent 検出。rotation 手順は memory `secret_rotation_safe_order`
- **commit/push 委任**: bot 自身が main 直 push する routine repo だが、Claude による main 直 push は `commit_delegation_policy` の例外 = user 明示承認が要る（5/21 N=1 で確認）

## 関連 memory

- `x_cli_operational_setup` — PPU only / 自動チャージ OFF / 月コスト推定
- `x_cli_claude_executes_directly` — `x post/quote/cancel/now/status` は Claude が Bash 直接実行
- `x_post_multiple_entrypoints_sync` — x-submit と x-now の 2 経路、posted 後処理は両方同期必須
- `secret_paste_target_terminal_vs_chat` — secret 貼り付けは terminal、Claude チャット欄禁止
