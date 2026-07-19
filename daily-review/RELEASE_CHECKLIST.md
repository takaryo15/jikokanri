# daily-review v1.3.0 Release Checklist

このchecklistはv1.1.0、v1.2.0の完了済み安全条件を維持し、v1.3.0の統合確認を
追加するものです。

## 品質

- [ ] `python -m pytest` 全件成功
- [ ] coverage計測成功
- [ ] `python -m ruff check .` 成功
- [ ] `python -m ruff format --check .` 成功
- [ ] mypyが設定済み対象で成功
- [ ] `python -m build` 成功
- [ ] wheelを一時venvへclean install

## CLI

- [ ] `daily-review --version` が`1.3.0`
- [ ] 全主要commandのhelp確認
- [ ] `daily-review doctor` 成功
- [ ] `daily-review release-check`が`RESULT: READY`
- [ ] `home --format json`がJSONだけを出力
- [ ] setup、config、scheduler、migrationを確認

## データ安全性

- [ ] v1.0形式のfixtureを読み込める
- [ ] migration checkとdry-runが無変更
- [ ] migration前backup作成・検証
- [ ] コピー上でmigration apply
- [ ] restore previewとrollback
- [ ] rollback後のファイルhash・件数一致
- [ ] Main最大3件、最低限、無承認確定なし
- [ ] 二重review、rollover、scheduler slotを重複保存しない
- [ ] path traversal、Zip Slip、symlink、hash不一致を拒否
- [ ] secretとruntime dataをGit追跡しない

## E2E

- [ ] 通常日
- [ ] 体調不良・最低限のみ
- [ ] 未レビュー・missed run
- [ ] 火曜〜月曜の週次と承認
- [ ] 月次、前月比較、将来日除外と承認
- [ ] JSON破損検出とrestore
- [ ] confirmation、idempotency、stale preview
- [ ] scheduler retry、quiet hours、launchd dry-run

## 文書

- [ ] README
- [ ] getting started / daily workflow
- [ ] ChatGPT / tasks / reports
- [ ] backup / restore / scheduler / configuration
- [ ] migration / rollback / troubleshooting / security
- [ ] CHANGELOG
- [ ] v1.3.0 release notes

## Gitとリリース

- [ ] `git diff --check`
- [ ] runtime data未追跡
- [ ] secret scan確認
- [ ] mainへcommit・push
- [ ] working tree clean
- [ ] push後にもrelease-check READY
- [ ] `git tag -a v1.3.0 -m "daily-review v1.3.0"`
- [ ] `git push origin v1.3.0`
- [ ] `docs/releases/v1.3.0.md`からGitHub Release作成

重大な問題、pytest失敗、restore不能、rollback未実証、release-check NOT READYの
いずれかが残る場合はtagを作成しません。
