# ChatGPT Command API

## 目的と範囲

Command API version `1`は、ChatGPTやローカルプログラムがdaily-reviewのユースケースをJSONでpreviewし、ユーザー確認後にcommitするための内部APIです。HTTPサーバーや外部LLM呼び出しは含みません。

実装と同じJSON Schemaは次のコマンドで取得できます。

```bash
daily-review api schema --type request
daily-review api schema --type response
daily-review api schema --command create_daily_review
```

## Request

主要フィールドは`version`、`request_id`、`idempotency_key`、`mode`、`timezone`、`effective_date`、`source`、`raw_input`、`commands`、`execution_policy`です。書き込みpreviewには`idempotency_key`、commitには同じ内容とconfirmation tokenが必要です。`mode`は`preview`または`commit`、`execution_policy`は既定の`atomic`または`best_effort`です。

```json
{
  "version": "1",
  "request_id": "req_example_001",
  "idempotency_key": "review-2026-07-15",
  "mode": "preview",
  "timezone": "Asia/Tokyo",
  "effective_date": "2026-07-15",
  "source": "chatgpt",
  "raw_input": "今日の振り返り...",
  "commands": [
    {
      "type": "create_daily_review",
      "payload": {
        "date": "2026-07-15",
        "done": ["自己管理システムの開発を進めた", "筋トレに行った"],
        "not_done": ["院試勉強"],
        "causes": ["薬で眠かった", "就寝が遅かった"],
        "tomorrow": ["院試過去問を1年分進める"],
        "minimum": ["過去問を1問解く"],
        "journal": "開発はかなり進んだ。"
      }
    }
  ]
}
```

## Response

Responseは`status`、`summary`、`changes`、`warnings`、`errors`、`confirmation_required`、`confirmation_token`、`result`を分離します。エラーは`code`、`message`、`field`、`details`、`recoverable`を持ちます。

主なstatusは`preview_ready`、`committed`、`partial_success`、`success`、`idempotent_replay`、`needs_clarification`、`confirmation_required`、`conflict`、`input_error`です。

## Command一覧

- `create_daily_review`: 生ログ、日次レビュー、翌日の未承認指示書案
- `create_task`: API管理タスクの作成
- `update_task`: IDまたは一意なタイトル候補による更新
- `complete_task`: 完了結果の記録
- `reschedule_task`: 期限変更
- `list_tasks`: 既存タスクとAPIタスクの統合取得
- `generate_instruction`: タスクから未承認指示書案を生成
- `revise_instruction`: 未承認案のMain・最低限・optional修正
- `approve_instruction`: 明示commandによる確定版作成
- `get_instruction`: 対象日の確定版または提案版取得

`$commands.0.result.task_id`のような単純な前方参照をpayloadの文字列値に使用できます。後方参照や式は利用できません。

## preview / commit

previewは日次・タスク・指示書を変更しません。プロセスをまたいでcommitするため、`data/api/confirmations`、`data/api/idempotency`、`data/api/audit`には補助記録を保存します。

confirmation tokenはrequest hash、changes hash、対象データstate hash、対象日、idempotency key、発行時刻、有効期限に結び付いています。既定期限は30分です。対象データがpreview後に変化すると`PREVIEW_STALE`になり、再previewが必要です。

```bash
daily-review api execute --input request.json --pretty
daily-review api execute --input request.json --mode commit --confirmation-token confirm_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx --pretty
cat request.json | daily-review api execute --stdin
```

CLIの`--mode`と`--confirmation-token`はJSON内の値を上書きします。stdoutは成功・エラーともJSONだけです。

## idempotency

request hashは`request_id`、`mode`、`confirmation_token`を除いた正規化JSONからSHA-256で生成します。同じkey・同じ内容のcommit再送は保存せず`idempotent_replay`を返します。同じkey・異なる内容は`IDEMPOTENCY_CONFLICT`です。previewが期限切れまたはstaleになった場合は、同じ内容で再previewすると新しいtokenを発行します。

## 一括実行

commandsは入力順に評価されます。既定の`atomic`は1件でも失敗すれば主要データを保存しません。`best_effort`は成功した変更だけを確認対象にし、commit結果を`partial_success`にします。日次JSON、inbox、Markdown、APIタスク、バックアップ、confirmation、idempotencyは一時ファイルとrollback付きの一括置換で保存します。

## 自然言語正規化

```bash
cat review.txt | daily-review parse review --stdin --date 2026-07-15
cat review.txt | daily-review parse review --stdin --date 2026-07-15 --preview --idempotency-key review-2026-07-15
cat review.txt | daily-review parse review --stdin --date 2026-07-15 --commit --idempotency-key review-2026-07-15 --confirmation-token confirm_xxx
```

明確な見出し、箇条書き、否定、最低限、相対日付だけを保守的に解釈します。分類不能な文章は`unclassified`、原文は`raw_input`とinboxへ残します。曖昧な解釈はwarningです。

## エラーコードと終了コード

主なコードは`INVALID_REQUEST`、`UNSUPPORTED_VERSION`、`UNKNOWN_COMMAND`、`INVALID_PAYLOAD`、`INVALID_DATE`、`DUPLICATE_REVIEW`、`TASK_NOT_FOUND`、`TASK_AMBIGUOUS`、`INSTRUCTION_NOT_FOUND`、`INSTRUCTION_ALREADY_APPROVED`、`IDEMPOTENCY_CONFLICT`、`CONFIRMATION_REQUIRED`、`CONFIRMATION_INVALID`、`CONFIRMATION_EXPIRED`、`PREVIEW_STALE`、`STORAGE_ERROR`です。

- `0`: 成功、preview、読み取り、partial_success
- `2`: 入力・payloadエラー
- `3`: 確認・対象特定が必要
- `4`: 競合、stale、tokenエラー
- `5`: 保存エラー

## 監査とセキュリティ

```bash
daily-review api history
daily-review api history --date 2026-07-15
daily-review api history --request-id req_example_001
daily-review api history --idempotency-key review-2026-07-15
```

監査ログはrequest hashとraw input hashだけを保存し、原文を二重保存しません。JSONから任意の保存パスは指定できません。commandは最大20件、raw inputは最大20,000文字、各配列は最大100件です。個人設定はGit管理外の`config/api.json`、配布例は`config/api.example.json`です。

曖昧なタスク候補は勝手に変更せず候補一覧を返します。Mainは先頭3件までで、4件目以降はoptionalまたはbacklogへ保持します。指示書は`approve_instruction`なしに確定されません。
