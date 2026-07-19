# daily-review

`daily-review` は、毎日の振り返り、翌日の指示書、実行結果、目標ロードマップをローカルのJSONとMarkdownに残すPython CLIです。v1.3ではChatGPTとの安全な受け渡し、タスク、backup/restore、レポート、scheduler、migrationを日常運用へ統合しました。変更の確定には明示承認が必要です。

毎日の入口は次のコマンドです。

```bash
daily-review home
```

初めて使う場合は、[はじめに](docs/getting-started.md)を参照してください。

## 利用ガイド

- [毎日の使い方](docs/daily-workflow.md)
- [ChatGPT連携](docs/chatgpt-integration.md)
- [タスクと繰越](docs/tasks-and-rollover.md)
- [週次・月次レポート](docs/weekly-monthly-reports.md)
- [backupとrestore](docs/backup-and-restore.md)
- [scheduler](docs/scheduler.md)
- [設定](docs/configuration.md)
- [v1.0からv1.3への移行](docs/migration-v1.0-to-v1.3.md)
- [トラブルシューティング](docs/troubleshooting.md)
- [セキュリティ](docs/security.md)
- [v1.3.0リリースノート](docs/releases/v1.3.0.md)

## v1でできること

- 生ログ、日記、整形済み振り返りのローカル保存
- 明日の指示書の提案、手動承認、結果記録
- 日次の入口 `home`、状況一覧 `summary`、夜の案内 `start`
- 火曜始まり・月曜終わりの週次、暦月の月次振り返り
- 読み取り専用の `doctor` と `release-check`
- SHA-256 manifest付きのバックアップと安全な復元

## v1.1以降: 自然文入力

## v1.1 RCの毎晩の基本操作

```bash
daily-review handoff --copy
daily-review receive --clipboard --approve
daily-review home
```

途中で止まった場合は `daily-review chat --resume`、初回または更新した後は `daily-review migrate` と `daily-review v11-check`、トラブル時は `daily-review doctor` を実行します。`migrate` は不足している保存先だけを作成し、日次・週次・月次の既存データを変更しません。

## v1.2: 目標管理

目標は日次データとは独立して、`data/goals/items/`の1目標1ファイルとして保存します。物理削除は行わず、編集・状態変更・アーカイブの前には`data/backups/goals/`へ退避します。

```bash
daily-review goal add --title "大学院入試に合格する" --level medium --category "院試" --start-date 2026-07-14 --due-date 2026-08-31
daily-review goal list
daily-review goal show goal-xxxxxxxx
daily-review goal edit goal-xxxxxxxx --due-date 2026-08-30
daily-review goal status goal-xxxxxxxx completed
daily-review goal archive goal-xxxxxxxx --yes
```

- `vision`: 人生・数年単位の方向性
- `long`: 半年〜3年程度の長期目標
- `medium`: 1〜6か月程度の中期目標
- `short`: 1日〜1か月程度の短期目標

`--qualitative`には「説明できる」のように達成を判断できる文章を指定します。`--metric`は`name|unit|baseline|target|direction`形式で、`increase`、`decrease`、`maintain`、`boolean`を指定できます。定性・定量指標から進捗を自動計算し、指標がない場合だけ`--manual-progress`を使えます。親目標には存在する未アーカイブ目標だけを指定でき、自己参照・循環参照・不自然な上位方向のlevel関係は拒否されます。

### 目標設計をChatGPTと往復する

`goal design`は曖昧な原文と回答をセッションに保存し、ChatGPTから受け取ったJSONを未適用proposalとして確認できます。外部APIには接続せず、`apply --yes`を実行するまでgoalを作成しません。

```bash
daily-review goal design --text "大学院入試に合格したい"
daily-review goal design answer design-xxxxxxxx --answer "2026-08-31まで"
daily-review goal design prompt design-xxxxxxxx
daily-review goal design receive design-xxxxxxxx --file goal-proposal.json
daily-review goal design review design-xxxxxxxx
daily-review goal design apply design-xxxxxxxx --yes
```

適用はgoal JSONとdesign statusを同じ複数ファイル更新で保存します。同じdesignの二重適用は拒否します。

### マイルストーンと実行ロードマップ

目標は、期限・依存関係・実行ステップを持つマイルストーンへ分解できます。マイルストーンやステップの変更前には、目標JSON全体を`data/backups/goals/`へ退避します。削除は行わず、不要な項目は`cancelled`に変更してください。

```bash
daily-review goal milestone add goal-xxxxxxxx --title "過去問5年分を1周する" --due-date 2026-07-31
daily-review goal milestone step add goal-xxxxxxxx mile-xxxxxxxx --title "2025年度の過去問を解く" --minimum "問題文を読み、解法方針だけ書く"
daily-review goal milestone step status goal-xxxxxxxx mile-xxxxxxxx step-xxxxxxxx doing
daily-review goal roadmap goal-xxxxxxxx
daily-review goal next goal-xxxxxxxx
```

`goal roadmap`は目標・マイルストーン・ステップの現在地を表示し、`goal next`は完了済み依存関係、期限、順序を考慮して次に進める1項目を選びます。`doing`のステップが優先されます。期限の整合性に関する警告を伴う追加・編集では、対話確認か非対話用の`--allow-warning`が必要です。

