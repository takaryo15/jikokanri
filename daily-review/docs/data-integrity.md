# データ整合性検査と安全修復

```bash
daily-review doctor check
daily-review doctor check --format json
daily-review doctor repair --dry-run
daily-review doctor repair --idempotency-key repair-2026-07-16
daily-review doctor report
```

`doctor check`はJSON/UTF-8、日次ファイル名とdate、timestamp、生ログ、Main数、最低限、task ID・status・priority・期限・完了時刻、指示書承認、idempotency hash、confirmation、通知履歴、backup、operation lockを読み取り専用で検査します。

各issueにはcode、severity、path、fixableが含まれます。severityは`info`、`warning`、`error`、`critical`です。

自動修復する範囲は次だけです。

- 既存created_atから欠損updated_atを補完
- Mainの4件目以降をoptionalへ退避
- 不正な負数rollover countを0へ戻す
- 現在期限をoriginal_due_dateとして保持
- failed通知の空errorを「未記録」として補完

生ログの推測、異なる内容の競合解消、日付推測、完了状態変更、タスク削除、idempotency競合の上書きは行いません。repair前には必ず自動バックアップし、修復後に再検査します。同じ冪等性キーの再実行は保存済み結果を返します。

重要操作はワークスペース直下の一時lock directoryで排他します。例外時はlockを解放し、2時間を超えたlockはstaleとして次の重要操作時に回収します。
