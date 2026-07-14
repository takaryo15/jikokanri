# Changelog

## Unreleased

### Added

- Natural-language raw-text storage through `daily-review input`.
- Per-day raw input inboxes in `data/inbox`.

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
