# v1.1.0 Release Checklist

## Release checks

- [x] Full `pytest` suite passes twice.
- [x] Three-day operational flow, interrupted resume, and ChatGPT handoff E2E pass.
- [x] Local-date midnight boundary, 05:00 handoff expiry, and Tuesday-to-Monday week boundary pass.
- [x] `daily-review migrate --yes` preserves v1.0-style data and is idempotent.
- [x] Clipboard failure exits safely with a file-input alternative.
- [x] Duplicate receive and approved-daily overwrite are rejected.
- [x] `daily-review doctor`, `release-check`, and `v11-check` pass.
- [x] `scripts/smoke_v11.py` passes in a temporary root.
- [x] Runtime data and `config/priorities.json` are not tracked by Git.
- [x] README and CHANGELOG are updated.
- [x] Version is `1.1.0`.

## GitHub Release draft

### daily-review v1.1.0

- Natural language input, review drafts, and explicit approval.
- Safe ChatGPT handoff/receive packages with session and prompt-hash checks.
- v1.0-to-v1.1 migration, operational checks, and safe backup/restore.
- No external AI API, automatic approval, or automatic plan changes.

The annotated `v1.1.0` tag is created only after this checklist and the final clean-tree check pass.
