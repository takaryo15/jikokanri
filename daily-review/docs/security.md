# セキュリティ

daily-reviewはローカルファイルを信頼境界として扱います。

- backupは`.env`、token、secret、credential、秘密鍵、symlink、一時ファイルを除外。
- restoreはZip Slip、path traversal、symlink経由、特殊ファイル、重複パス、
  hash不一致、zip bomb相当を拒否。
- restore、rollover、Command APIは期限付きconfirmationとstale状態を検査。
- idempotency keyはrequest hashと結び付け、同じkeyの別内容を拒否。
- JSON保存は一時ファイルと原子的置換を利用。
- CSVは`=`, `+`, `-`, `@`で始まるセルを無害化。
- API入力は文字数、件数、深度を制限し、null byteと不正な制御文字を拒否。
- launchdはshellを使わずProgramArguments配列、絶対実行パス、固定PATH、
  所有者だけが書けるplistを利用。
- runtime dataと個人設定はGit対象外。

backup ZIPも個人データです。公開リポジトリ、共有フォルダ、メールへそのまま
添付しないでください。
