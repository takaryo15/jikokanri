# daily-review

`daily-review` は、ChatGPTで作った毎日の振り返りと明日の指示書を、ローカルのJSONとMarkdownへ保存するPython CLIです。

外部API、Notion、LINE、Googleカレンダー連携は使いません。ChatGPTで生成した文章やJSONをCLIへ貼り付けて、提案版と確定版を分けて管理します。

## インストール

Python 3.11以上を使います。

```bash
cd daily-review
python3 -m pip install -e ".[test]"
```

インストールせずに試す場合:

```bash
cd daily-review
PYTHONPATH=src python3 -m daily_review.cli --help
```

## 初期化

```bash
daily-review init
```

作成される保存先:

```text
data/daily/
data/weekly/
logs/
templates/
```

すでに存在するディレクトリやテンプレートは削除・上書きしません。

保存先を変えたい場合は各コマンドで `--root /path/to/root` を指定できます。

## 基本の流れ

朝:

```bash
daily-review today
```

今日の確定済みタスクを見ます。夜にChatGPTへタスク結果を渡す場合は、ID付きで表示します。

```bash
daily-review today --date 2026-07-14 --show-ids
```

夜:

1. ChatGPTへ夜の振り返りを送る
2. ChatGPTが1つの `night.json` を出力する
3. `close-day --dry-run` で保存前確認をする
4. `close-day` で当日の結果、振り返り、翌日提案を一括保存する
5. 提案版を確認する
6. OKなら `approve-plan` で承認する
7. 翌朝に `today` で確認する

```bash
daily-review today --date 2026-07-14 --show-ids
daily-review close-day --date 2026-07-14 --file night.json --dry-run
daily-review close-day --date 2026-07-14 --file night.json
daily-review show-proposal --date 2026-07-14
daily-review approve-plan --date 2026-07-14
daily-review today
```

未完了確認:

```bash
daily-review carryover --date 2026-07-14
```

`carryover` は引き継ぎ候補を表示するだけです。翌日の提案版や確定版へは自動追加しません。

## 毎晩の保存

日付を明示したい場合:

```bash
daily-review close-day --date 2026-07-14 --file night.json
```

`close-day` は `--file` を省略すると標準入力から貼り付けできます。入力後は `Ctrl-D` で保存します。

```bash
cat night.json | daily-review close-day --date 2026-07-14
```

保存後は、日次JSONとMarkdownの保存先、次に実行するコマンドが表示されます。保存内容をざっと確認したいときは次を使います。

```bash
daily-review status --date 2026-07-13
daily-review list --limit 7
```

日次Markdownは `logs/YYYY-MM-DD.md` に作成されます。生ログ、日記、今日のMain、最低ライン、崩れた原因、提案版、確定版を1ファイルで見返せます。

`close-day` は当日のタスク結果、夜の振り返り、翌日の提案版をまとめて保存します。翌日の提案版は未承認のままで、確定版は `approve-plan` を実行したときだけ作られます。

`save-night` は詳細・トラブル対応用として残しています。`save-night` は生ログ、日記、整形ログ、翌日提案のみを保存し、タスク結果は保存しません。

## 個別に保存する場合

`save-night` を使わず、各ファイルを分けて保存することもできます。

```bash
daily-review save-raw --date 2026-07-13 --file raw.txt
daily-review save-review --date 2026-07-13 --file review.json
daily-review save-proposal --date 2026-07-13 --file proposal.json
daily-review status --date 2026-07-13
daily-review validate --date 2026-07-13
daily-review approve-plan --date 2026-07-13
```

## 朝の使い方

```bash
daily-review today
```

朝は表示された確定版を見るだけです。朝に新しく計画を作り直す前提にはしていません。

任意の日付を見る場合:

```bash
daily-review today --date 2026-07-14
```

`today` は保存元の日付ではなく、確定版指示書の `target_date` で探します。

## 月曜日夜の週次振り返り

```bash
daily-review weekly
```

`--date` で指定した日を含む、火曜日始まり・月曜日終わりの週を集計します。

例:

```bash
daily-review weekly --date 2026-07-13
```

対象期間は `2026-07-07` から `2026-07-13` です。

## JSON入力例

`close-day` 用 `night.json`:

