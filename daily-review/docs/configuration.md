# 設定

```bash
daily-review config path
daily-review config show
daily-review config show --json
daily-review config validate
```

統合設定は`config/app.json`、個別設定は`notifications.json`、`scheduler.json`、
`recovery.json`、`api.json`、`priorities.json`です。欠損項目は互換的な既定値を
使い、未知項目は実行設定として採用しません。

安全な既定値は、Asia/Tokyo、火曜開始、Main最大3件、scheduler無効、restore前
backup有効、未承認指示書の自動確定なしです。

自動化用setup JSONは次の形式です。

```json
{
  "app": {"timezone": "Asia/Tokyo", "week_start": "tuesday", "main_limit": 3},
  "scheduler": {"enabled": false},
  "launchd": {"install": false}
}
```

```bash
daily-review setup --config setup.json --dry-run
daily-review setup --config setup.json --yes
```

既存設定がある場合は停止し、無断上書きしません。
対話setupではデータ保存先、timezone、火曜開始、Main最大3件、朝・夜の時刻、
通知sender、quiet hours、backup先、カテゴリ優先順位、schedulerとlaunchdの利用を
確認します。`launchd.install`は明示的に`true`へし、最終確認した場合だけ登録します。