マイルストーン同士は`daily-review goal milestone edit ... --depends-on mile-xxxxxxxx`、同じマイルストーン内のステップ同士は`daily-review goal milestone step edit ... --depends-on step-xxxxxxxx`で依存を設定できます。自己参照、存在しないID、循環、別目標・別マイルストーンへの依存は保存前に拒否します。フェーズ1のマイルストーンを持たない目標は、書き換えず空のロードマップとして読み込みます。

### 週次重点と毎日のMain候補

`plan`は進行中の目標から候補を出すだけで、日次レビューや既存の確定計画を自動変更しません。週は既存どおり火曜開始・月曜終了です。保存後も`apply`による明示承認が必要です。

```bash
daily-review plan week --date 2026-07-14 --save
daily-review plan review --week 2026-07-14
daily-review plan apply --week 2026-07-14 --yes
daily-review plan today --date 2026-07-14 --save
daily-review plan review --date 2026-07-14
daily-review plan apply --date 2026-07-14 --yes
```

週次重点は最大5件、日次Main候補は最大3件です。候補はカテゴリ優先順位、期限、`doing`、依存関係を考慮します。`data/plans/weekly/`と`data/plans/daily/`に保存され、承認済み計画を編集するときはバックアップを作成します。

日次計画の候補を目標へ明示的に結び、夜の記録からステップ状態の更新候補を確認できます。自動反映は行いません。

```bash
daily-review goal link --date 2026-07-14 --main-index 1 --goal goal-xxxxxxxx --milestone mile-xxxxxxxx --step step-xxxxxxxx
daily-review goal progress --date 2026-07-14
daily-review goal progress --date 2026-07-14 --apply --yes
daily-review goal unlink --date 2026-07-14 --main-index 1
```

### 週次・月次評価

評価は日次実績、承認済み計画、goal link、step状態を読み取り、目標別の状態と根拠、計画精度、診断、修正候補を独立JSONへ保存します。評価を承認しても目標や計画は変更されません。

```bash
daily-review goal evaluate week --date 2026-07-20 --save
daily-review goal evaluate review --week 2026-07-14
daily-review goal evaluate apply --week 2026-07-14 --yes
daily-review goal evaluate month --month 2026-07 --save
daily-review goal evaluate review --month 2026-07
```

週次評価は`data/evaluations/weekly/`、月次評価は`data/evaluations/monthly/`へ保存します。過去の進捗スナップショットがない最初の評価では、進捗差分を無理に推測せず現在値を基準にします。月次差分は保存済み週次評価の値だけから算出します。期限リスクは必要速度/実績速度が`1.0以下=low`、`1.0超〜1.5以下=medium`、`1.5超〜2.0未満=high`、`2.0以上=critical`で、根拠不足は予測不能と表示します。

### 計画修正案と安全な適用

`replan`は評価の診断から修正案を作ります。保存しただけでは適用されず、proposalを承認対象へ選んだ後、確認付きで適用します。全proposalを事前検証し、対象ファイルをバックアップしてから原子的に反映します。

```bash
daily-review goal replan --week 2026-07-14 --save
daily-review goal replan list
daily-review goal replan review replan-xxxxxxxx
daily-review goal replan edit replan-xxxxxxxx --approve proposal-xxxxxxxx
daily-review goal replan apply replan-xxxxxxxx --yes
```

適用履歴には`source: replan`、replan ID、proposal ID、変更フィールドを保存します。承認対象がないreplan、存在しない対象、不正日付、依存関係を壊す変更、バックアップに失敗した変更は反映しません。複数ファイル更新は`data/transactions/`にmanifestを残し、途中失敗時は元のJSONへrollbackします。

### ChatGPTによる評価補助

外部APIへ接続せず、保存済み評価をクリップボードでChatGPTへ渡せます。回答は評価の補助情報として保存するだけで、目標やreplanへ自動適用しません。

```bash
daily-review goal coach --week 2026-07-14 --copy
daily-review goal coach-receive --week 2026-07-14 --clipboard
daily-review goal coach --month 2026-07 --copy
daily-review goal coach-receive --month 2026-07 --clipboard
```

### 毎晩の推奨操作: ChatGPT往復フロー

毎晩は、handoffを作成してChatGPTへ貼り付け、回答をreceiveする流れを使います。外部APIには接続せず、コピー＆ペーストまたはクリップボードだけで往復します。handoffには対象日・一意なセッションID・プロンプトハッシュ・期限が含まれるため、古い回答や別日の回答を保存前に拒否できます。

```bash
daily-review handoff --copy
daily-review receive --clipboard --approve
daily-review chat --resume
daily-review home
```

回答を受信しても、承認するまで日次データは確定されません。保留したドラフトは `daily-review chat --resume` で再開できます。`chat`、`chat-prompt`、`chat-import`、`input`、`organize`、`review`、`edit-draft`、`approve` はトラブル対応や高度な個別操作として引き続き利用できます。

`input` は、ChatGPT上で書いた自然文の原文を日別inboxへ安全に追記保存する入口です。入力原文は日次レビュー、Main、タスク結果、明日の計画へ自動反映しません。