```json
{
  "date": "2026-07-14",
  "raw_log": "今日は院試の過去問を大問1つ解いた。研究はRGS1だけ確認した。",
  "diary": "少し疲れていたが、院試を進められたのはよかった。",
  "task_results": [
    {
      "task_id": "task-1",
      "status": "completed",
      "note": "大問1を最後まで解いた",
      "minimum_line_achieved": true
    }
  ],
  "structured_review": {
    "today_main": [
      {
        "area": "院試",
        "status": "完了",
        "note": "過去問の大問1を解いた"
      }
    ],
    "minimum_line": {
      "院試": "達成"
    },
    "what_went_well": ["学校に行けた"],
    "breakdown_causes": ["スマホ"],
    "one_change_tomorrow": "朝イチで過去問を開く"
  },
  "tomorrow_plan_proposal": {
    "target_date": "2026-07-15",
    "main": ["院試", "研究", "筋トレ・健康"],
    "tasks": [
      {
        "area": "院試",
        "task": "過去問の次の大問を1つ解く",
        "priority": 1,
        "minimum_line": "問題文を開く"
      }
    ],
    "one_change_tomorrow": "帰宅前に過去問を開く"
  }
}
```

`record-results` 用 `task_results.json`:

```json
{
  "task_results": [
    {
      "task_id": "task-1",
      "status": "completed",
      "note": "大問1を最後まで解いた",
      "minimum_line_achieved": true
    },
    {
      "task_id": "task-2",
      "status": "partial",
      "note": "RGS1だけ確認した",
      "minimum_line_achieved": true
    }
  ]
}
```

`review.json`:

```json
{
  "diary": "任意の日記",
  "structured_review": {
    "today_main": [
      {
        "area": "院試",
        "status": "一部進んだ",
        "note": "過去問を少し見た"
      }
    ],
    "minimum_line": {
      "院試": "達成",
      "研究": "未達"
    },
    "what_went_well": ["学校に行けた"],
    "breakdown_causes": ["眠気", "スマホ"],
    "one_change_tomorrow": "朝イチで過去問を開く"
  }
}
```

`proposal.json`:

```json
{
  "target_date": "2026-07-14",
  "main": ["院試", "研究", "健康"],
  "tasks": [
    {
      "area": "院試",
      "task": "過去問 大問1つ",
      "priority": 1,
      "minimum_line": "問題文だけ読む"
    }
  ],
  "one_change_tomorrow": "朝イチで過去問を開く"
}
```

`save-proposal` 実行時に `status` は必ず `pending_review` として保存されます。

## 全コマンド

```text
daily-review init
daily-review close-day --date YYYY-MM-DD [--file night.json] [--dry-run]
daily-review save-night --date YYYY-MM-DD [--file night.json]
daily-review save-raw --date YYYY-MM-DD [--file raw.txt]
daily-review save-review --date YYYY-MM-DD [--file review.json]
daily-review save-proposal --date YYYY-MM-DD [--file proposal.json]
daily-review approve-plan --date YYYY-MM-DD [--force]
daily-review today [--date YYYY-MM-DD] [--show-ids]
daily-review show-proposal --date YYYY-MM-DD
daily-review record-results --date YYYY-MM-DD [--file task_results.json]
daily-review results --date YYYY-MM-DD
daily-review carryover --date YYYY-MM-DD [--include-skipped]
daily-review status --date YYYY-MM-DD
daily-review list [--limit 7]
daily-review validate --date YYYY-MM-DD
daily-review weekly [--date YYYY-MM-DD]
```

## 提案版と確定版の違い

提案版は、ChatGPTが作った明日の指示書の候補です。`save-proposal` で保存しても、朝に見る確定版にはなりません。

確定版は、ユーザーが `approve-plan` を実行したときだけ作られます。確定版には `status: approved` と `approved_at` が保存されます。

## データ保存場所

日次JSON:

```text
data/daily/YYYY-MM-DD.json
```

日次Markdown:

```text
logs/YYYY-MM-DD.md
```

週次JSON:

```text
data/weekly/YYYY-MM-DD_YYYY-MM-DD.json
```

週次Markdown:

```text
logs/weekly_YYYY-MM-DD_YYYY-MM-DD.md
```

JSON更新は一時ファイルへ書き出してから置き換えるため、保存途中の失敗で既存データを壊しにくい設計です。

## バックアップ方法

`data/` と `logs/` をまとめてコピーしてください。

```bash
cp -R data logs backup-$(date +%Y%m%d)
```

Gitで管理する場合も、最低限 `data/` と `logs/` が残っていれば記録を復元できます。

## よくあるエラー

`target_dateはYYYY-MM-DDにしてください`:
保存元の日付の翌日を `target_date` にしてください。例: `2026-07-13` の夜なら `2026-07-14`。

`Mainは最大3つです`:
Mainを3つ以内に減らしてください。

`タスクに最低ラインがありません`:
すべてのタスクに `minimum_line` を追加してください。

`提案版がないため承認できません`:
先に `save-proposal` を実行してください。

`提案版のみです。まだ未承認です`:
朝に見るには、前夜の記録で `approve-plan` を実行してください。

## テスト

```bash
cd daily-review
pytest
```

または:

```bash
python3 -m pytest
```
