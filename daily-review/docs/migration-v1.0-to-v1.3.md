# v1.0からv1.3への移行

既存の日次・週次・月次JSONは書き換えず、不足ディレクトリ、配布テンプレート、
migration履歴だけを追加します。旧JSONの省略可能フィールドと未知フィールドは
そのまま読み込めます。

## リハーサル

```bash
daily-review backup create
daily-review backup verify BACKUP_FILE
daily-review migrate check
daily-review migrate apply --dry-run
```

実データでは、backupを別のテスト用ディレクトリへ復元してからmigrationを
試してください。

```bash
daily-review restore preview BACKUP_FILE --root TEST_ROOT --mode missing-only
daily-review migrate apply --root TEST_ROOT --yes
daily-review doctor --root TEST_ROOT
```

## 本番適用

```bash
daily-review migrate apply --yes
daily-review doctor
```

`migrate apply`は適用前にフルbackupを作成・検証します。問題がある場合は、
そのbackupを`restore preview`して差分を確認し、confirmation tokenを使って
復元します。コードをv1.0へ戻さなくてもv1.0形式のデータを読み込めます。
