# bobu-x-queue (Claude 作業メモ)

@bobu_reflect の予約投稿クラウド cron。Public repo + GitHub Actions schedule（5 分毎）で投稿エンジンが Mac 不要で走る。設計詳細は `README.md`、本ファイルは Claude が編集時に踏むべき判断軸。

## 状態（2026-05-21〜）

- **setup 当日**: 旧 launchd 経路（`~/.claude/x-queue/`）から本 repo 経路に完全統合（`ad62e43 migrate:` 参照）
- **投稿エンジン**: GitHub Actions `submit.yml`（cron `*/5 * * * *`）= Mac 落としても投稿が走る
- **救援経路**: Mac `com.higashishota.x-submit-kick` (5 分 interval) が `gh workflow run submit.yml` を発火 = schedule cron が止まっても safety net
- **CLI 入口**: `~/.claude/scripts/x`（dispatcher）→ scripts/x-{enqueue,submit,retro}.py
- **シリーズ運用**: 「映す世界を間違えた」daily 22:00 JST 固定（`x post --source x-post-series`）
- **Obsidian 振り返り経路（2026-05-22 追加）**: `scripts/vault_export.py` が投稿成功時に `vault-export/YYYY-MM/*.md` + `_assets/` を書く → submit.yml が commit & push → Mac `com.higashishota.bobu-vault-sync` (15 分 interval) が `git pull` + `rsync` で `~/Documents/メイン/projects/X-posts/` に同期 → iPhone Obsidian Sync で振り返り可能
- **稼働実績**: 5/21 22:43「自己肯定感を高めよう」シリーズ投稿成功 (workflow_dispatch 経由)、5/22 まで schedule trigger run は要観察

## 仕組み（Claude が触る時の前提）

```
Mac:                          GitHub Actions:
  x post --image PNG          submit.yml (cron 5min + workflow_dispatch 救援)
    ↓                            ↓ checkout main
  x-enqueue.py                 x-submit.py
    ↓                            ↓ pending/*.json 走査
  queue/pending/*.json         ↓ scheduled_at <= now なら投稿
    + queue/_media/*.png         ↓ done/ に move + tweet_url 追記
    ↓ git add + commit + push    ↓ vault_export.export_entry(data) 呼ぶ
  GitHub repo                  ↓ vault-export/ に MD + 画像コピー
    ↑                            ↓ vault_export.py backfill (idempotent 救援)
  bobu-vault-sync (15min)      ↓ git add queue/ vault-export/ + commit + push
    ↓ git pull + rsync         GitHub repo
  ~/Documents/メイン/projects/X-posts/
    ↓ Obsidian Sync Plus
  iPhone Obsidian
```

- queue 状態は git で同期（pending/done/failed/_media 全部 commit 対象、`.gitignore` は `.env*` / `secrets/` / `logs/` のみ）
- vault-export/ も git 管理対象（commit 経由で Mac に配信）
- 重複投稿ガード: `.submit.lock` flock + `concurrency: x-queue-write` (GHA) + `attempts < MAX_ATTEMPTS=3`
- vault export 整合性: `vault_export.py` は `_already_exported()` で idempotent、二重書き込みなし

## 次の一歩

1. **Mac off 時の確実性 (2026-05-22 cron 再設計済)**: GHA schedule cron は public docs 通り高負荷時に大幅遅延する (5/21 14h で 1 件 trigger 観測)。`*/5 * * * *` から「22:00 JST 帯集中 (5 分間隔 7 attempts) + 3h 毎安全網 (8 attempts)」のデュアル設計に変更 (総 15/day、GHA deprioritize 回避)。**ただし 100% 保証ではない**: 本気で Mac off で確実に動かすなら Option B = cron-job.org / healthchecks.io から GitHub fine-grained PAT (`actions:write` on bobu-x-queue のみ) で `workflow_dispatch` 外部 trigger。PAT 作成 + 外部サービス sign-up は user 操作必須 (GitHub UI / cron-job.org)
2. **vault export 経路の本番稼働確認**: 5/22 投稿成立時に vault-export/2026-05/*.md が GHA 経路でも書かれて push されるか観察。Mac 側 `~/.claude/logs/routines/bobu-vault-sync.log` で `rsync ok` が出るか確認
3. **retro 振り返り運用**: `x retro` 手動 or `retro.yml` 03:30 JST 自動で done/*.json の metrics 更新。投稿後 7-30 日の振り返り 0 件状態を抜ける（現状 0）
4. **シリーズ「映す世界を間違えた」継続**: 残 pending 8 件（5/22-6/8 22:00 default）、最遅予約 6/8 → 最低 6/9 までに次 batch 起草
5. **x-now.py 動線**: `x post/quote --now` は Mac 側 X API 直叩き（GHA 経路 bypass、即時性が要る引用RT 用）。vault export は Mac の `~/.claude/scripts/x_vault_export.py` が直接 `~/Documents/メイン/projects/X-posts/` に書く。bobu-vault-sync の rsync は `--delete` なしで両経路の file が共存可

## 編集時の判断軸

- **`scripts/x_lib.py` の `QUEUE_ROOT`**: env var で path override 可。Mac と GHA runner で異なるパスに展開されるので、JSON 内の絶対パスは GHA で解決不可
- **`image_path` の絶対パス問題（解決済、2026-05-21）**: 2 段階で fix。(1) `5bfae47` x-submit.py に basename fallback、(2) x-enqueue.py が今後 `_media/...` 相対パスで書く根本修正 + x-submit.py が `is_absolute()` で新旧分岐。既存の絶対パス pending JSON は fallback で投稿可、新規 enqueue は最初から相対 = Mac/Linux 両方で動く
- **vault export の path resolve**: `scripts/vault_export.py` も `X_QUEUE_ROOT` env var で resolve、`image_path` の絶対/相対と basename fallback は x-submit.py と同じロジックで処理。リポ内 `vault-export/` 出力 = Mac/Linux 両方で動く
- **Secrets rotation**: `X_CONSUMER_KEY` 等の指紋（先頭 8 文字）が `x status` で出る = GHA log と突合し divergent 検出。rotation 手順は memory `secret_rotation_safe_order`
- **commit/push 委任**: bot 自身が main 直 push する routine repo だが、Claude による main 直 push は `commit_delegation_policy` の例外 = user 明示承認が要る（5/21 N=1, 5/22 N=2 で確認）

## 関連 memory

- `x_cli_operational_setup` — PPU only / 自動チャージ OFF / 月コスト推定
- `x_cli_claude_executes_directly` — `x post/quote/cancel/now/status` は Claude が Bash 直接実行
- `x_post_multiple_entrypoints_sync` — x-submit と x-now の 2 経路、posted 後処理は両方同期必須
- `secret_paste_target_terminal_vs_chat` — secret 貼り付けは terminal、Claude チャット欄禁止
