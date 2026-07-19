# 週次・月次レポート

週は火曜日から月曜日、月次は暦月です。

```bash
daily-review weekly --date YYYY-MM-DD --dry-run
daily-review weekly --date YYYY-MM-DD
daily-review weekly --date YYYY-MM-DD --approve
daily-review monthly --date YYYY-MM-DD --dry-run
daily-review monthly --date YYYY-MM-DD
daily-review monthly --date YYYY-MM-DD --approve
```

生成物は最初は`draft`です。日次データのSHA-256 source revisionを保持し、
生成後に元データが変わったstaleレポートは承認できません。再生成内容を確認し、
必要な場合だけ`--force`を指定します。

Main完了率、最低限達成率、繰越率相当の件数、原因分類、カテゴリ別完了率、
長期未完了、改善候補を保存します。月次には前月比較と次月重点候補を含めます。
