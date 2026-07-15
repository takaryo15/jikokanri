# daily-review 1.2.0 Release Checklist

正式タグは、以下を2回連続で確認してから作成します。本番データへのreplan適用やrestoreは行いません。

- [ ] `pytest`が全件成功する（2回）
- [ ] `python scripts/smoke_v12.py`が成功する（2回）
- [ ] `daily-review --version`が`daily-review 1.2.0`を表示する
- [ ] `daily-review --help`、`goal --help`、`goal design --help`、`goal evaluate --help`、`goal replan --help`を確認する
- [ ] `daily-review doctor`が成功する
- [ ] `daily-review release-check`が成功する
- [ ] `daily-review v11-check`が成功する
- [ ] `daily-review v12-check --verbose`が成功する
- [ ] `daily-review v12-check --json`がJSON以外を出力しない
- [ ] `daily-review migrate --dry-run`と`daily-review migrate --yes`を隔離ディレクトリで確認する
- [ ] 目標設計proposalの受信・review・一度だけのapplyを確認する
- [ ] 火曜始まり・月曜終わり、月末、年末、期限当日/翌日の境界を確認する
- [ ] 週次・月次評価の進捗スナップショットと再現性を確認する
- [ ] replanの選択適用、バックアップ、rollback、transaction履歴を確認する
- [ ] v1.0/v1.1/v1.2旧データのhashがmigrationで変わらないことを確認する
- [ ] `git diff --check`が成功する
- [ ] `git status`に実行時データが含まれない
- [ ] `git log --oneline -5`を確認する

正式リリース手順:

```bash
git add .
git commit -m "Release daily-review v1.2.0"
git push origin main
git tag -a v1.2.0 -m "daily-review v1.2.0"
git push origin v1.2.0
```

GitHub Releaseを作る場合は、`CHANGELOG.md`の1.2.0節を本文に使い、タグ`v1.2.0`を選択します。
