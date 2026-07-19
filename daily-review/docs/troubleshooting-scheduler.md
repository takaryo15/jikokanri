# Scheduler troubleshooting

最初に次を確認します。

```bash
daily-review scheduler status
daily-review scheduler doctor
daily-review scheduler history --status failed
daily-review scheduler run-due --dry-run
```

- `SCHEDULER_DISABLED`: `config/scheduler.json`を確認してください。旧環境の安全な既定値は無効です。
- `EXECUTABLE_NOT_FOUND`: 現在のvenvまたはpipx環境で`daily-review --version`が動くか確認してください。
- `LAUNCHD_NOT_INSTALLED`: 自動起動が必要な場合だけ`install --dry-run`後にinstallしてください。
- `PLIST_MISMATCH`: 実行パス、ルート、poll間隔変更後は内容を確認して再installしてください。
- `STALE_SCHEDULER_LOCK`: 実行中プロセスがないことを確認し、`scheduler doctor --repair --dry-run`から始めてください。
- `FAILED_JOB_PENDING` / `RETRY_OVERDUE`: historyのerror codeと`retry_at`を確認してください。

JSON履歴が壊れている場合は自動修復せずERRORにします。backupを検証し、実データへのrestore前に必ずdry-runしてください。