```bash
daily-review input --text "今日は院試を2問進めた"
daily-review input --clipboard
daily-review input --date 2026-07-14
echo "今日は院試を2問進めた" | daily-review input
```

入力は `data/inbox/YYYY-MM-DD.json` に原文のまま追記されます。確認だけしたい場合は `--dry-run` を使います。

### 自然文を整理する

`organize` は、その日のinboxを外部APIやLLMを使わない固定ルールで「今日の候補」「振り返り」「明日の候補」「未分類」に整理し、`data/drafts/YYYY-MM-DD.json` に保存します。これは確認用のドラフトであり、日次JSON、提案版、確定版を変更せず、自動承認もしません。曖昧な文は推測せず `unclassified` に残します。

```bash
daily-review organize
daily-review organize --date 2026-07-14
daily-review organize --date 2026-07-14 --dry-run
daily-review organize --date 2026-07-14 --json
```

同じ入力を再度整理しても書き換えず、新しいinbox入力がある場合だけドラフトへ追記します。全入力から作り直したい場合だけ `--force` を使います。候補を日次データへ反映するには、`approve` または `reflect` でユーザー承認が必要です。

### ドラフトを確認・修正・承認する

整理ドラフトは自動確定されません。`review` で確認し、必要なら `edit-draft` で修正してから、明示的に `approve` してください。承認されると、その日の既存日次JSONへ互換的に保存され、明日の内容は未承認の提案版として保存されます。確定版への自動昇格は行いません。

```bash
daily-review review --date 2026-07-14
daily-review review --date 2026-07-14 --json
daily-review edit-draft --date 2026-07-14 --set tomorrow.main_candidates="院試の過去問を2問解く"
daily-review approve --date 2026-07-14
daily-review approve --date 2026-07-14 --yes
```

`--set` を同じフィールドへ複数回指定すると、その指定順の配列で置換します。空文字で空配列に置換できます。編集できるのは候補・振り返り・日記・未分類だけで、入力原文、作成日時、承認状態は編集できません。承認済みドラフトは通常編集不可です。`edit-draft --force` はドラフトを未承認へ戻しますが、既存の日次データを削除しません。

承認済みドラフトを `approve --force --yes` で再承認する場合は、先に `data/backups/daily/` へ日次JSONのバックアップを作成します。ドラフトはルールベースの候補であり、判断できない文章は未分類のまま残ります。後続フェーズではユーザー確認を前提に、より詳細な取り込みを追加予定です。

### 振り返りを一度に行う

`reflect` は、自然文入力、inboxへの原文保存、ルールベース整理、ドラフト確認、承認までを一つの流れで行う入口です。個別の `input`、`organize`、`review`、`edit-draft`、`approve` も引き続き利用できます。

```bash
daily-review reflect
daily-review reflect --text "今日は院試を2問進めた。明日は研究を進める。"
daily-review reflect --clipboard
daily-review reflect --resume
daily-review reflect --text "今日は院試を2問進めた。明日は研究を進める。" --yes
```

通常は内容を表示してから `y`（承認）、`e`（編集）、`n`（確定せず終了）を選びます。原文は必ずinboxへ残り、`n` や途中の失敗で日次データは作成されません。中断後は `--resume` で未承認ドラフトから再開できます。

`--yes` は確認を省略しますが、未分類、今日または明日のMain候補不足、候補数超過、既存日次データ、承認済みドラフトなどの安全条件に当てはまる場合は承認せずエラーにします。`--dry-run` は入力・整理結果だけを表示して一切保存せず、`--json` は機械処理向けのJSONだけを標準出力へ出力します。

### ChatGPTの構造化出力を取り込む

`chat-import` は外部APIへ接続せず、ChatGPTからコピーしたJSONを検証して確認用ドラフトに取り込みます。原文は `data/inbox/YYYY-MM-DD.json` に `chat_import` としてそのまま保存されます。日次記録や翌日の確定版は、明示的な確認・承認なしには変更されません。

まず、ChatGPTへ渡す形式を表示またはコピーします。

```bash
daily-review chat-prompt
daily-review chat-prompt --clipboard
```

出力を取り込むには、クリップボード、ファイル、標準入力、直接文字列のいずれか一つを使います。

```bash
daily-review chat-import --clipboard
daily-review chat-import --file chatgpt-output.json
echo '{"schema_version":"1.0", "date":"2026-07-14", "raw_text":"...", "today":{"main":[],"completed":[],"partial":[],"not_completed":[]}, "reflection":{"good":[],"problems":[],"causes":[],"change_next":[]}, "tomorrow":{"main":[],"other_tasks":[],"minimum":[]}, "journal":[],"unclassified":[]}' | daily-review chat-import
daily-review chat-import --json-text '{"schema_version":"1.0", "date":"2026-07-14", "raw_text":"...", "today":{"main":[],"completed":[],"partial":[],"not_completed":[]}, "reflection":{"good":[],"problems":[],"causes":[],"change_next":[]}, "tomorrow":{"main":[],"other_tasks":[],"minimum":[]}, "journal":[],"unclassified":[]}' --dry-run
```

