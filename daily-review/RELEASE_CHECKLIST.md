# v1.1.0rc1 Release Checklist

## Release checks

- [ ] Full `pytest` suite passes.
- [ ] `daily-review migrate --yes` succeeds on a v1.0-style workspace.
- [ ] `daily-review v11-check` succeeds.
- [ ] `daily-review doctor` succeeds.
- [ ] `daily-review release-check` succeeds.
- [ ] `scripts/smoke_v11.py` succeeds in a temporary root.
- [ ] Natural-input, handoff, and interrupted-resume E2E flows succeed.
- [ ] Duplicate receive and approved-daily overwrite are rejected.
- [ ] Runtime data and `config/priorities.json` are not tracked by Git.
- [ ] README and CHANGELOG are updated.
- [ ] Version is `1.1.0rc1`.
- [ ] Confirm the final staged/unstaged `git status` before commit.
- [ ] Create and push the annotated `v1.1.0rc1` tag after verification.

## GitHub Release draft

### daily-review v1.1.0rc1

- Natural language input, review drafts, and explicit approval.
- Safe ChatGPT handoff/receive packages with session and prompt-hash checks.
- v1.0-to-v1.1 migration, operational checks, and safe backup/restore.
- No external AI API, automatic approval, or automatic plan changes.

This checklist prepares the release only. It does not create a Git tag or publish a GitHub Release.
