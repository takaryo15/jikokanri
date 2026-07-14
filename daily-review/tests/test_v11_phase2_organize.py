from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()
DAY = "2026-07-14"


def _input(root, text: str) -> None:
    result = runner.invoke(app, ["input", "--date", DAY, "--text", text, "--root", str(root)])
    assert result.exit_code == 0, result.output


def _draft(root):
    return json.loads((root / "data" / "drafts" / f"{DAY}.json").read_text(encoding="utf-8"))


def test_organize_creates_expected_rule_based_draft_without_touching_inbox(tmp_path):
    raw = """今日は院試の過去問を2問解いた。
研究はO VIIとO VIIIを確認した。
筋トレは休み。
集中できなかった原因はスマホ。
今日は研究室で先生と話せてよかった。
明日は院試を最優先にして、研究も少し進めたい。"""
    _input(tmp_path, raw)
    inbox_before = (tmp_path / "data" / "inbox" / f"{DAY}.json").read_bytes()
    result = runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    draft = _draft(tmp_path)
    assert draft["parser_version"] == "rule-v1"
    assert len(draft["source_entry_ids"]) == 1
    assert "今日は院試の過去問を2問解いた" in draft["today"]["completed"]
    assert "研究はO VIIとO VIIIを確認した" in draft["today"]["completed"]
    assert "筋トレは休み" in draft["unclassified"]
    assert "集中できなかった原因はスマホ" in draft["reflection"]["problems"]
    assert "スマホ" in draft["reflection"]["causes"]
    assert "今日は研究室で先生と話せてよかった" in draft["reflection"]["good"]
    assert "今日は研究室で先生と話せてよかった" in draft["journal"]
    assert draft["tomorrow"]["main_candidates"] == ["院試を最優先にして", "研究も少し進めたい"]
    assert (tmp_path / "data" / "inbox" / f"{DAY}.json").read_bytes() == inbox_before
    assert not (tmp_path / "data" / "daily" / f"{DAY}.json").exists()


def test_organize_prioritizes_partial_over_completed_and_limits_main_candidates(tmp_path):
    _input(tmp_path, "研究は少し進めた。院試の問題を解いた。読書を読んだ。筋トレをやった。副業を進めた。")
    assert runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)]).exit_code == 0
    draft = _draft(tmp_path)
    assert draft["today"]["partial"] == ["研究は少し進めた"]
    assert "研究は少し進めた" not in draft["today"]["completed"]
    assert len(draft["today"]["main_candidates"]) == 3
    assert draft["today"]["main_candidates"] == ["研究は少し進めた", "院試の問題を解いた", "筋トレをやった"]


def test_organize_incrementally_appends_only_new_entries_and_is_idempotent(tmp_path):
    _input(tmp_path, "院試の問題を解いた。")
    first = runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)])
    first_draft = _draft(tmp_path)
    repeated = runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)])
    assert first.exit_code == repeated.exit_code == 0
    assert "すでに整理済み" in repeated.output
    assert _draft(tmp_path) == first_draft
    _input(tmp_path, "明日は研究を進める。")
    appended = runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)])
    draft = _draft(tmp_path)
    assert appended.exit_code == 0
    assert len(draft["source_entry_ids"]) == 2
    assert draft["today"]["completed"] == ["院試の問題を解いた"]
    assert draft["tomorrow"]["main_candidates"] == ["研究を進める"]


def test_organize_force_rebuilds_from_all_entries(tmp_path):
    _input(tmp_path, "院試の問題を解いた。")
    assert runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)]).exit_code == 0
    path = tmp_path / "data" / "drafts" / f"{DAY}.json"
    draft = _draft(tmp_path)
    draft["today"]["completed"].append("手で追加した候補")
    path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    result = runner.invoke(app, ["organize", "--date", DAY, "--force", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert _draft(tmp_path)["today"]["completed"] == ["院試の問題を解いた"]


def test_organize_dry_run_and_json_do_not_write(tmp_path):
    _input(tmp_path, "院試の問題を解いた。")
    dry_run = runner.invoke(app, ["organize", "--date", DAY, "--dry-run", "--root", str(tmp_path)])
    as_json = runner.invoke(app, ["organize", "--date", DAY, "--dry-run", "--json", "--root", str(tmp_path)])
    assert dry_run.exit_code == as_json.exit_code == 0
    assert "保存は行いませんでした" in dry_run.output
    assert json.loads(as_json.output)["date"] == DAY
    assert not (tmp_path / "data" / "drafts").exists()


def test_organize_rejects_missing_input_and_corrupt_documents_without_overwriting(tmp_path):
    missing = runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)])
    assert missing.exit_code == 2
    assert "先に daily-review input" in missing.output

    inbox = tmp_path / "data" / "inbox" / f"{DAY}.json"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("{", encoding="utf-8")
    corrupt_inbox = runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)])
    assert corrupt_inbox.exit_code == 3
    assert inbox.read_text(encoding="utf-8") == "{"

    inbox.write_text(json.dumps({"date": DAY, "entries": [{"id": "one", "raw_text": "院試を解いた"}]}), encoding="utf-8")
    draft = tmp_path / "data" / "drafts" / f"{DAY}.json"
    draft.parent.mkdir(parents=True)
    draft.write_text("{", encoding="utf-8")
    corrupt_draft = runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)])
    assert corrupt_draft.exit_code == 3
    assert draft.read_text(encoding="utf-8") == "{"


def test_organize_preserves_order_deduplicates_and_keeps_special_tokens(tmp_path):
    _input(tmp_path, "研究はO VII/O VIIIを確認した。\n研究はO VII/O VIIIを確認した。\n明日の予定：院試を進める。")
    assert runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)]).exit_code == 0
    draft = _draft(tmp_path)
    assert draft["today"]["completed"] == ["研究はO VII/O VIIIを確認した"]
    assert draft["tomorrow"]["main_candidates"] == ["院試を進める"]


def test_organize_combines_entries_and_classifies_remaining_categories(tmp_path):
    _input(tmp_path, "院試に着手した。筋トレはできなかった。なぜなら疲れた。明日変えることは朝に始める。")
    _input(tmp_path, "明日は院試を進める。明日は研究を進める。明日は筋トレをやる。明日は読書をする。")
    result = runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    draft = _draft(tmp_path)
    assert len(draft["source_entry_ids"]) == 2
    assert draft["today"]["partial"] == ["院試に着手した"]
    assert draft["today"]["not_completed"] == ["筋トレはできなかった"]
    assert draft["reflection"]["causes"] == ["疲れた"]
    assert draft["reflection"]["change_next"] == ["明日変えることは朝に始める"]
    assert len(draft["tomorrow"]["main_candidates"]) == 3
    assert draft["tomorrow"]["other_tasks"] == ["読書をする"]
    assert "分類済み:" in result.output
    assert "1. 院試に着手した" in result.output


def test_home_shows_inbox_and_draft_status_and_doctor_checks_drafts(tmp_path):
    _input(tmp_path, "院試を解いた。")
    before = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    assert before.exit_code == 0
    assert "自然文入力: 1件" in before.output
    assert "整理ドラフト: 未作成" in before.output
    assert f"daily-review organize --date {DAY}" in before.output

    assert runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)]).exit_code == 0
    after = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    assert after.exit_code == 0
    assert "整理ドラフト: 作成済み" in after.output
    assert "今日のMain候補: 1件" in after.output

    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert doctor.exit_code == 0
    assert "OK   data/drafts" in doctor.output
