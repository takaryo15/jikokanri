# Changelog

## Unreleased

### Added

- manifest、size、SHA-256、件数、data versionを持つ検証可能なZIPバックアップと`backup create/list/inspect/verify/delete`。
- restoreのmerge/replace/missing-only preview、stale検出付きconfirmation token、復元前自動バックアップ、復元履歴。
- タスクを複製せず元期限と繰越回数を保持する`rollover preview/apply/history`、長期未完了警告・分解提案。
- issue codeとseverityを返す`doctor check`、安全な修復だけを行う`doctor repair`、修復履歴。
- ワークスペース単位のプロセスロック、stale lock回復、v1.3 recovery migrationと互換設定。
- ChatGPTや外部プログラム向けのversioned Command APIとPydantic JSON Request/Response schema。
- request・changes・state hashに結び付いたpreview/commit confirmation tokenと期限・stale検査。
- commit再送を安全に返し、同一keyの内容変更を拒否する永続idempotency記録。
- atomic/best_effortの一括command、単純な前方結果参照、構造化error/warning。
- 日次レビュー、タスク作成・更新・完了・延期・一覧、指示書生成・修正・承認・取得command。
- 見出し・箇条書き・否定・最低限・相対日付を保守的に扱う日本語レビュー正規化。
- `daily-review api execute/schema/history`と`daily-review parse review`。
- 原文を二重保存しないCommand API監査履歴と、ChatGPT連携API仕様書。
- 既存の日次指示書と目標日次計画を統合表示する`daily-review tasks list`、主要フィルター、詳細表示、JSON出力。
- 対話・個別オプション・JSON標準入力に対応する`daily-review review quick`と、dry-run、同日更新前バックアップ。
- reviews・tasks・instructionsを固定列で出力する`daily-review export csv`、期間指定、UTF-8 BOM、決定的な出力。
- 通知条件、メッセージ生成、Console/File送信、履歴を分離した通知基盤と`notifications check/history`。
- タスク、クイックレビュー、CSV、通知の正常系・境界・異常系を覆うv1.3フェーズ1テスト。

### Changed

- 従来の`backup`、`restore BACKUP_FILE`、`doctor`を維持したままpreview-firstのサブコマンドを追加しました。
- 通知設定がない旧環境は組み込みデフォルトを使い、既知項目だけを互換的に上書きするようにしました。
- `doctor`と`release-check`へ通知設定・履歴、およびv1.3コマンド基盤の検査を追加しました。

### Safety

- 復元・repair前の自動バックアップ、復元差分と状態hashの再検証、rollback可能な複数ファイル置換を追加しました。
- バックアップから秘密情報候補・symlink・一時データ・再帰バックアップを除外し、Zip Slipとzip bomb相当を拒否します。
- rolloverはcompleted/cancelled/blocked/never等を除外し、Main最大3件、元期限保持、同日再実行防止を保証します。
- previewでは主要データを変更せず、commit時は確認token・request hash・state hashを再検証します。
- 複数commandの主要保存を一時ファイルとrollback付きで一括置換し、曖昧なタスク候補を自動選択しません。
- Mainの4件目以降と分類不能な自然文をoptional・backlog・unclassifiedへ保持します。
- クイックレビューは原入力をinboxへ先に保存し、日次JSONの失敗時にも生ログを維持します。
- 明日やることの先頭3件だけをMain候補にし、残りをバックログ候補へ保持して自動承認しません。
- CSVは既存出力を明示的な`--force`なしに上書きせず、数式開始文字を無害化します。
- 通知は対象データを含む安定キーで重複送信を抑止し、送信失敗を日次保存処理から分離します。

## 1.2.0 - 2026-07-15

### Added

- vision・long・medium・shortの目標管理と`daily-review goal add/list/show/edit/status/archive`。
- 定性・定量指標、親子目標、期限、進捗率、編集履歴、更新前バックアップ。
- `data/goals/items`を正データとする安全な目標保存、doctor検査、migration、homeの目標概要。
- 目標を期限・依存関係付きのマイルストーンと実行ステップへ分解する`daily-review goal milestone`。
- ロードマップ表示、次アクション選定、変更前バックアップ、`v1.2-goal-roadmap`移行履歴。
- 目標由来の週次重点・日次Main候補、明示承認、手動リンク、日次結果に基づく進捗更新候補。
- 火曜始まりの週次目標評価、暦月の月次評価、診断、根拠付きの修正候補。
- 選択した提案だけをバックアップ・事前検証・原子的保存で適用するreplanワークフロー。
- 外部APIを使わない`goal coach` / `goal coach-receive`と、評価への補助分析保存。
- ChatGPTと往復し、確認後に目標・マイルストーン・ステップを一括作成する`goal design`。
- `v12-check`、`v1.2-final` migration、doctor、release-check、長期E2E・境界・復旧テスト。

