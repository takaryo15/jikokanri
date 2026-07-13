from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()


def _night_payload(**overrides):
    payload = {
        "date": "2026-07-13",
        "raw_log": "今日は研究室に行った。\n院試は少しだけ進めた。",
        "diary": "少し疲れていたが、完全に何もしない日にはならなかった。",
        "structured_review": {
            "today_main": [
                {"area": "院試", "status": "一部進んだ", "note": "過去問の問題文を確認した"},
                {"area": "研究", "status": "一部進んだ", "note": "RGSスペクトルを確認した"},
            ],
            "minimum_line": {"院試": "達成", "研究": "達成"},
            "what_went_well": ["学校に行けた"],
            "breakdown_causes": ["スマホ", "疲れ"],
            "one_change_tomorrow": "朝イチで過去問を開く",
        },
        "tomorrow_plan_proposal": {
            "target_date": "2026-07-14",
            "main": ["院試", "研究", "筋トレ・健康"],
            "tasks": [
                {"area": "院試", "task": "過去問を大問1つ解く", "priority": 1, "minimum_line": "問題文を開く"},
                {"area": "研究", "task": "RGS1とRGS2のスペクトルを確認する", "priority": 2, "minimum_line": "図を1枚開く"},
                {"area": "筋トレ・健康", "task": "体調を見て軽く運動する", "priority": 3, "minimum_line": "プロテインを飲む"},
            ],
            "one_change_tomorrow": "朝イチで過去問を開く",
        },
    }
    payload.update(overrides)
    return payload


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _save_night(tmp_path, payload=None, *extra_args):
    path = tmp_path / "night.json"
    _write_json(path, payload or _night_payload())
    return runner.invoke(app, ["save-night", "--file", str(path), "--root", str(tmp_path), *extra_args])


def _write_existing_entry(tmp_path, payload):
    daily_dir = tmp_path / "data" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / f"{payload['date']}.json"
    _write_json(path, payload)
    return path


def test_save_night_saves_raw_diary_review_and_proposal(tmp_path):
    result = _save_night(tmp_path)
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["raw_log"] == "今日は研究室に行った。\n院試は少しだけ進めた。"
    assert entry["diary"] == "少し疲れていたが、完全に何もしない日にはならなかった。"
    assert entry["structured_review"]["today_main"][0]["area"] == "院試"
    assert entry["tomorrow_plan_proposal"]["status"] == "pending_review"


def test_save_night_does_not_create_final_plan(tmp_path):
    result = _save_night(tmp_path)
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert "tomorrow_plan_final" not in entry


def test_save_night_uses_json_date_when_cli_date_is_missing(tmp_path):
    result = _save_night(tmp_path)
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-13") is not None


def test_save_night_uses_cli_date_when_json_date_is_missing(tmp_path):
    payload = _night_payload()
    payload.pop("date")
    result = _save_night(tmp_path, payload, "--date", "2026-07-13")
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-13") is not None


def test_save_night_accepts_standard_input(tmp_path):
    payload = _night_payload()
    payload.pop("date")
    result = runner.invoke(
        app,
        ["save-night", "--date", "2026-07-13", "--root", str(tmp_path)],
        input=json.dumps(payload, ensure_ascii=False),
    )
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-13")["tomorrow_plan_proposal"]["target_date"] == "2026-07-14"


def test_save_night_allows_matching_cli_and_json_dates(tmp_path):
    result = _save_night(tmp_path, _night_payload(), "--date", "2026-07-13")
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-13") is not None


def test_save_night_rejects_mismatched_dates_without_saving(tmp_path):
    result = _save_night(tmp_path, _night_payload(), "--date", "2026-07-12")
    assert result.exit_code != 0
    assert "一致しません" in result.output
    assert load_daily(tmp_path, "2026-07-12") is None
    assert load_daily(tmp_path, "2026-07-13") is None


def test_save_night_mismatched_date_does_not_change_existing_json(tmp_path):
    existing = {
        "date": "2026-07-12",
        "raw_log": "既存",
        "created_at": "2026-07-12T22:00:00+09:00",
        "updated_at": "2026-07-12T22:00:00+09:00",
    }
    path = _write_existing_entry(tmp_path, existing)
    before = path.read_text(encoding="utf-8")
    result = _save_night(tmp_path, _night_payload(), "--date", "2026-07-12")
    assert result.exit_code != 0
    assert path.read_text(encoding="utf-8") == before


def test_save_night_input_error_does_not_partially_save(tmp_path):
    payload = _night_payload(raw_log="   ")
    result = _save_night(tmp_path, payload)
    assert result.exit_code != 0
    assert load_daily(tmp_path, "2026-07-13") is None
    assert not (tmp_path / "logs" / "2026-07-13.md").exists()


