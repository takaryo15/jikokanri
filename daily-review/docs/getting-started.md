# はじめに

## インストール

Python 3.11以上を用意し、リポジトリ内で次を実行します。

```bash
python -m pip install -e .
daily-review --version
```

## 初回設定

```bash
daily-review setup --dry-run
daily-review setup
daily-review migrate check
daily-review migrate apply --dry-run
daily-review doctor
daily-review onboarding
```

`setup`は書き込み前に設定を表示し、既存設定を上書きしません。schedulerと
launchdは既定で起動しません。launchdを使う場合も、先に
`daily-review scheduler install --dry-run`で内容を確認してください。
対話setupではデータ保存先、火曜開始、Main最大3件、最低限ライン、通知時刻、
quiet hours、backup、scheduler、launchdを順に確認します。

## 初日の安全確認

```bash
daily-review close-day --date YYYY-MM-DD --clipboard --dry-run
daily-review home --date YYYY-MM-DD
```

`--root PATH`を明示すれば、その保存先が常に最優先です。