入力は schema version `1.0` のJSONオブジェクト1件だけを受け付けます。JSONコードブロック1つを含む説明文も利用できます。未知のフィールドは警告して無視し、必須フィールド不足、型違い、空白だけの項目、重複、Main 3件超過、不自然に遠い未来の日付は保存前に拒否します。

通常の取り込み後は、既存の確認フローで内容を確認・修正できます。

```bash
daily-review reflect --date 2026-07-14 --resume
```

`chat-import --approve` はこの確認フローをすぐ開始します。`--yes` は、警告・未分類・既存日次データがなく、今日と明日のMainが各1〜3件である場合だけ自動承認します。既存の未承認ドラフトを置き換えるときだけ `--force` を使えます。この場合、古いドラフトは `data/backups/drafts/` に保存されます。承認済みドラフトや既存日次データは `--force` を付けても置き換えません。

### `chat` の入力方法

通常の `daily-review chat` は、プロンプトの表示・コピー後に `[c]` クリップボード、`[p]` 貼り付け、`[f]` ファイル、`[q]` 終了を選べます。貼り付けは複数行に対応し、最後の行に `__END__` を入力するか、空行を2回入力すると終了します。

JSONだけを取り込む場合は、既存の検証規則をそのまま使う `--import-only` を使います。

```bash
daily-review chat --import-only --clipboard
daily-review chat --import-only --file chatgpt-output.json
daily-review chat --import-only --json-text '{"schema_version":"1.0", "date":"2026-07-14", "raw_text":"...", "today":{"main":[],"completed":[],"partial":[],"not_completed":[]}, "reflection":{"good":[],"problems":[],"causes":[],"change_next":[]}, "tomorrow":{"main":[],"other_tasks":[],"minimum":[]}, "journal":[],"unclassified":[]}' --dry-run
```

動的プロンプトには対象日、`config/priorities.json` の優先順位、前日の提案・確定計画、今日の未完了タスク、今週の最低ラインを、存在するものだけ含めます。セッションは `data/sessions/YYYY-MM-DD.json` に補助情報として保存します。日次データとドラフトが常に正であり、セッションの破損や欠損だけで日次データを変更することはありません。

### handoff / receive による安全な受け渡し

`handoff` はChatGPTへ貼り付ける完成済みパッケージを発行し、`receive` は回答に含まれるhandoff情報を照合してから既存の安全なインポート処理へ渡します。期限は対象日の翌日05:00（Asia/Tokyo）です。

```bash
daily-review handoff --date 2026-07-14 --copy
daily-review handoff --date 2026-07-14 --output /tmp/daily-review-handoff.txt
daily-review receive --clipboard --dry-run
daily-review receive --file response.json --approve
daily-review receive --clipboard --yes
daily-review handoff-list --date 2026-07-14
daily-review handoff-cancel --date 2026-07-14 --latest
```

`receive` はsession ID、対象日、prompt hash、有効期限、受信履歴、日次データの有無を検証します。期限切れを意図的に確認するときだけ `--allow-expired` を使えます。未承認ドラフトの置換だけは `--force` で可能ですが、確定済み日次・別日・異なるsession ID・異なるprompt hashは常に拒否されます。

## v1.3 開発中: 実運用UX、CSV、通知基盤

### タスク一覧

日次の提案・確定指示書とv1.2の目標日次計画を、元データを書き換えず共通形式で一覧にします。通常は完了済みを除外し、期限超過、今日期限、Main、高優先度の順に表示します。`--detail`で参照元や作成・更新日時も確認できます。

```bash
daily-review tasks list
daily-review tasks list --due overdue
daily-review tasks list --main
daily-review tasks list --minimum
daily-review tasks list --status completed
daily-review tasks list --format json
```

`--status`、`--priority`、`--category`、`--due`、`--main`、`--minimum`はAND条件です。完了済みを含む全件は`--all`で表示します。

### クイックレビュー

対話形式、個別オプション、JSON標準入力のいずれかで、夜の入力を短く完了できます。保存前の確認だけなら`--dry-run`を使います。

```bash
daily-review review quick
daily-review review quick --date 2026-07-15
daily-review review quick --done "自己管理システムの開発を進めた" --tomorrow "院試過去問を進める" --minimum "1問解く"
daily-review review quick --dry-run
echo '{"date":"2026-07-15","done":["開発"],"tomorrow":["院試"],"minimum":["1問"]}' | daily-review review quick --stdin
```

原入力は先に`data/inbox/YYYY-MM-DD.json`へ保存し、整形済みレビューと翌日の未承認指示書案を`data/daily/YYYY-MM-DD.json`、Markdownを`logs/YYYY-MM-DD.md`へ保存します。整形済み保存が失敗しても、保存済み原入力は残ります。同日のレビューは通常拒否し、意図的に更新する場合だけ`--force`を指定します。更新前の日次JSONは`data/backups/daily/`へ退避されます。

明日やることが4件以上なら入力順の先頭3件をMain候補とし、残りは`backlog_candidates`へ保持します。確定版への自動承認は行いません。

### CSVエクスポート

レビュー、タスク、指示書を固定列・安定した行順でCSVに出力します。配列は可逆なJSON文字列です。標準はUTF-8、`--excel`はUTF-8 BOM付きです。