### Fixed

- 日付固定handoffテストを実時間に依存させず、期限境界をclock注入で再現可能にしました。
- 評価のrecommendation ID・並び順・進捗差分を同じ入力から再現できるようにしました。
- 月次評価が現在のgoal値から過去進捗を推測せず、保存済み週次評価のスナップショットを使うようにしました。

### Safety

- replanの複数JSON更新にtransaction manifestとrollbackを追加し、部分更新を防止しました。
- 目標設計proposalは未適用で保存し、`--yes`がない限りgoalを作成しません。

## 1.1.0 - 2026-07-14

### Added

- Natural-language raw-text storage through `daily-review input`.
- Per-day raw input inboxes in `data/inbox`.
- Rule-based inbox organization through `daily-review organize`.
- Per-day review drafts in `data/drafts` with source-entry IDs and parser versioning.
- 今日と明日のMain候補、および振り返り・原因・改善・日記・未分類のルールベース分類.
- 整理ドラフトを確認する`daily-review review`、編集する`daily-review edit-draft`、承認する`daily-review approve`.
- ドラフトの承認状態、revision、編集履歴、および承認済みドラフトから既存日次データへの互換的な保存.
- 再承認前の日次JSONバックアップ.
- 自然文入力から承認までを一括実行する`daily-review reflect`.
- 未承認ドラフトから再開する`reflect --resume`、安全条件付きの自動承認`reflect --yes`、統合フローのJSON出力.
- 中断・失敗後の復旧案内、および状態に応じたhomeの次操作表示.
- ChatGPTの構造化JSONを安全に取り込む`daily-review chat-import`と、形式を表示する`daily-review chat-prompt`.
- `chat-schema-1.0`による検証、未知フィールドの警告、インポートハッシュによる重複検知、強制置換前のドラフトバックアップ.
- ChatGPTとの日次往復をまとめる`daily-review chat`、`--prompt-only`、`--copy-prompt`、`--import-only`.
- 動的なChatGPTプロンプト、`data/sessions`による補助セッション管理、`config/priorities.json`の優先順位設定.
- 日付・session ID・prompt hash・有効期限付きの`daily-review handoff`と、安全に照合して受信する`daily-review receive`.
- handoffの一覧・取消、期限・重複・別日回答の拒否、クリップボードとファイル出力の運用.
- 自然文から承認までのv1.1日次ワークフロー、ChatGPT JSON取込、handoff・receive、および中断・再開。
- v1.0環境に不足した保存先だけを追加する`daily-review migrate`と、実運用準備を確認する`daily-review v11-check`。

### Changed

- `home`をChatGPT中心の運用案内へ改善し、`doctor`と`release-check`をv1.1向けに整理。
- 実行時の優先順位設定はGit管理しない`config/priorities.json`とし、`config/priorities.example.json`を配布。

### Safety

- Organization drafts do not modify daily reviews, proposals, final plans, or raw inbox text.
- Unknown or ambiguous sentences are retained as unclassified text instead of being inferred.
- ChatGPTの構造化入力は確認用ドラフトとして保存し、明示的な承認なしに日次記録を変更しません.
- ChatGPTセッションは補助情報であり、日次データやドラフトより優先されません.
- handoffの照合に失敗したChatGPT回答は、inbox・ドラフト・日次データを書き換えません.
- ChatGPT入力の検証、承認済み日次の上書き拒否、原子的JSON書き込みと上書き前バックアップを維持。

### Fixed

- handoffの有効期限を翌日05:00ちょうどから期限切れとして判定。
- `receive --clipboard`でクリップボードを読めない場合に、ファイル入力への代替コマンドを表示。

## 1.0.0 - 2026-07-14

### Added

- Night-review storage with preserved raw logs and structured reviews.
- Tomorrow-plan proposal and explicit approval workflow.
- A maximum of three Main items and a required minimum line for every task.
- Task-result recording and carryover candidates without automatic plan changes.
- `home`, `start`, `summary`, `next`, and `status` for daily operation.
- Tuesday-to-Monday weekly reports and calendar-monthly reports.
- `doctor`, `backup`, `restore`, and `release-check` local safety tools.
- Project-root auto-detection from the repository root or `daily-review/` directory.

### Safety

- Personal `data/`, `logs/`, `backups/`, and ZIP archives are excluded from Git.
- JSON validation and UTF-8 atomic writes protect saved daily records.
- Backups include manifests and SHA-256 hashes; restore validates paths and hashes.
- No external APIs, automatic approvals, automatic carryover, or automatic plan changes.
