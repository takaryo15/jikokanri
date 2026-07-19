# launchd

macOSではlaunchdを推奨します。生成されるplistは`~/Library/LaunchAgents/com.daily-review.scheduler.plist`です。

```bash
daily-review scheduler install --dry-run
daily-review scheduler install
daily-review scheduler status
```

plistは解決済みの`daily-review`実行パス、プロジェクトルート、PATH、ログパスを保持し、`ProgramArguments`配列で`daily-review scheduler run-due --format json`を呼びます。日本語や空白を含むパスをshell文字列として連結しません。poll間隔は`config/scheduler.json`の`poll_interval_minutes`です。

標準出力は`logs/scheduler.log`、標準エラーは`logs/scheduler-error.log`です。jobごとの日付・時刻判定はplistに複製しません。

```bash
daily-review scheduler uninstall --dry-run
daily-review scheduler uninstall
```

uninstallはlaunchd登録とplistだけを削除し、`data/`、通知履歴、scheduler履歴は削除しません。cronを使う場合は`daily-review scheduler cron-example`の出力をユーザー自身で確認して登録してください。
