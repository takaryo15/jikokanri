# Scheduler

daily-reviewのschedulerは、launchdやcronから一定間隔で`daily-review scheduler run-due`を呼ぶpoll方式です。jobの時刻ロジックはPython側だけにあります。

## 安全な開始

`config/scheduler.example.json`を`config/scheduler.json`へコピーし、内容を確認して`enabled`を`true`にします。設定が存在しない旧環境ではschedulerは無効で、自動起動もしません。

```bash
daily-review scheduler status
daily-review scheduler due --at 2026-07-20T21:00:00+09:00 --format json
daily-review scheduler run-due --at 2026-07-20T21:00:00+09:00 --dry-run --format json
```

## 判定

- daily jobは日付、weekly jobは火曜から月曜、monthly jobは年月をschedule slotにします。
- 同じjob・slotが成功済みなら通常は再実行しません。
- 予定時刻後もgrace period内ならmissed jobとして1回実行します。
- retry可能なI/O、通知、lock失敗は、設定されたbackoff後の次回pollで再試行します。常駐sleepはしません。
- quiet hours中の非緊急通知は終了時刻までdeferredとして履歴に残します。
- `--force`は手動job再実行用ですが、自動承認、rollover apply、データ検証は迂回しません。

履歴は`data/scheduler/history.json`に保存されます。lockは`data/scheduler/locks`にあり、設定時間を超えたstale lockだけをdoctor repairの対象にします。

jobのPython内処理を強制終了するprocess-level timeoutは実装していません。代わりにjobのtimeout設定をlockのstale判定に用い、launchdの次回pollとdoctorで回復可能にしています。
missed run policyは`run_once`、`skip`、`notify_only`です。複数の過去slotを一括再生する
`run_all`はv1.3の対象外で、設定すると検証エラーになります。
