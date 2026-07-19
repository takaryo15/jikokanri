# タスクと繰越

```bash
daily-review tasks list
daily-review tasks list --due overdue
daily-review rollover preview --date YYYY-MM-DD
```

繰越は必ずpreviewから始めます。applyにはpreviewで発行されたconfirmation
tokenが必要です。

```bash
daily-review rollover apply --date YYYY-MM-DD --confirmation-token TOKEN
daily-review rollover history
```

completed、cancelled、blocked等は自動繰越しません。同じタスクを同じ日へ
再適用しても複製せず、元期限と繰越回数を保持します。長期未完了には分解や
最低限縮小の候補を表示しますが、ユーザー承認なしにMainへ昇格しません。
