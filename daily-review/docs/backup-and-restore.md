# バックアップと復元

## バックアップ対象

フルバックアップは`data/`、`logs/`、`templates/`、安全な`config/`を対象にします。`.env`、token・secret・credential・秘密鍵、symlink、一時ファイル、`data/backups/`、バックアップZIP自身は除外します。

```bash
daily-review backup create --dry-run
daily-review backup create --output /safe/path --idempotency-key backup-2026-07-16
daily-review backup verify /safe/path/example.zip
daily-review backup list --format json
```

ZIP内の`manifest.json`にはformat version、backup ID、app/data version、作成日時、timezone、件数、各ファイルのsizeとSHA-256が入ります。verifyはmanifest外ファイル、欠損、重複、絶対パス、`../`、symlink・特殊ファイル、異常な展開量・圧縮率を拒否します。

## preview-first復元

```bash
daily-review restore preview backup.zip --mode merge --format json
daily-review restore apply backup.zip --mode merge \
  --confirmation-token restore_confirm_xxx \
  --idempotency-key restore-2026-07-16
```

`backup create`、`backup delete --apply`、`restore apply`は冪等性キーに対応します。同じキーと同じ要求は二重作成・二重削除・二重復元せず、異なる要求へのキー再利用は拒否します。

- `merge`: 新規だけ追加し、異なる既存内容は競合
- `missing-only`: 新規だけ追加し、既存はスキップ
- `replace`: previewに表示された更新・削除候補を適用

tokenはbackup SHA-256、mode、差分hash、現在状態hash、期限に結び付きます。apply前にZIPとtokenを再検証し、現在状態が変わっていればstaleとして拒否します。applyは現在状態の自動バックアップに成功した後だけ進みます。

復元対象JSONは一時ワークスペースで整合性検査し、重大な問題を含む変更は本体へ書き込みません。保存途中の失敗時は置換済みファイルを元のbytesへ戻します。復元履歴は`data/restore/history.json`です。

従来の`daily-review restore BACKUP_FILE --dry-run`も互換のため残しています。通常運用ではconfirmation tokenを使う新フローを推奨します。