```bash
daily-review export csv --type tasks
daily-review export csv --type reviews --from 2026-07-01 --to 2026-07-31
daily-review export csv --type reviews --period week --date 2026-07-15
daily-review export csv --type all --output exports/
daily-review export csv --type tasks --excel --output exports/tasks.csv
```

デフォルト出力は単一種別が`exports/<type>.csv`、`all`が`exports/csv/`です。既存ファイルは上書きせず、更新する場合だけ`--force`を使います。ユーザー入力が`=`、`+`、`-`、`@`で始まるセルには先頭へアポストロフィを付け、表計算ソフトの数式実行を防ぎます。週指定は既存仕様どおり火曜開始・月曜終了です。

### 通知チェック

通知は自動スケジューラーではなく、安全に呼び出せる判定・送信基盤です。振り返り未実施、指示書案・未承認・確定、期限超過、Main未完了、最低限未完了を判定し、ConsoleとJSONファイルへ送信できます。

```bash
daily-review notifications check
daily-review notifications check --dry-run
daily-review notifications check --date 2026-07-15 --time 21:30 --dry-run
daily-review notifications history
```

設定例は`config/notifications.example.json`です。個人設定は同じ形式で`config/notifications.json`へ置きます。設定がない旧環境では安全な組み込みデフォルトを使用し、未知の設定項目は無視します。送信履歴は`data/notifications/history.json`、File送信結果は`data/notifications/events/`に原子的に保存します。同じ通知種別・対象日・対象データ・送信先は、安定したキーにより既定24時間重複送信しません。通知失敗は履歴へ残しますが、日次データは変更しません。

現フェーズでは外部通知サービス、自動スケジューラー、ChatGPT APIによる自動解析は実装していません。

## v1.3 開発中: ChatGPT Command API

ChatGPTや外部プログラムは、version `1`のJSON CommandRequestを使って、日次レビュー、タスク、指示書を同じ入口から操作できます。HTTPや外部LLM APIは使わず、CLIからローカルのアプリケーション層を呼び出します。

preview用の`request.json`例です。

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

```bash
daily-review api execute --input request.json --pretty
cat request.json | daily-review api execute --stdin
daily-review api schema --type request
daily-review api schema --type response
daily-review api history
```

preview responseの`confirmation_token`を確認後、同じrequest内容をcommitします。CLIオプションはJSON内のmodeとtokenを上書きします。

```bash
daily-review api execute --input request.json --mode commit --confirmation-token confirm_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx --pretty
```

自然文を解析するだけ、またはCommand APIへpreviewすることもできます。

```bash
cat review.txt | daily-review parse review --stdin --date 2026-07-15
cat review.txt | daily-review parse review --stdin --date 2026-07-15 --preview --idempotency-key review-2026-07-15
```

- previewは日次・タスク・指示書を変更しません。commitに必要な確認・監査・idempotency補助記録だけを`data/api/`へ保存します。
- commitは既定30分以内のtokenを要求し、request内容と対象データがpreview時から変わっていないか再検証します。
- 同じidempotency keyと同じ内容の再送は二重保存せず、異なる内容は拒否します。
- 曖昧なタスク名は候補一覧を返し、勝手に変更しません。
- raw inputとunclassifiedを保持し、Mainは最大3件、4件目以降はoptionalまたはbacklogへ残します。
- 指示書案は`approve_instruction`commandなしに確定されません。

全command、構造化エラー、一括実行、保存仕様は[ChatGPT Command API仕様](docs/chatgpt-command-api.md)を参照してください。

## 設計思想

- 生ログは加工せず保存します。
- 提案版と確定版を分け、`approve-plan` だけが確定版を作成します。
- Mainは最大3つ、各タスクには最低ラインが必要です。
- carryoverと改善提案は表示のみで、翌日の計画へ自動追加しません。
- 外部API、Notion、Google Calendar、LINE、LLM呼び出しは使いません。

## インストールと初期設定

Python 3.11以上が必要です。

```bash
cd daily-review
python3 -m pip install -e ".[test]"
daily-review --version
daily-review init
daily-review migrate
daily-review v11-check
```

保存先は自動検出されます。`daily-review/` 内でも、その親のリポジトリルートからでも実行できます。別の保存先を使うときは、各コマンドに `--root /path/to/root` を付けるか、`DAILY_REVIEW_ROOT` を設定します。明示した `--root` が常に優先されます。`init` と `migrate` は既存のデータとテンプレートを上書きしません。

## 毎日の運用

毎日の入口は `home` です。状況、Main、未完了タスク、明日の計画、次の操作をまとめて表示します。

```bash
daily-review home
daily-review home --date 2026-07-14
```

最短運用は次のとおりです。

```bash
daily-review home
daily-review close-day --date YYYY-MM-DD --clipboard --dry-run
daily-review close-day --date YYYY-MM-DD --clipboard
daily-review show-proposal --date YYYY-MM-DD
daily-review approve-plan --date YYYY-MM-DD
```

朝は確定済み指示書を確認します。

```bash
daily-review today
```

夜は、結果を含むJSONをChatGPTで作成し、保存前確認をしてから保存します。提案版を見て、自分で承認してください。

```bash
daily-review today --show-ids
daily-review close-day --clipboard --dry-run
daily-review close-day --clipboard
daily-review show-proposal
daily-review approve-plan
```

