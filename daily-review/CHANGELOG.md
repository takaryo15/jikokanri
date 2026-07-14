# Changelog

## Unreleased

### Added

- Natural-language raw-text storage through `daily-review input`.
- Per-day raw input inboxes in `data/inbox`.
- Rule-based inbox organization through `daily-review organize`.
- Per-day review drafts in `data/drafts` with source-entry IDs and parser versioning.
- 今日と明日のMain候補、および振り返り・原因・改善・日記・未分類のルールベース分類.
- 整理ドラフトを確認する`daily-review review`、編集する`daily-review edit-draft`、承認する`daily-review approve`.
- ドラフトの承認状態、revision、編集履歴、および承認済みドラフトから既存日次データへの互換的な保存.
- 再承認前の日次JSONバックアップ.
- 自然文入力から承認までを一括実行する`daily-review reflect`.
- 未承認ドラフトから再開する`reflect --resume`、安全条件付きの自動承認`reflect --yes`、統合フローのJSON出力.
- 中断・失敗後の復旧案内、および状態に応じたhomeの次操作表示.
- ChatGPTの構造化JSONを安全に取り込む`daily-review chat-import`と、形式を表示する`daily-review chat-prompt`.
- `chat-schema-1.0`による検証、未知フィールドの警告、インポートハッシュによる重複検知、強制置換前のドラフトバックアップ.
- ChatGPTとの日次往復をまとめる`daily-review chat`、`--prompt-only`、`--copy-prompt`、`--import-only`.
- 動的なChatGPTプロンプト、`data/sessions`による補助セッション管理、`config/priorities.json`の優先順位設定.

### Safety

- Organization drafts do not modify daily reviews, proposals, final plans, or raw inbox text.
- Unknown or ambiguous sentences are retained as unclassified text instead of being inferred.
- ChatGPTの構造化入力は確認用ドラフトとして保存し、明示的な承認なしに日次記録を変更しません.
- ChatGPTセッションは補助情報であり、日次データやドラフトより優先されません.

## 1.0.0 - 2026-07-14

### Added

- Night-review storage with preserved raw logs and structured reviews.
- Tomorrow-plan proposal and explicit approval workflow.
- A maximum of three Main items and a required minimum line for every task.
- Task-result recording and carryover candidates without automatic plan changes.
- `home`, `start`, `summary`, `next`, and `status` for daily operation.
- Tuesday-to-Monday weekly reports and calendar-monthly reports.
- `doctor`, `backup`, `restore`, and `release-check` local safety tools.
- Project-root auto-detection from the repository root or `daily-review/` directory.

### Safety

- Personal `data/`, `logs/`, `backups/`, and ZIP archives are excluded from Git.
- JSON validation and UTF-8 atomic writes protect saved daily records.
- Backups include manifests and SHA-256 hashes; restore validates paths and hashes.
- No external APIs, automatic approvals, automatic carryover, or automatic plan changes.
