# 未完了タスクの繰越

rolloverは新しいタスクを作りません。既存タスクへ次のmetadataを追記します。

- `original_due_date`
- `current_due_date`
- `planned_date` / `next_action_date`
- `first_planned_date` / `latest_planned_date`
- `rollover_count` / `consecutive_unfinished_days`
- `last_rolled_over_at` / `last_rolled_from_date`
- `rollover_policy` / `rollover_reason`

```bash
daily-review rollover preview --date 2026-07-16 --format json
daily-review rollover apply --date 2026-07-16 \
  --confirmation-token rollover_confirm_xxx \
  --idempotency-key rollover-2026-07-16
```

前日Main・最低限付きタスク・期限超過・前日期限・automatic/until_completed等を候補にします。completed、cancelled、archived、deleted、someday、未解除blocked、never、登録済み、上限到達は除外します。

既定では3回目に警告、5回目に`split_suggested`、7回目に`needs_confirmation`として自動Mainから外します。Mainは`config/priorities.json`と優先度を使って最大3件です。最低限の縮小は候補として返すだけで、ユーザー入力を上書きしません。

applyはpreviewのtask state hashを再検証し、同日の既定idempotency keyで二重適用を防ぎます。履歴は`data/rollover/history.json`へタスク単位で保存します。
