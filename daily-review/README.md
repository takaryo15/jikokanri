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

## 毎晩の使い方

1. ChatGPTへ振り返りを送る
2. 生ログを `save-raw` で保存する
3. 整形結果を `save-review` で保存する
4. 明日の指示書を `save-proposal` で保存する
5. `status` と `validate` で保存状況と検証結果を確認する
6. OKなら `approve-plan` する

```bash
daily-review save-raw --date 2026-07-13 --file raw.txt
daily-review save-review --date 2026-07-13 --file review.json
daily-review save-proposal --date 2026-07-13 --file proposal.json
daily-review status --date 2026-07-13
daily-review validate --date 2026-07-13
daily-review approve-plan --date 2026-07-13
```

`save-raw` は `--file` を省略すると標準入力から貼り付けできます。入力後は `Ctrl-D` で保存します。

保存後は、日次JSONとMarkdownの保存先、次に実行するコマンドが表示されます。保存内容をざっと確認したいときは次を使います。

```bash
daily-review status --date 2026-07-13
daily-review list --limit 7
```

日次Markdownは `logs/YYYY-MM-DD.md` に作成されます。生ログ、日記、今日のMain、最低ライン、崩れた原因、提案版、確定版を1ファイルで見返せます。

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
daily-review save-raw --date YYYY-MM-DD [--file raw.txt]
daily-review save-review --date YYYY-MM-DD [--file review.json]
daily-review save-proposal --date YYYY-MM-DD [--file proposal.json]
daily-review approve-plan --date YYYY-MM-DD [--force]
daily-review today [--date YYYY-MM-DD]
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
