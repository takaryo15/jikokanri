# トラブルシューティング

最初に読み取り専用点検を実行します。

```bash
daily-review doctor
daily-review doctor check
daily-review config validate
daily-review scheduler doctor
```

- JSON破損: 対象ファイル名をdoctorで確認し、backupを検証してrestore previewする。
- lock競合: 実行中処理を確認する。stale lockだけ`doctor repair --dry-run`で確認する。
- 指示書未承認: `daily-review show-proposal`で内容を確認し、`approve-plan`を実行する。
- staleレポート: 元データ変更を確認してレポートを再生成する。
- scheduler未実行: `scheduler status`、`scheduler due`、`scheduler doctor`を順に確認する。
- restore競合: mergeで上書きせず、missing-onlyまたは内容確認後のreplaceを選ぶ。

repair、restore、rollover、migrationは、必ずdry-runまたはpreviewから始めてください。