個別保存が必要な場合は、`save-raw`、`save-review`、`save-proposal`、`save-night`、`record-results` も利用できます。

| コマンド | 役割 |
| --- | --- |
| `home` | 毎日最初に見る統合画面 |
| `summary` | 計画・記録・次の操作の短い一覧 |
| `start` | 今から夜の運用を始めるための案内 |
| `next` | 次に実行する1コマンドを案内 |
| `status` | 指定日の保存ファイル状態を確認 |
| `doctor` | 保存構造とデータを読み取り専用で点検 |
| `input` | 自然文の原文をinboxへ追記保存 |
| `organize` | inboxをルールベースの確認用ドラフトへ整理 |
| `review` | 整理ドラフトを確認表示 |
| `edit-draft` | 許可済みのドラフト配列を置換編集 |
| `approve` | 確認済みドラフトを日次記録と翌日提案へ保存 |
| `reflect` | 入力から整理・確認・承認までをまとめて進める入口 |
| `chat` | ChatGPT用プロンプト、JSON取り込み、確認・承認をまとめる推奨入口 |
| `handoff` | 対象日・session ID・期限付きのChatGPT受け渡しパッケージを発行 |
| `receive` | handoff照合後にChatGPT回答を安全に取り込む |
| `handoff-list` | handoffの発行・受信・取消状態を確認 |
| `handoff-cancel` | 未承認handoffを取消して以後の受信を拒否 |
| `chat-prompt` | ChatGPTへ渡す構造化JSON用プロンプトを表示・コピー |
| `chat-import` | ChatGPTの構造化JSONを検証して確認用ドラフトへ取り込む |

`start`、`summary`、`home` は保存状態を読むだけで変更しません。指定日の確認には `daily-review start --date YYYY-MM-DD` または `daily-review summary --date YYYY-MM-DD` を使えます。

## 夜の振り返りと翌日の承認

`close-day` は当日の結果、振り返り、翌日提案を一括保存します。保存前に必ず `--dry-run` で確認できます。提案版は未承認のままで、`approve-plan` を実行したときだけ確定版になります。

```bash
daily-review close-day --date 2026-07-14 --clipboard --dry-run
daily-review close-day --date 2026-07-14 --clipboard
daily-review show-proposal --date 2026-07-14
daily-review approve-plan --date 2026-07-14
```

## 結果記録

翌日の確定版をID付きで確認し、JSONファイルまたは標準入力から結果を記録します。`carryover` は候補を表示するだけで、翌日の計画を変更しません。

```bash
daily-review today --date 2026-07-15 --show-ids
daily-review record-results --date 2026-07-15 --file task_results.json
daily-review carryover --date 2026-07-15
```

## 週次・月次振り返り

週次は火曜日始まり・月曜日終わりです。`--date` を指定すると、その日を含む週または暦月を集計します。

```bash
daily-review weekly
daily-review weekly --date 2026-07-13
daily-review monthly
daily-review monthly --date 2026-07-14
```

集計結果はJSONとMarkdownの両方に保存されます。結果未記録は未着手として扱わず分離し、最低ラインを確実に判定できない場合は「算出不可」と表示します。

## バックアップ

`backup create`は`data/`、`logs/`、`templates/`、秘密情報を除いた`config/`をZIPへ保存します。`.env`、token・secret・credentialを示す設定、`data/backups/`、一時ファイル、symlink、バックアップ自身は含めません。従来の`daily-review backup`も同じフルバックアップとして利用できます。

```bash
daily-review backup
daily-review backup create
daily-review backup create --dry-run
daily-review backup create --output /path/to/backups --idempotency-key backup-2026-07-16
daily-review backup list --format json
daily-review backup inspect /path/to/backup.zip
daily-review backup verify /path/to/backup.zip
daily-review backup delete --retention --dry-run
daily-review backup delete --retention --apply --idempotency-key retention-2026-07-16
```

既定の保存先は`backups/daily-review-backup-YYYYMMDDTHHMMSS+0900-ID.zip`です。同名ファイルは上書きしません。manifestにはbackup ID、app/data version、作成日時、件数、各ファイルのsizeとSHA-256が入ります。検証では重複パス、Zip Slip、symlink・特殊ファイル、異常な件数・展開サイズ・圧縮率も拒否します。`config/recovery.json`で自動バックアップの保持数・保持日数を設定でき、手動バックアップは自動削除しません。

## 復元

既存の追加専用restoreは引き続き利用できます。v1.3では、通常は`restore preview`で追加・更新・競合・削除候補を確認し、発行されたtokenでapplyしてください。

```bash
daily-review restore path/to/backup.zip --dry-run
daily-review restore preview path/to/backup.zip --mode merge --format json
daily-review restore apply path/to/backup.zip --mode merge \
  --confirmation-token restore_confirm_xxx \
  --idempotency-key restore-2026-07-16
daily-review restore status --format json
```

`merge`は現在存在しないファイルだけを追加し、内容が違う既存ファイルを競合として停止します。`missing-only`は既存ファイルをすべてスキップします。`replace`だけが更新・削除候補を適用しますが、秘密情報や復元・繰越・修復履歴は削除対象にしません。

