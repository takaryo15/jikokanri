# v1.0.0 Release Checklist

## Release checks

- [ ] `pytest` passes.
- [ ] `daily-review --help` works.
- [ ] `daily-review --version` prints `daily-review 1.0.0`.
- [ ] Run help for `backup`, `restore`, and `doctor`.
- [ ] Run `daily-review doctor` against a real workspace.
- [ ] Create a backup with `daily-review backup`.
- [ ] Inspect it with `daily-review restore path/to/backup.zip --dry-run`.
- [ ] Check `daily-review weekly` and `daily-review monthly`.
- [ ] Read README commands against `daily-review --help`.
- [ ] Confirm `git status` is clean.
- [ ] Create the `v1.0.0` Git tag after approval.
- [ ] Create the GitHub Release after approval.

## GitHub Release draft

### daily-review v1.0.0

- Local daily review, approved tomorrow plans, and task-result recording.
- Tuesday-to-Monday weekly and calendar-monthly reports.
- Safe ZIP backup, validated restore with dry-run, and read-only doctor checks.
- No external API, automatic approval, automatic carryover, or automatic plan changes.

This checklist prepares the release only. It does not create a Git tag or publish a GitHub Release.
