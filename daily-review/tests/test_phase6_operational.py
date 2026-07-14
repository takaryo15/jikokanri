from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

import daily_review.cli as cli
from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _day1_payload():
    return {
        "date": "2026-07-14",
        "raw_log": "今日は院試の過去問を少し進めた。研究ではRGSスペクトルを確認した。筋トレは休みにした。スマホを触って開始が遅れた。明日は帰宅前に過去問を開きたい。",
        "diary": "疲れていたが、院試と研究を両方少し進められた。",
        "task_results": [],
        "structured_review": {
            "today_main": [
                {"area": "院試", "status": "一部進んだ", "note": "過去問を途中まで進めた"},
                {"area": "研究", "status": "一部進んだ", "note": "RGSスペクトルを確認した"},
                {"area": "筋トレ・健康", "status": "未着手", "note": "休みにした"},
            ],
            "minimum_line": {"院試": "達成", "研究": "達成", "筋トレ・健康": "未達"},
            "what_went_well": ["院試と研究を両方少し進められた"],
            "breakdown_causes": ["スマホ", "疲れ"],
            "one_change_tomorrow": "帰宅前に過去問を開く",
        },
        "tomorrow_plan_proposal": {
            "target_date": "2026-07-15",
            "main": ["院試", "研究", "筋トレ・健康"],
            "tasks": [
                {"area": "院試", "task": "過去問を大問1つ解く", "priority": 1, "minimum_line": "問題文を開く"},
                {"area": "研究", "task": "RGS1とRGS2の図を確認する", "priority": 2, "minimum_line": "図を1枚開く"},
                {"area": "筋トレ・健康", "task": "胸トレを行う", "priority": 3, "minimum_line": "プロテインを飲む"},
            ],
            "one_change_tomorrow": "帰宅前に過去問を開く",
        },
    }


def _day2_payload(task_ids=None):
    task_ids = task_ids or ["task-1", "task-2", "task-3"]
    return {
        "date": "2026-07-15",
        "raw_log": "今日は院試の過去問を大問1つ解いた。研究はRGS1だけ確認した。筋トレはできなかったが、プロテインは飲んだ。帰宅前に過去問を開くことはできた。",
        "diary": "昨日決めた「帰宅前に過去問を開く」を守れたことで、院試を最後まで進めやすかった。",
        "task_results": [
            {"task_id": task_ids[0], "status": "completed", "note": "大問1を解いた", "minimum_line_achieved": True},
            {"task_id": task_ids[1], "status": "partial", "note": "RGS1だけ確認した", "minimum_line_achieved": True},
            {"task_id": task_ids[2], "status": "minimum_only", "note": "プロテインは飲んだ", "minimum_line_achieved": True},
        ],
        "structured_review": {
            "today_main": [
                {"area": "院試", "status": "完了", "note": "過去問を大問1つ解いた"},
                {"area": "研究", "status": "一部進んだ", "note": "RGS1だけ確認した"},
                {"area": "筋トレ・健康", "status": "最低ラインのみ", "note": "プロテインは飲んだ"},
            ],
            "minimum_line": {"院試": "達成", "研究": "達成", "筋トレ・健康": "達成"},
            "what_went_well": ["帰宅前に過去問を開けた"],
            "breakdown_causes": ["疲れ"],
            "one_change_tomorrow": "研究室を出る前にRGS2の図を開く",
        },
        "tomorrow_plan_proposal": {
            "target_date": "2026-07-16",
            "main": ["院試", "研究", "筋トレ・健康"],
            "tasks": [
                {"area": "院試", "task": "過去問の次の大問を1つ解く", "priority": 1, "minimum_line": "問題文を開く"},
                {"area": "研究", "task": "RGS2のスペクトルを確認する", "priority": 2, "minimum_line": "RGS2の図を開く"},
                {"area": "筋トレ・健康", "task": "胸トレを行う", "priority": 3, "minimum_line": "プロテインを飲む"},
            ],
            "one_change_tomorrow": "研究室を出る前にRGS2の図を開く",
        },
    }


def _close_day(tmp_path, payload, *args):
    path = tmp_path / f"night_{payload['date']}.json"
    _write_json(path, payload)
    return runner.invoke(app, ["close-day", "--file", str(path), "--root", str(tmp_path), *args])