applyはZIPを再検証し、backup hash、mode、差分hash、現在状態hash、期限をtokenと照合します。staleなpreviewや未解決競合は拒否します。適用前には必ず現在状態を自動バックアップし、復元ファイルと履歴・idempotency記録をrollback可能な一括置換で保存します。

詳細は[`docs/backup-and-restore.md`](docs/backup-and-restore.md)を参照してください。

## 未完了タスクの繰越

繰越はタスクをコピーせず、既存タスクへ`planned_date`、`original_due_date`、`rollover_count`などを追記します。completed、cancelled、archived、deleted、未解除blocked、someday、`rollover_policy: never`、対象日の指示書へ登録済みのタスクは除外します。

```bash
daily-review rollover preview --date 2026-07-16 --format json
daily-review rollover apply --date 2026-07-16 \
  --confirmation-token rollover_confirm_xxx \
  --idempotency-key rollover-2026-07-16
daily-review rollover history --format json
```

3回目から見直し警告、5回目から分解提案、7回目から自動Main候補外とします。閾値は`config/recovery.json`で変更できます。Mainは優先順位設定を使って最大3件とし、残りはoptional候補です。最低限を縮小した場合は自動提案として表示し、元の最低限を上書きしません。詳細は[`docs/task-rollover.md`](docs/task-rollover.md)を参照してください。

## doctor

`doctor` はデータを変更せず、ディレクトリ、テンプレート、日次・週次・月次JSON、inbox・draft JSON、Markdown対応、Main数、最低ライン、task_resultsのstatusを点検します。

```bash
daily-review doctor
```

`doctor` は採用した保存先ルート、書き込み可能性、火曜始まりの週、パッケージバージョンも表示します。`WARN` は不足したMarkdownなど、`ERROR` は読めないJSONや不正な計画を示します。古いJSONに新規フィールドがないことだけではエラーにしません。最後は問題なしで `daily-review doctor: OK`、警告ありで `daily-review doctor: WARNING`、重大な問題で `daily-review doctor: ERROR` を表示します。

構造化された全データ検査と安全修復にはサブコマンドを使います。

```bash
daily-review doctor check
daily-review doctor check --format json
daily-review doctor repair --dry-run
daily-review doctor repair --idempotency-key repair-2026-07-16
daily-review doctor report --format json
```

issueには安定したcodeとinfo/warning/error/criticalのseverityが付きます。repair対象は、欠損updated_atの既存時刻による補完、Main超過分のoptional退避、負のrollover count、元期限保持、通知errorの「未記録」補完だけです。raw logの推測、競合解決、完了状態変更、タスク削除は行いません。実行前に自動バックアップし、履歴を`data/repairs/history.json`へ保存します。詳細は[`docs/data-integrity.md`](docs/data-integrity.md)を参照してください。

## release-check

`release-check` はv1.3.0のバージョン、package metadata、主要コマンド、安全機能、ドキュメント、Git除外、migration定義を読み取り専用で確認します。`v12-check`は指定ルートの目標・計画・評価・replan・transactionを読み取り専用で点検します。

```bash
daily-review release-check
daily-review v11-check
daily-review v12-check --verbose
daily-review v12-check --json
```

## 保存構造とJSON

```text
data/daily/YYYY-MM-DD.json
data/weekly/YYYY-MM-DD_YYYY-MM-DD.json
data/monthly/YYYY-MM.json
data/inbox/YYYY-MM-DD.json
data/drafts/YYYY-MM-DD.json
data/sessions/YYYY-MM-DD.json
data/handoffs/YYYY-MM-DD.json
data/backups/daily/YYYY-MM-DD_TIMESTAMP.json
data/backups/drafts/YYYY-MM-DD_TIMESTAMP.json
data/goals/items/goal-xxxxxxxx.json
data/plans/weekly/YYYY-MM-DD_YYYY-MM-DD.json
data/plans/daily/YYYY-MM-DD.json
data/evaluations/weekly/YYYY-MM-DD_YYYY-MM-DD.json
data/evaluations/monthly/YYYY-MM.json
data/replans/replan-xxxxxxxx.json
data/goal-designs/design-xxxxxxxx.json
data/transactions/transaction-xxxxxxxxxxxx.json
data/notifications/history.json
data/notifications/events/notification-xxxxxxxxxxxx.json
data/scheduler/history.json
data/scheduler/audit/flow-xxxxxxxx.json
data/scheduler/idempotency/HASH.json
data/scheduler/locks/HASH.lock/
data/api/tasks.json
data/api/audit/audit-xxxxxxxx.json
data/api/confirmations/confirm_xxxxxxxx.json
data/api/idempotency/HASH.json
data/backup/idempotency/HASH.json
data/restore/history.json
data/restore/idempotency/HASH.json
data/rollover/history.json
data/rollover/idempotency/HASH.json
data/repairs/history.json
data/repairs/idempotency/HASH.json
logs/YYYY-MM-DD.md
logs/weekly_YYYY-MM-DD_YYYY-MM-DD.md
logs/monthly_YYYY-MM.md
templates/
config/priorities.json
config/notifications.json
config/api.json
config/recovery.json
config/scheduler.json
exports/
backups/
```

