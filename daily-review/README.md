# daily-review

`daily-review` は、毎日の振り返り、翌日の指示書、実行結果をローカルのJSONとMarkdownに残すPython CLIです。v1.0.0では、週次・月次振り返りと、ローカルバックアップ・安全な復元・状態点検までを扱います。

## v1でできること

- 生ログ、日記、整形済み振り返りのローカル保存
- 明日の指示書の提案、手動承認、結果記録
- 日次の入口 `home`、状況一覧 `summary`、夜の案内 `start`
- 火曜始まり・月曜終わりの週次、暦月の月次振り返り
- 読み取り専用の `doctor` と `release-check`
- SHA-256 manifest付きのバックアップと安全な復元

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

`doctor` はデータを変更せず、ディレクトリ、テンプレート、日次・週次・月次JSON、Markdown対応、Main数、最低ライン、task_resultsのstatusを点検します。

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
logs/YYYY-MM-DD.md
logs/weekly_YYYY-MM-DD_YYYY-MM-DD.md
logs/monthly_YYYY-MM.md
templates/
backups/
```

日次JSONには生ログ、整形済み振り返り、提案版、確定版、タスク結果を必要に応じて保存します。新しい任意フィールドがなくても既存JSONは読み込めます。未知の追加フィールドを一括削除・変換することはありません。

## 個人データの扱い

`data/`、`logs/`、`backups/`、ZIPアーカイブはGitの追跡対象外です。テンプレート、テスト、README、CHANGELOGは追跡対象です。バックアップには個人データが含まれるため、安全な場所へ保管してください。

## 安全設計

日次JSONの更新は一時ファイルを経由します。バックアップは読み取り専用、復元は検証後に実行し、既定では競合を停止します。`proposal` の自動承認、carryoverの自動追加、改善提案の自動反映は行いません。

## トラブルシューティング

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