def _run_two_day_flow(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert _close_day(tmp_path, _day1_payload(), "--dry-run").exit_code == 0
    assert _close_day(tmp_path, _day1_payload()).exit_code == 0
    assert runner.invoke(app, ["approve-plan", "--date", "2026-07-14", "--root", str(tmp_path)]).exit_code == 0
    today_with_ids = runner.invoke(app, ["today", "--date", "2026-07-15", "--show-ids", "--root", str(tmp_path)])
    assert today_with_ids.exit_code == 0
    ids = []
    for line in today_with_ids.output.splitlines():
        if "[task-" in line:
            ids.append(line.split("[", 2)[1].split("]", 1)[0])
    assert _close_day(tmp_path, _day2_payload(ids), "--dry-run").exit_code == 0
    assert _close_day(tmp_path, _day2_payload(ids)).exit_code == 0
    return ids


def test_two_day_flow_initial_day_closes_without_results(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    result = _close_day(tmp_path, _day1_payload())
    assert result.exit_code == 0
    assert "当日のタスク結果は保存されませんでした" in result.output


def test_two_day_flow_can_approve_day1_proposal(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert _close_day(tmp_path, _day1_payload()).exit_code == 0
    result = runner.invoke(app, ["approve-plan", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "対象日 2026-07-15" in result.output


def test_two_day_flow_day2_finds_previous_final_by_target_date(tmp_path):
    ids = _run_two_day_flow(tmp_path)
    assert ids == ["task-1", "task-2", "task-3"]
    assert load_daily(tmp_path, "2026-07-14")["task_results"][0]["task_id"] == "task-1"


def test_two_day_flow_saves_day2_results_and_review_and_day3_proposal(tmp_path):
    _run_two_day_flow(tmp_path)
    assert len(load_daily(tmp_path, "2026-07-14")["task_results"]) == 3
    assert "帰宅前に過去問" in load_daily(tmp_path, "2026-07-15")["raw_log"]
    assert load_daily(tmp_path, "2026-07-15")["tomorrow_plan_proposal"]["target_date"] == "2026-07-16"


def test_two_day_flow_day3_today_display_after_approval(tmp_path):
    _run_two_day_flow(tmp_path)
    assert runner.invoke(app, ["approve-plan", "--date", "2026-07-15", "--root", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["today", "--date", "2026-07-16", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "今日の指示書｜2026-07-16" in result.output
    assert "[task-" not in result.output


def test_two_day_flow_list_and_weekly_work(tmp_path):
    _run_two_day_flow(tmp_path)
    list_result = runner.invoke(app, ["list", "--root", str(tmp_path)])
    weekly_result = runner.invoke(app, ["weekly", "--date", "2026-07-15", "--root", str(tmp_path)])
    assert list_result.exit_code == 0
    assert "2026-07-14" in list_result.output
    assert "2026-07-15" in list_result.output
    assert weekly_result.exit_code == 0
    assert "タスク実行状況" in weekly_result.output


def _mock_clipboard(monkeypatch, text, system="Darwin"):
    monkeypatch.setattr(cli.platform, "system", lambda: system)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=text))


def test_close_day_clipboard_reads_json(tmp_path, monkeypatch):
    _mock_clipboard(monkeypatch, json.dumps(_day1_payload(), ensure_ascii=False))
    result = runner.invoke(app, ["close-day", "--clipboard", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-14")["raw_log"]


def test_close_day_clipboard_rejects_empty_clipboard(tmp_path, monkeypatch):
    _mock_clipboard(monkeypatch, "   ")
    result = runner.invoke(app, ["close-day", "--clipboard", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "クリップボードが空" in result.output


def test_close_day_clipboard_rejects_file_combo(tmp_path, monkeypatch):
    _mock_clipboard(monkeypatch, json.dumps(_day1_payload(), ensure_ascii=False))
    path = tmp_path / "night.json"
    _write_json(path, _day1_payload())
    result = runner.invoke(app, ["close-day", "--clipboard", "--file", str(path), "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "同時に指定できません" in result.output


def test_json_code_block_is_accepted_from_file(tmp_path):
    path = tmp_path / "night.md"
    path.write_text("```json\n" + json.dumps(_day1_payload(), ensure_ascii=False) + "\n```\n", encoding="utf-8")
    result = runner.invoke(app, ["close-day", "--file", str(path), "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-14")


def test_code_block_with_explanation_is_rejected(tmp_path):
    path = tmp_path / "night.md"
    path.write_text("説明\n```json\n{}\n```\n", encoding="utf-8")
    result = runner.invoke(app, ["close-day", "--file", str(path), "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "JSONだけ" in result.output


def test_multiple_code_blocks_are_rejected(tmp_path):
    path = tmp_path / "night.md"
    path.write_text("```json\n{}\n```\n```json\n{}\n```\n", encoding="utf-8")
    result = runner.invoke(app, ["close-day", "--file", str(path), "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "複数" in result.output


def test_clipboard_dry_run_does_not_save(tmp_path, monkeypatch):
    _mock_clipboard(monkeypatch, json.dumps(_day1_payload(), ensure_ascii=False))
    result = runner.invoke(app, ["close-day", "--clipboard", "--dry-run", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-14") is None


def test_next_guides_to_today_when_final_exists(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert _close_day(tmp_path, _day1_payload()).exit_code == 0
    assert runner.invoke(app, ["approve-plan", "--date", "2026-07-14", "--root", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["next", "--date", "2026-07-15", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "daily-review today --date 2026-07-15" in result.output


def test_next_guides_to_close_day_when_review_missing(tmp_path):
    result = runner.invoke(app, ["next", "--date", "2026-07-15", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "close-day --clipboard --dry-run" in result.output


def test_next_guides_to_approval_when_proposal_pending(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert _close_day(tmp_path, _day1_payload()).exit_code == 0
    result = runner.invoke(app, ["next", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "approve-plan --date 2026-07-14" in result.output


def test_next_guides_to_next_morning_when_complete(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert _close_day(tmp_path, _day1_payload()).exit_code == 0
    assert runner.invoke(app, ["approve-plan", "--date", "2026-07-14", "--root", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["next", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "daily-review today --date 2026-07-15" in result.output


def test_close_day_error_mentions_field_and_no_save(tmp_path):
    payload = _day1_payload()
    payload["tomorrow_plan_proposal"]["tasks"][1]["minimum_line"] = ""
    before = tmp_path / "data" / "daily" / "2026-07-14.json"
    result = _close_day(tmp_path, payload)
    assert result.exit_code != 0
    assert "minimum_line" in result.output
    assert "既存データは変更されていません" in result.output
    assert not before.exists()