日次JSONには生ログ、整形済み振り返り、提案版、確定版、タスク結果を必要に応じて保存します。新しい任意フィールドがなくても既存JSONは読み込めます。未知の追加フィールドを一括削除・変換することはありません。

- `inbox`: 原文
- `drafts`: 整理・編集途中
- `daily`: 承認済み記録
- `handoffs`: ChatGPTとの受け渡し情報
- `sessions`: 進行状態
- `backups`: 上書き前の退避
- `notifications`: 通知イベントと送信履歴
- `scheduler`: job実行履歴、flow監査、冪等性、短時間の実行lock
- `api`: APIタスク、confirmation、idempotency、監査履歴
- `restore` / `rollover` / `repairs`: preview-first操作の履歴と冪等性記録
- `exports`: CSVのデフォルト出力先（Git管理外）

`config/notifications.example.json`、`config/api.example.json`、`config/recovery.example.json`、`config/scheduler.example.json`は配布用サンプルです。個人用の同名設定（`.example`なし）はGit管理しません。

ドラフト承認で保存する当日の候補・分類結果は、既存の `structured_review` と `tomorrow_plan_proposal` に反映します。確定版タスクに紐付かない当日結果、問題、未分類などは、後方互換な任意フィールド `draft_approval` に保存します。

## 個人データの扱い

`data/`、`logs/`、`backups/`、ZIPアーカイブ、実行時の`config/priorities.json`はGitの追跡対象外です。テンプレート、`config/priorities.example.json`、テスト、README、CHANGELOGは追跡対象です。バックアップには個人データが含まれるため、安全な場所へ保管してください。

## 安全設計

日次JSONの更新は一時ファイルを経由します。バックアップは読み取り専用、復元は検証後に実行し、既定では競合を停止します。`proposal` の自動承認、carryoverの自動追加、改善提案の自動反映は行いません。

## トラブルシューティング

- 自然文を整理する: `daily-review organize --date YYYY-MM-DD --dry-run`
- 振り返りを一度に進める: `daily-review reflect --date YYYY-MM-DD`
- 中断したドラフトを再開する: `daily-review reflect --date YYYY-MM-DD --resume`
- ChatGPTへ渡す安全なパッケージを作る: `daily-review handoff --copy`
- ChatGPT回答を確認付きで受信する: `daily-review receive --clipboard --approve`
- 整理ドラフトを確認する: `daily-review review --date YYYY-MM-DD`
- ドラフトを承認する: `daily-review approve --date YYYY-MM-DD --yes`
- 保存状態を確認する: `daily-review status --date YYYY-MM-DD`
- 日次の短い一覧を見る: `daily-review summary --date YYYY-MM-DD`
- 毎日の統合画面を見る: `daily-review home`
- データ全体を点検する: `daily-review doctor`
- 次の操作を確認する: `daily-review next`
- 日次運用を開始する: `daily-review start`
- 復元対象を確認する: `daily-review restore path/to/backup.zip --dry-run`
- CLIの一覧を確認する: `daily-review --help`

## v1.3開発中：自動実行

毎日の入口は引き続き`daily-review home`です。スケジューラーを手動確認する場合は次を使います。

```bash
daily-review scheduler status
daily-review scheduler due
daily-review scheduler run-due --dry-run
daily-review scheduler history
daily-review scheduler history --status failed
daily-review scheduler doctor
```

朝・夜・週次・月次のまとまった確認は、次の安全な運用フローで実行できます。

```bash
daily-review flow morning
daily-review flow nightly
daily-review flow weekly
daily-review flow monthly
```

macOSではlaunchdを推奨します。launchdは15分ごとに`run-due`を呼ぶだけで、時刻、grace period、missed run、retry、同一schedule slotの重複防止はdaily-review内部で判定します。PCスリープ後もgrace period内なら1回だけ回復実行し、retryはプロセスをsleepさせず次回pollで行います。quiet hours中の通知は許可時刻まで保留します。

自動起動は明示的にinstallした場合だけ有効です。最初に必ずpreviewしてください。

```bash
daily-review scheduler install --dry-run
daily-review scheduler install
daily-review scheduler status
```

`daily-review scheduler uninstall`はlaunchd登録だけを外し、日次データ、job履歴、監査履歴を削除しません。launchdを使えない環境では`daily-review scheduler cron-example`で例を表示できます。設定と安全境界の詳細は[スケジューラー運用](docs/scheduler.md)、[launchd](docs/launchd.md)、[運用フロー](docs/operational-flows.md)、[トラブルシューティング](docs/troubleshooting-scheduler.md)を参照してください。

## v1で実装しないもの

外部サービス連携、LLM呼び出し、自動承認、自動計画変更は対象外です。v1.3開発版の自動通知はローカルのConsole/File senderだけを使います。

## 今後の候補

将来の候補として、外部通知sender、運用テンプレートの追加、より詳細なローカル分析があります。

## 開発・テスト

```bash
cd daily-review
pytest
daily-review --help
daily-review --version
```

## バージョン

```bash
daily-review --version
```

現在の正式リリースは `1.3.0` です。詳細は`CHANGELOG.md`と`docs/releases/v1.3.0.md`を参照してください。
