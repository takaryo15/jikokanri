# v1.0.0 Release Checklist

## Release checks

- [x] Full `pytest` suite passes (141 tests).
- [x] `daily-review --help` and `daily-review --version` work.
- [x] `daily-review home`, `summary`, `start`, `doctor`, and `release-check` work.
- [x] Initial-use, night-review, approval, result-recording, carryover, weekly/monthly, and backup/restore workflows are covered by tests.
- [x] Root auto-detection works from the repository root and project directory.
- [x] Invalid dates and broken JSON are covered without normal traceback output.
- [x] `git diff --check` passes.
- [x] `data/`, `logs/`, `backups/`, and ZIP archives are ignored by Git; previously tracked generated reports are staged for untracking without deleting local files.
- [x] README and CHANGELOG have been updated for v1.0.0.
- [x] Editable install verification passed in a temporary virtual environment.
- [ ] Confirm the final staged/unstaged `git status` before commit.
- [ ] Create the `v1.0.0` Git tag after approval.
- [ ] Create the GitHub Release after approval.

## GitHub Release draft

### daily-review v1.0.0

- Local daily review, approved tomorrow plans, and task-result recording.
- Tuesday-to-Monday weekly and calendar-monthly reports.
- Safe ZIP backup, validated restore with dry-run, and read-only doctor checks.
- No external API, automatic approval, automatic carryover, or automatic plan changes.

This checklist prepares the release only. It does not create a Git tag or publish a GitHub Release.
