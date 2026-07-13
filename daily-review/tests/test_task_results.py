from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()


def _proposal_payload(tasks=None):
    return {
        "target_date": "2026-07-14",
        "main": ["院試", "研究", "筋トレ・健康"],
        "tasks": tasks
        or [
            {"area": "院試", "task": "過去問を大問1つ解く", "priority": 1, "minimum_line": "問題文を開く"},
            {"area": "研究", "task": "RGS1とRGS2のスペクトルを確認する", "priority": 2, "minimum_line": "図を1枚開く"},
            {"area": "筋トレ・健康", "task": "体調を見て軽く運動する", "priority": 3, "minimum_line": "プロテインを飲む"},
        ],
        "one_change_tomorrow": "朝イチで過去問を開く",
    }


def _night_payload():
    return {
        "date": "2026-07-13",
        "raw_log": "今日は研究室に行った。",
        "diary": "少し疲れていた。",
        "structured_review": {
            "today_main": [{"area": "院試", "status": "一部進んだ", "note": "問題文を確認した"}],
            "minimum_line": {"院試": "達成"},
            "what_went_well": ["学校に行けた"],
            "breakdown_causes": ["スマホ"],
            "one_change_tomorrow": "朝イチで過去問を開く",
        },
        "tomorrow_plan_proposal": _proposal_payload(),
    }


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _save_night_and_approve(tmp_path):
    path = tmp_path / "night.json"
    _write_json(path, _night_payload())
    assert runner.invoke(app, ["save-night", "--file", str(path), "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["approve-plan", "--date", "2026-07-13", "--root", str(tmp_path)]).exit_code == 0


def _record_results(tmp_path, results):
    path = tmp_path / "results.json"
    _write_json(path, {"task_results": results})
    return runner.invoke(app, ["record-results", "--date", "2026-07-14", "--file", str(path), "--root", str(tmp_path)])


def _sample_results():
    return [
        {"task_id": "task-1", "status": "completed", "note": "大問1を最後まで解いた", "minimum_line_achieved": True},
        {"task_id": "task-2", "status": "partial", "note": "RGS1だけ確認した", "minimum_line_achieved": True},
        {"task_id": "task-3", "status": "not_started", "note": "疲れていてできなかった", "minimum_line_achieved": False},
    ]


def test_save_proposal_assigns_task_ids(tmp_path):
    path = tmp_path / "proposal.json"
    _write_json(path, _proposal_payload())
    result = runner.invoke(app, ["save-proposal", "--date", "2026-07-13", "--file", str(path), "--root", str(tmp_path)])
    assert result.exit_code == 0
    tasks = load_daily(tmp_path, "2026-07-13")["tomorrow_plan_proposal"]["tasks"]
    assert [task["id"] for task in tasks] == ["task-1", "task-2", "task-3"]


def test_save_proposal_keeps_existing_task_ids(tmp_path):
    payload = _proposal_payload(tasks=[{"id": "exam-main", "area": "院試", "task": "過去問", "priority": 1, "minimum_line": "読む"}])
    path = tmp_path / "proposal.json"
    _write_json(path, payload)
    result = runner.invoke(app, ["save-proposal", "--date", "2026-07-13", "--file", str(path), "--root", str(tmp_path)])
    assert result.exit_code == 0
    task = load_daily(tmp_path, "2026-07-13")["tomorrow_plan_proposal"]["tasks"][0]
    assert task["id"] == "exam-main"


def test_save_proposal_rejects_duplicate_task_ids(tmp_path):
    tasks = [
        {"id": "dup", "area": "院試", "task": "A", "priority": 1, "minimum_line": "読む"},
        {"id": "dup", "area": "研究", "task": "B", "priority": 2, "minimum_line": "開く"},
    ]
    path = tmp_path / "proposal.json"
    _write_json(path, _proposal_payload(tasks=tasks))
    result = runner.invoke(app, ["save-proposal", "--date", "2026-07-13", "--file", str(path), "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "重複" in result.output


def test_approve_plan_keeps_task_ids(tmp_path):
    _save_night_and_approve(tmp_path)
    entry = load_daily(tmp_path, "2026-07-13")
    proposal_ids = [task["id"] for task in entry["tomorrow_plan_proposal"]["tasks"]]
    final_ids = [task["id"] for task in entry["tomorrow_plan_final"]["tasks"]]
    assert final_ids == proposal_ids


def test_record_results_saves_results(tmp_path):
    _save_night_and_approve(tmp_path)
    result = _record_results(tmp_path, _sample_results())
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["task_results"][0]["task_id"] == "task-1"
    assert entry["task_results"][0]["recorded_at"]


def test_record_results_finds_source_json_by_target_date(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, [_sample_results()[0]]).exit_code == 0
    assert load_daily(tmp_path, "2026-07-14") is None
    assert load_daily(tmp_path, "2026-07-13")["task_results"][0]["task_id"] == "task-1"


def test_record_results_fails_without_final_plan(tmp_path):
    result = _record_results(tmp_path, [_sample_results()[0]])
    assert result.exit_code != 0
    assert "確定版" in result.output


def test_record_results_fails_when_only_proposal_exists(tmp_path):
    path = tmp_path / "night.json"
    _write_json(path, _night_payload())
    assert runner.invoke(app, ["save-night", "--file", str(path), "--root", str(tmp_path)]).exit_code == 0
    result = _record_results(tmp_path, [_sample_results()[0]])
    assert result.exit_code != 0
    assert "提案版のみ" in result.output


def test_record_results_rejects_unknown_task_id(tmp_path):
    _save_night_and_approve(tmp_path)
    result = _record_results(
        tmp_path,
        [{"task_id": "missing", "status": "completed", "note": "", "minimum_line_achieved": True}],
    )
    assert result.exit_code != 0
    assert "存在しないtask_id" in result.output


def test_record_results_rejects_duplicate_input_task_id(tmp_path):
    _save_night_and_approve(tmp_path)
    result = _record_results(tmp_path, [_sample_results()[0], _sample_results()[0]])
    assert result.exit_code != 0
    assert "複数" in result.output


def test_record_results_rejects_invalid_status(tmp_path):
    _save_night_and_approve(tmp_path)
    result = _record_results(
        tmp_path,
        [{"task_id": "task-1", "status": "done", "note": "", "minimum_line_achieved": True}],
    )
    assert result.exit_code != 0
    assert "status" in result.output


def test_record_results_error_does_not_change_existing_data(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, [_sample_results()[0]]).exit_code == 0
    before = (tmp_path / "data" / "daily" / "2026-07-13.json").read_text(encoding="utf-8")
    assert _record_results(tmp_path, [{"task_id": "missing", "status": "completed", "minimum_line_achieved": True}]).exit_code != 0
    after = (tmp_path / "data" / "daily" / "2026-07-13.json").read_text(encoding="utf-8")
    assert after == before


def test_record_results_invalid_batch_does_not_partially_save(tmp_path):
    _save_night_and_approve(tmp_path)
    result = _record_results(tmp_path, [_sample_results()[0], {"task_id": "missing", "status": "partial", "minimum_line_achieved": True}])
    assert result.exit_code != 0
    assert load_daily(tmp_path, "2026-07-13").get("task_results") in (None, [])


def test_record_results_updates_existing_result(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, [_sample_results()[0]]).exit_code == 0
    updated = {"task_id": "task-1", "status": "partial", "note": "途中まで", "minimum_line_achieved": True}
    assert _record_results(tmp_path, [updated]).exit_code == 0
    assert load_daily(tmp_path, "2026-07-13")["task_results"][0]["status"] == "partial"


def test_record_results_keeps_unspecified_existing_results(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, _sample_results()[:2]).exit_code == 0
    assert _record_results(tmp_path, [_sample_results()[2]]).exit_code == 0
    ids = {result["task_id"] for result in load_daily(tmp_path, "2026-07-13")["task_results"]}
    assert ids == {"task-1", "task-2", "task-3"}


def test_results_displays_all_tasks(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, _sample_results()).exit_code == 0
    result = runner.invoke(app, ["results", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "過去問を大問1つ解く" in result.output
    assert "RGS1とRGS2" in result.output
    assert "体調を見て軽く運動する" in result.output


def test_results_marks_unrecorded_tasks(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, [_sample_results()[0]]).exit_code == 0
    result = runner.invoke(app, ["results", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "結果: 未記録" in result.output


def test_carryover_displays_only_incomplete_tasks(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, _sample_results()).exit_code == 0
    result = runner.invoke(app, ["carryover", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "RGS1とRGS2" in result.output
    assert "体調を見て軽く運動する" in result.output


def test_carryover_excludes_completed_tasks(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, _sample_results()).exit_code == 0
    result = runner.invoke(app, ["carryover", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert "過去問を大問1つ解く" not in result.output


def test_carryover_excludes_skipped_by_default(tmp_path):
    _save_night_and_approve(tmp_path)
    skipped = {"task_id": "task-3", "status": "skipped", "note": "予定変更", "minimum_line_achieved": False}
    assert _record_results(tmp_path, [_sample_results()[0], _sample_results()[1], skipped]).exit_code == 0
    result = runner.invoke(app, ["carryover", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert "体調を見て軽く運動する" not in result.output


def test_today_show_ids_displays_task_ids(tmp_path):
    _save_night_and_approve(tmp_path)
    result = runner.invoke(app, ["today", "--date", "2026-07-14", "--show-ids", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "[task-1] [院試]" in result.output


def test_today_without_show_ids_hides_task_ids(tmp_path):
    _save_night_and_approve(tmp_path)
    result = runner.invoke(app, ["today", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "[task-1]" not in result.output


def test_markdown_includes_task_results(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, _sample_results()).exit_code == 0
    markdown = (tmp_path / "logs" / "2026-07-13.md").read_text(encoding="utf-8")
    assert "## タスク実行結果｜2026-07-14" in markdown
    assert "メモ：RGS1だけ確認した" in markdown


def test_weekly_calculates_task_completion_rate(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, _sample_results()).exit_code == 0
    result = runner.invoke(app, ["weekly", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "通常タスク完了率: 33.3%（1/3）" in result.output


def test_weekly_calculates_task_minimum_line_rate(tmp_path):
    _save_night_and_approve(tmp_path)
    assert _record_results(tmp_path, _sample_results()).exit_code == 0
    result = runner.invoke(app, ["weekly", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "最低ライン達成率: 66.7%（2/3）" in result.output


def test_weekly_reports_no_task_execution_data(tmp_path):
    result = runner.invoke(app, ["weekly", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "集計対象なし" in result.output


def test_existing_json_without_task_ids_can_record_results(tmp_path):
    daily_dir = tmp_path / "data" / "daily"
    daily_dir.mkdir(parents=True)
    payload = {
        "date": "2026-07-13",
        "tomorrow_plan_final": {
            "status": "approved",
            "target_date": "2026-07-14",
            "main": ["院試"],
            "tasks": [{"area": "院試", "task": "過去問", "priority": 1, "minimum_line": "読む"}],
            "one_change_tomorrow": "朝イチ",
            "approved_at": "2026-07-13T22:00:00+09:00",
        },
        "created_at": "2026-07-13T22:00:00+09:00",
        "updated_at": "2026-07-13T22:00:00+09:00",
    }
    _write_json(daily_dir / "2026-07-13.json", payload)
    result = _record_results(
        tmp_path,
        [{"task_id": "task-1", "status": "completed", "note": "読んだ", "minimum_line_achieved": True}],
    )
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["tomorrow_plan_final"]["tasks"][0]["id"] == "task-1"


def test_record_results_keeps_japanese_task_notes(tmp_path):
    _save_night_and_approve(tmp_path)
    result = _record_results(
        tmp_path,
        [{"task_id": "task-1", "status": "completed", "note": "日本語メモを保持する", "minimum_line_achieved": True}],
    )
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-13")["task_results"][0]["note"] == "日本語メモを保持する"
