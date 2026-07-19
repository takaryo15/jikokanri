# Operational flows

## Morning

`daily-review flow morning`は今日の確定指示書、Main最大3件、最低限、期限超過、前日未完了、次の操作を読み取ります。確定版がなければ警告し、作成や承認はしません。

## Nightly

`daily-review flow nightly`は当日レビューを確認し、レビュー済みで未生成なら翌日の指示書draftを生成します。その後、未承認通知、rollover preview、Main・最低限、軽量integrity、summaryをまとめます。

`close-day`はユーザー入力とレビュー保存、nightly flowは保存後の安全な後処理という責務です。nightly flowは指示書を承認せず、rolloverをapplyしません。

## Weekly

`daily-review flow weekly`は火曜開始・月曜終了の週を対象に、source revision付きの未承認draft、ChatGPT向け要約、「来週変えることを1つ」の候補を作ります。月曜夜は当日まで、火曜朝のmissed実行は直前週を対象にします。

## Monthly

`daily-review flow monthly`は対象月の将来日を除外し、前月比較と次月重点候補を持つdraftを作り、変更がある実実行時だけ検証可能なbackupを作成します。月初に月指定を省略すると前月が対象です。

すべてのflowは`--dry-run`と`--format json`に対応します。実実行は`data/scheduler/audit`と`idempotency`へ記録され、同じ対象の再送は保存済み結果を返します。