def test_save_night_keeps_existing_final_plan(tmp_path):
    final = {
        "status": "approved",
        "target_date": "2026-07-14",
        "main": ["院試"],
        "tasks": [{"area": "院試", "task": "既存の確定タスク", "priority": 1, "minimum_line": "読む"}],
        "one_change_tomorrow": "既存",
        "approved_at": "2026-07-13T22:30:00+09:00",
    }
    _write_existing_entry(
        tmp_path,
        {
            "date": "2026-07-13",
            "tomorrow_plan_final": final,
            "created_at": "2026-07-13T22:00:00+09:00",
            "updated_at": "2026-07-13T22:00:00+09:00",
        },
    )
    result = _save_night(tmp_path)
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["tomorrow_plan_final"] == final
    assert entry["tomorrow_plan_proposal"]["tasks"][0]["task"] == "過去問を大問1つ解く"


def test_save_night_keeps_existing_unspecified_fields(tmp_path):
    _write_existing_entry(
        tmp_path,
        {
            "date": "2026-07-13",
            "custom_note": "消さない",
            "created_at": "2026-07-13T22:00:00+09:00",
            "updated_at": "2026-07-13T22:00:00+09:00",
        },
    )
    result = _save_night(tmp_path)
    assert result.exit_code == 0
    assert load_daily(tmp_path, "2026-07-13")["custom_note"] == "消さない"


def test_save_night_rejects_more_than_three_main_items(tmp_path):
    payload = _night_payload(
        tomorrow_plan_proposal={
            **_night_payload()["tomorrow_plan_proposal"],
            "main": ["院試", "研究", "筋トレ・健康", "読書"],
        }
    )
    result = _save_night(tmp_path, payload)
    assert result.exit_code != 0
    assert "Mainは最大3つ" in result.output
    assert load_daily(tmp_path, "2026-07-13") is None


def test_save_night_saves_with_warning_when_task_count_is_seven(tmp_path):
    proposal = _night_payload()["tomorrow_plan_proposal"]
    proposal["tasks"] = [
        {"area": "院試", "task": f"task {index}", "priority": index + 1, "minimum_line": "読む"}
        for index in range(7)
    ]
    result = _save_night(tmp_path, _night_payload(tomorrow_plan_proposal=proposal))
    assert result.exit_code == 0
    assert "警告" in result.output
    assert "通常タスク" in result.output
    assert len(load_daily(tmp_path, "2026-07-13")["tomorrow_plan_proposal"]["tasks"]) == 7


def test_save_night_rejects_blank_raw_log(tmp_path):
    result = _save_night(tmp_path, _night_payload(raw_log="\n\t"))
    assert result.exit_code != 0
    assert "raw_log" in result.output
    assert load_daily(tmp_path, "2026-07-13") is None


def test_show_proposal_displays_proposal_summary(tmp_path):
    assert _save_night(tmp_path).exit_code == 0
    result = runner.invoke(app, ["show-proposal", "--date", "2026-07-13", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "明日の指示書・提案版｜2026-07-14" in result.output
    assert "1. 院試" in result.output
    assert "[院試] 過去問を大問1つ解く" in result.output


def test_show_proposal_makes_unapproved_status_explicit(tmp_path):
    assert _save_night(tmp_path).exit_code == 0
    result = runner.invoke(app, ["show-proposal", "--date", "2026-07-13", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "状態: 未承認" in result.output


def test_save_night_updates_daily_markdown(tmp_path):
    assert _save_night(tmp_path).exit_code == 0
    markdown = (tmp_path / "logs" / "2026-07-13.md").read_text(encoding="utf-8")
    assert "## 生ログ" in markdown
    assert "## 日記" in markdown
    assert "## 明日の指示書・提案版" in markdown


def test_save_night_keeps_japanese_multiline_raw_log(tmp_path):
    raw = "今日は研究室に行った。\n日本語の複数行ログ。\n改行を残す。"
    assert _save_night(tmp_path, _night_payload(raw_log=raw)).exit_code == 0
    assert load_daily(tmp_path, "2026-07-13")["raw_log"] == raw


def test_init_creates_chatgpt_night_output_template(tmp_path):
    result = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert result.exit_code == 0
    template = tmp_path / "templates" / "chatgpt_night_output_prompt.md"
    assert template.is_file()
    assert "ChatGPT夜入力用プロンプト" in template.read_text(encoding="utf-8")


def test_init_does_not_overwrite_chatgpt_night_output_template(tmp_path):
    runner.invoke(app, ["init", "--root", str(tmp_path)])
    template = tmp_path / "templates" / "chatgpt_night_output_prompt.md"
    template.write_text("custom", encoding="utf-8")
    result = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert template.read_text(encoding="utf-8") == "custom"
