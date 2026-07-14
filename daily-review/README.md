# daily-review

`daily-review` は、毎日の振り返り、翌日の指示書、実行結果をローカルのJSONとMarkdownに残すPython CLIです。v1.0.0では、週次・月次振り返りと、ローカルバックアップ・安全な復元・状態点検までを扱います。

## v1でできること

- 生ログ、日記、整形済み振り返りのローカル保存
- 明日の指示書の提案、手動承認、結果記録
- 日次の入口 `home`、状況一覧 `summary`、夜の案内 `start`
- 火曜始まり・月曜終わりの週次、暦月の月次振り返り
- 読み取り専用の `doctor` と `release-check`
- SHA-256 manifest付きのバックアップと安全な復元

## v1.1 開発中: 自然文入力

### 毎晩の推奨操作: ChatGPT往復フロー

毎晩は `daily-review chat` を入口として使います。外部APIには接続せず、表示されたプロンプトとChatGPTのJSONをコピー＆ペーストまたはクリップボードで往復します。原文、確認用ドラフト、確定データは分離して保存され、承認するまで日次データは確定されません。中断してもドラフトから再開できます。

```bash
daily-review chat
daily-review chat --resume
daily-review chat --import-only --clipboard
daily-review chat --prompt-only --copy-prompt
```

`chat` は既存の `chat-prompt`、`chat-import`、`reflect --resume` をまとめる上位コマンドです。`input`、`organize`、`review`、`edit-draft`、`approve` はトラブル対応や高度な個別操作として引き続き利用できます。

`input` は、ChatGPT上で書いた自然文の原文を日別inboxへ安全に追記保存するv1.1開発中の入口です。入力原文は日次レビュー、Main、タスク結果、明日の計画へ自動反映しません。

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
```

保存先は自動検出されます。`daily-review/` 内でも、その親のリポジトリルートからでも実行できます。別の保存先を使うときは、各コマンドに `--root /path/to/root` を付けます。明示した `--root` が常に優先されます。`init` は既存のデータとテンプレートを上書きしません。

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

`backup` は `data/`、`logs/`、`templates/` をZIPへ保存します。ソースコードや仮想環境は含めません。アーカイブにはファイル一覧、SHA-256、作成日時、形式バージョンを含む `manifest.json` が入ります。

```bash
daily-review backup
daily-review backup --output /path/to/backups
daily-review backup --output /path/to/daily-review.zip
```

既定の保存先は `backups/daily-review-backup-YYYYMMDD-HHMMSS.zip` です。同名ファイルは上書きしません。元データは変更しません。

## 復元

まず内容だけを確認してください。

```bash
daily-review restore path/to/backup.zip --dry-run
```

通常の復元は、対象ファイルが1件でも既存なら停止します。これにより既存データを無断で上書きしません。

```bash
daily-review restore path/to/backup.zip
```

意図的に上書きする必要がある場合だけ `--force` を指定できます。この場合は復元前に `backups/pre-restore-YYYYMMDD-HHMMSS.zip` を自動作成します。

```bash
daily-review restore path/to/backup.zip --force
```

復元前にZIP、manifest、形式バージョン、パス、SHA-256を検証します。不正なZIP、絶対パス、パストラバーサル、対象外パス、ハッシュ不一致は書き込み前に拒否します。

## doctor

`doctor` はデータを変更せず、ディレクトリ、テンプレート、日次・週次・月次JSON、inbox・draft JSON、Markdown対応、Main数、最低ライン、task_resultsのstatusを点検します。

```bash
daily-review doctor
```

`doctor` は採用した保存先ルート、書き込み可能性、火曜始まりの週、パッケージバージョンも表示します。`WARN` は不足したMarkdownなど、`ERROR` は読めないJSONや不正な計画を示します。古いJSONに新規フィールドがないことだけではエラーにしません。正常時は最後に `daily-review doctor: OK` を表示します。

## release-check

`release-check` はv1.0.0のバージョン、package metadata、主要コマンド、保存先、doctorの重大エラーを読み取り専用で確認します。

```bash
daily-review release-check
```

## 保存構造とJSON

```text
data/daily/YYYY-MM-DD.json
data/weekly/YYYY-MM-DD_YYYY-MM-DD.json
data/monthly/YYYY-MM.json
data/inbox/YYYY-MM-DD.json
data/drafts/YYYY-MM-DD.json
data/sessions/YYYY-MM-DD.json
data/backups/daily/YYYY-MM-DD_TIMESTAMP.json
logs/YYYY-MM-DD.md
logs/weekly_YYYY-MM-DD_YYYY-MM-DD.md
logs/monthly_YYYY-MM.md
templates/
config/priorities.json
backups/
```

日次JSONには生ログ、整形済み振り返り、提案版、確定版、タスク結果を必要に応じて保存します。新しい任意フィールドがなくても既存JSONは読み込めます。未知の追加フィールドを一括削除・変換することはありません。

ドラフト承認で保存する当日の候補・分類結果は、既存の `structured_review` と `tomorrow_plan_proposal` に反映します。確定版タスクに紐付かない当日結果、問題、未分類などは、後方互換な任意フィールド `draft_approval` に保存します。

## 個人データの扱い

`data/`、`logs/`、`backups/`、ZIPアーカイブはGitの追跡対象外です。テンプレート、テスト、README、CHANGELOGは追跡対象です。バックアップには個人データが含まれるため、安全な場所へ保管してください。

## 安全設計

日次JSONの更新は一時ファイルを経由します。バックアップは読み取り専用、復元は検証後に実行し、既定では競合を停止します。`proposal` の自動承認、carryoverの自動追加、改善提案の自動反映は行いません。

## トラブルシューティング

- 自然文を整理する: `daily-review organize --date YYYY-MM-DD --dry-run`
- 振り返りを一度に進める: `daily-review reflect --date YYYY-MM-DD`
- 中断したドラフトを再開する: `daily-review reflect --date YYYY-MM-DD --resume`
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

## v1で実装しないもの

外部サービス連携、通知、LLM呼び出し、自動承認、自動計画変更はv1の対象外です。

## 今後の候補

将来の候補として、データのエクスポート、運用テンプレートの追加、より詳細なローカル分析があります。いずれもv1では自動実行しません。

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

現在のv1リリースは `1.0.0` です。
