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

### Safety

- Organization drafts do not modify daily reviews, proposals, final plans, or raw inbox text.
- Unknown or ambiguous sentences are retained as unclassified text instead of being inferred.

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
