from __future__ import annotations

import json

from typer.testing import CliRunner

import daily_review.storage as storage
from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()


def _write_json(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _previous_night_payload():
    return {
        "date": "2026-07-13",
        "raw_log": "前日のログ",
        "diary": "前日の日記",
        "structured_review": {
            "today_main": [
                {"area": "院試", "status": "一部進んだ", "note": "問題文を見た"}
            ],
            "minimum_line": {"院試": "達成"},
            "what_went_well": ["学校に行けた"],
            "breakdown_causes": ["スマホ"],
            "one_change_tomorrow": "朝イチで過去問を開く",
        },
        "tomorrow_plan_proposal": {
            "target_date": "2026-07-14",
            "main": ["院試", "研究", "筋トレ・健康"],
            "tasks": [
                {
                    "area": "院試",
                    "task": "過去問を大問1つ解く",
                    "priority": 1,
                    "minimum_line": "問題文を開く",
                },
                {
                    "area": "研究",
                    "task": "RGS1とRGS2のスペクトルを確認する",
                    "priority": 2,
                    "minimum_line": "図を1枚開く",
                },
                {
                    "area": "筋トレ・健康",
                    "task": "胸トレを行う",
                    "priority": 3,
                    "minimum_line": "プロテインを飲む",
                },
            ],
            "one_change_tomorrow": "朝イチで過去問を開く",
        },
    }


def _close_day_payload(**overrides):
    payload = {
        "date": "2026-07-14",
        "raw_log": "今日は院試の過去問を大問1つ解いた。研究はRGS1だけ確認した。",
        "diary": "少し疲れていたが、院試を進められたのはよかった。",
        "task_results": [
            {
                "task_id": "task-1",
                "status": "completed",
                "note": "過去問の大問1を最後まで解いた",
                "minimum_line_achieved": True,
            },
            {
                "task_id": "task-2",
                "status": "partial",
                "note": "RGS1だけ確認した",
                "minimum_line_achieved": True,
            },
            {
                "task_id": "task-3",
                "status": "not_started",
                "note": "疲れていてできなかった",
                "minimum_line_achieved": False,
            },
        ],
        "structured_review": {
            "today_main": [
                {"area": "院試", "status": "完了", "note": "過去問の大問1を解いた"},
                {"area": "研究", "status": "一部進んだ", "note": "RGS1を確認した"},
                {
                    "area": "筋トレ・健康",
                    "status": "未着手",
                    "note": "今日は実施しなかった",
                },
            ],
            "minimum_line": {"院試": "達成", "研究": "達成", "筋トレ・健康": "未達"},
            "what_went_well": [
                "院試の過去問を最後まで解けた",
                "研究も完全な未着手にはならなかった",
            ],
            "breakdown_causes": ["疲れ", "帰宅後のスマホ"],
            "one_change_tomorrow": "帰宅前に過去問を開く",
        },
        "tomorrow_plan_proposal": {
            "target_date": "2026-07-15",
            "main": ["院試", "研究", "筋トレ・健康"],
            "tasks": [
                {
                    "area": "院試",
                    "task": "過去問の次の大問を1つ解く",
                    "priority": 1,
                    "minimum_line": "問題文を開く",
                },
                {
                    "area": "研究",
                    "task": "RGS2のスペクトルを確認する",
                    "priority": 2,
                    "minimum_line": "RGS2の図を1枚開く",
                },
                {
                    "area": "筋トレ・健康",
                    "task": "胸トレを行う",
                    "priority": 3,
                    "minimum_line": "プロテインを飲む",
                },
            ],
            "one_change_tomorrow": "帰宅前に過去問を開く",
        },
    }
    payload.update(overrides)
    return payload


def _setup_previous_final(tmp_path):
    path = tmp_path / "previous-night.json"
    _write_json(path, _previous_night_payload())
    assert (
        runner.invoke(
            app, ["save-night", "--file", str(path), "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["approve-plan", "--date", "2026-07-13", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )


def _close_day(tmp_path, payload=None, *extra_args):
    path = tmp_path / "night.json"
    _write_json(path, payload or _close_day_payload())
    return runner.invoke(
        app, ["close-day", "--file", str(path), "--root", str(tmp_path), *extra_args]
    )


def test_close_day_saves_results_and_review_together(tmp_path):
    _setup_previous_final(tmp_path)
    result = _close_day(tmp_path)
    assert result.exit_code == 0
    assert len(load_daily(tmp_path, "2026-07-13")["task_results"]) == 3
    assert load_daily(tmp_path, "2026-07-14")["raw_log"].startswith("今日は院試")


def test_close_day_stores_task_results_in_previous_source_json(tmp_path):
    _setup_previous_final(tmp_path)
    assert _close_day(tmp_path).exit_code == 0
    assert load_daily(tmp_path, "2026-07-13")["task_results"][0]["task_id"] == "task-1"
    assert (
        "task_results" not in load_daily(tmp_path, "2026-07-14")
        or not load_daily(tmp_path, "2026-07-14")["task_results"]
    )


def test_close_day_stores_review_in_current_day_json(tmp_path):
    _setup_previous_final(tmp_path)
    assert _close_day(tmp_path).exit_code == 0
    entry = load_daily(tmp_path, "2026-07-14")
    assert entry["diary"] == "少し疲れていたが、院試を進められたのはよかった。"
    assert entry["structured_review"]["minimum_line"]["筋トレ・健康"] == "未達"


def test_close_day_saves_next_day_proposal_target_date(tmp_path):
    _setup_previous_final(tmp_path)
    assert _close_day(tmp_path).exit_code == 0
    proposal = load_daily(tmp_path, "2026-07-14")["tomorrow_plan_proposal"]
    assert proposal["target_date"] == "2026-07-15"


def test_close_day_does_not_auto_create_final(tmp_path):
    _setup_previous_final(tmp_path)
    assert _close_day(tmp_path).exit_code == 0
    assert "tomorrow_plan_final" not in load_daily(tmp_path, "2026-07-14")


def test_close_day_preserves_existing_final_on_current_day(tmp_path):
    _setup_previous_final(tmp_path)
    current_dir = tmp_path / "data" / "daily"
    current_dir.mkdir(parents=True, exist_ok=True)
    existing_final = {
        "status": "approved",
        "target_date": "2026-07-15",
        "main": ["院試"],
        "tasks": [
            {
                "id": "task-1",
                "area": "院試",
                "task": "既存",
                "priority": 1,
                "minimum_line": "読む",
            }
        ],
        "one_change_tomorrow": "既存",
        "approved_at": "2026-07-14T21:00:00+09:00",
    }
    _write_json(
        current_dir / "2026-07-14.json",
        {
            "date": "2026-07-14",
            "tomorrow_plan_final": existing_final,
            "created_at": "2026-07-14T21:00:00+09:00",
            "updated_at": "2026-07-14T21:00:00+09:00",
        },
    )
    assert _close_day(tmp_path).exit_code == 0
    assert load_daily(tmp_path, "2026-07-14")["tomorrow_plan_final"] == existing_final


def test_close_day_keeps_unspecified_existing_results(tmp_path):
    _setup_previous_final(tmp_path)
    one_result = [
        {
            "task_id": "task-1",
            "status": "completed",
            "note": "先に保存",
            "minimum_line_achieved": True,
        }
    ]
    assert (
        runner.invoke(
            app,
            ["record-results", "--date", "2026-07-14", "--root", str(tmp_path)],
            input=json.dumps({"task_results": one_result}, ensure_ascii=False),
        ).exit_code
        == 0
    )
    payload = _close_day_payload(task_results=[_close_day_payload()["task_results"][1]])
    assert _close_day(tmp_path, payload).exit_code == 0
    ids = {
        result["task_id"]
        for result in load_daily(tmp_path, "2026-07-13")["task_results"]
    }
    assert ids == {"task-1", "task-2"}


def test_close_day_unknown_task_id_changes_neither_file(tmp_path):
    _setup_previous_final(tmp_path)
    payload = _close_day_payload(
        task_results=[
            {
                "task_id": "missing",
                "status": "completed",
                "note": "",
                "minimum_line_achieved": True,
            }
        ]
    )
    before_previous = (tmp_path / "data" / "daily" / "2026-07-13.json").read_text(
        encoding="utf-8"
    )
    result = _close_day(tmp_path, payload)
    assert result.exit_code != 0
    assert (tmp_path / "data" / "daily" / "2026-07-13.json").read_text(
        encoding="utf-8"
    ) == before_previous
    assert load_daily(tmp_path, "2026-07-14") is None


def test_close_day_invalid_status_changes_neither_file(tmp_path):
    _setup_previous_final(tmp_path)
    payload = _close_day_payload(
        task_results=[
            {
                "task_id": "task-1",
                "status": "done",
                "note": "",
                "minimum_line_achieved": True,
            }
        ]
    )
    result = _close_day(tmp_path, payload)
    assert result.exit_code != 0
    assert load_daily(tmp_path, "2026-07-14") is None
    assert "task_results" not in load_daily(tmp_path, "2026-07-13")


def test_close_day_invalid_proposal_does_not_save_results(tmp_path):
    _setup_previous_final(tmp_path)
    proposal = _close_day_payload()["tomorrow_plan_proposal"]
    proposal["main"] = ["院試", "研究", "筋トレ・健康", "読書"]
    result = _close_day(tmp_path, _close_day_payload(tomorrow_plan_proposal=proposal))
    assert result.exit_code != 0
    assert "task_results" not in load_daily(tmp_path, "2026-07-13")
    assert load_daily(tmp_path, "2026-07-14") is None


def test_close_day_blank_raw_log_saves_nothing(tmp_path):
    _setup_previous_final(tmp_path)
    result = _close_day(tmp_path, _close_day_payload(raw_log=" "))
    assert result.exit_code != 0
    assert "task_results" not in load_daily(tmp_path, "2026-07-13")
    assert load_daily(tmp_path, "2026-07-14") is None


def test_close_day_rejects_too_many_main_items_without_saving(tmp_path):
    _setup_previous_final(tmp_path)
    proposal = _close_day_payload()["tomorrow_plan_proposal"]
    proposal["main"] = ["院試", "研究", "筋トレ・健康", "読書"]
    result = _close_day(tmp_path, _close_day_payload(tomorrow_plan_proposal=proposal))
    assert result.exit_code != 0
    assert load_daily(tmp_path, "2026-07-14") is None


def test_close_day_rejects_mismatched_date_without_saving(tmp_path):
    _setup_previous_final(tmp_path)
    result = _close_day(tmp_path, _close_day_payload(), "--date", "2026-07-13")
    assert result.exit_code != 0
    assert load_daily(tmp_path, "2026-07-14") is None


def test_close_day_empty_task_results_still_saves_review(tmp_path):
    payload = _close_day_payload(task_results=[])
    result = _close_day(tmp_path, payload)
    assert result.exit_code == 0
    assert "タスク結果" in result.output
    assert load_daily(tmp_path, "2026-07-14")["raw_log"].startswith("今日は院試")


def test_close_day_missing_task_results_still_saves_review(tmp_path):
    payload = _close_day_payload()
    payload.pop("task_results")
    result = _close_day(tmp_path, payload)
    assert result.exit_code == 0
    assert "保存されていません" in result.output
    assert load_daily(tmp_path, "2026-07-14")["diary"]


def test_close_day_warns_when_task_results_missing(tmp_path):
    payload = _close_day_payload()
    payload.pop("task_results")
    result = _close_day(tmp_path, payload)
    assert result.exit_code == 0
    assert "警告" in result.output
    assert "当日のタスク結果" in result.output


def test_close_day_preserves_unspecified_current_fields(tmp_path):
    current_dir = tmp_path / "data" / "daily"
    current_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        current_dir / "2026-07-14.json",
        {
            "date": "2026-07-14",
            "custom_note": "消さない",
            "created_at": "2026-07-14T20:00:00+09:00",
            "updated_at": "2026-07-14T20:00:00+09:00",
        },
    )
    payload = _close_day_payload(task_results=[])
    assert _close_day(tmp_path, payload).exit_code == 0
    assert load_daily(tmp_path, "2026-07-14")["custom_note"] == "消さない"


def test_close_day_dry_run_does_not_change_files(tmp_path):
    _setup_previous_final(tmp_path)
    before = (tmp_path / "data" / "daily" / "2026-07-13.json").read_text(
        encoding="utf-8"
    )
    result = _close_day(tmp_path, _close_day_payload(), "--dry-run")
    assert result.exit_code == 0
    assert (tmp_path / "data" / "daily" / "2026-07-13.json").read_text(
        encoding="utf-8"
    ) == before
    assert load_daily(tmp_path, "2026-07-14") is None


def test_close_day_dry_run_shows_planned_files(tmp_path):
    _setup_previous_final(tmp_path)
    result = _close_day(tmp_path, _close_day_payload(), "--dry-run")
    assert result.exit_code == 0
    assert "data/daily/2026-07-13.json" in result.output
    assert "data/daily/2026-07-14.json" in result.output


def test_close_day_dry_run_invalid_input_exits_nonzero(tmp_path):
    _setup_previous_final(tmp_path)
    result = _close_day(tmp_path, _close_day_payload(raw_log=""), "--dry-run")
    assert result.exit_code != 0


def test_close_day_keeps_japanese_text(tmp_path):
    _setup_previous_final(tmp_path)
    payload = _close_day_payload(
        raw_log="日本語の生ログを保持する",
        diary="日本語の日記を保持する",
        task_results=[
            {
                "task_id": "task-1",
                "status": "completed",
                "note": "日本語メモを保持する",
                "minimum_line_achieved": True,
            }
        ],
    )
    assert _close_day(tmp_path, payload).exit_code == 0
    assert load_daily(tmp_path, "2026-07-14")["raw_log"] == "日本語の生ログを保持する"
    assert (
        load_daily(tmp_path, "2026-07-13")["task_results"][0]["note"]
        == "日本語メモを保持する"
    )


def test_close_day_updates_both_markdown_logs(tmp_path):
    _setup_previous_final(tmp_path)
    assert _close_day(tmp_path).exit_code == 0
    previous_markdown = (tmp_path / "logs" / "2026-07-13.md").read_text(
        encoding="utf-8"
    )
    current_markdown = (tmp_path / "logs" / "2026-07-14.md").read_text(encoding="utf-8")
    assert "タスク実行結果｜2026-07-14" in previous_markdown
    assert "今日は院試の過去問" in current_markdown


def test_close_day_temp_write_failure_keeps_original_files(tmp_path, monkeypatch):
    _setup_previous_final(tmp_path)
    current_dir = tmp_path / "data" / "daily"
    _write_json(
        current_dir / "2026-07-14.json",
        {
            "date": "2026-07-14",
            "raw_log": "既存",
            "created_at": "2026-07-14T20:00:00+09:00",
            "updated_at": "2026-07-14T20:00:00+09:00",
        },
    )
    before_previous = (current_dir / "2026-07-13.json").read_text(encoding="utf-8")
    before_current = (current_dir / "2026-07-14.json").read_text(encoding="utf-8")
    original_mkstemp = storage.tempfile.mkstemp
    calls = {"count": 0}

    def failing_mkstemp(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 3:
            raise OSError("simulated temp failure")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(storage.tempfile, "mkstemp", failing_mkstemp)
    result = _close_day(tmp_path)
    assert result.exit_code != 0
    assert (current_dir / "2026-07-13.json").read_text(
        encoding="utf-8"
    ) == before_previous
    assert (current_dir / "2026-07-14.json").read_text(
        encoding="utf-8"
    ) == before_current
