# ChatGPT連携

daily-review自身は外部AI APIへ接続しません。クリップボードまたはJSON
Command APIを境界にして、ChatGPTと安全に往復します。

## 人が確認する往復

```bash
daily-review handoff --copy
daily-review receive --clipboard
daily-review review
daily-review approve
```

## JSON Command API

```bash
daily-review api schema --type request
daily-review api execute --input request.json
```

変更commandは`preview`で差分とconfirmation tokenを取得し、同じrequest内容、
workspace状態、期限内tokenを使う`commit`だけが保存されます。同じ
idempotency keyを異なるrequestへ再利用すると拒否します。commitの再送は
二重保存せず、監査ログへ結果を残します。

入力は件数・文字数・JSON深度を制限し、null byte、不正な制御文字、未知の
timezone、未知のenumを拒否します。
