# 毎日の使い方

通常は次の2つを入口にします。

```bash
daily-review home
daily-review close-day --clipboard --dry-run
```

朝は`home`で確定指示書、Main（最大3件）、最低限、期限超過、前日からの
未完了、scheduler、最後のbackup、次の操作を確認します。機械処理では
`daily-review home --format json`を使えます。

夜は`close-day --dry-run`で保存予定を確認してから、`--dry-run`を外して
保存します。生ログと構造化レビューを保存しても、明日の指示書は提案版です。
確定には明示承認が必要です。

```bash
daily-review approve-plan --date YYYY-MM-DD
```

自然文中心の運用では`handoff`と`receive`も利用できます。

```bash
daily-review handoff --copy
daily-review receive --clipboard
daily-review reflect --resume
```

原文、未承認draft、承認済み確定版を別々に保存します。
