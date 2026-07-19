from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.date_utils import week_range_for
from daily_review.storage import load_daily


runner = CliRunner()

RAW_LOG = """今日は研究室に行った。
院試の過去問は問題文を少し見ただけで、予定していた大問1つまでは進まなかった。
研究ではRGSのスペクトルを開いて、表示を少し確認できた。
筋トレは休み。
スマホを触ってしまって、勉強を始めるまでに時間がかかった。
ただ、学校に行って研究作業を始められた点はよかった。
明日は朝イチで過去問を開く。
日記としては、今日は少し疲れていたが、完全に何もしない日にはならなかった。
"""

REVIEW = {
    "diary": "今日は少し疲れていたが、完全に何もしない日にはならなかった。",
    "structured_review": {
        "today_main": [
            {
                "area": "院試",
                "status": "一部進んだ",
                "note": "過去問の問題文を少し確認した",
            },
            {
                "area": "研究",
                "status": "一部進んだ",
                "note": "RGSスペクトルを開いて表示を確認した",
            },
            {"area": "筋トレ・健康", "status": "未着手", "note": "今日は休みにした"},
        ],
        "minimum_line": {"院試": "達成", "研究": "達成", "筋トレ・健康": "未達"},
        "what_went_well": [
            "学校に行けた",
            "研究作業を始められた",
            "完全に何もしない日にはならなかった",
        ],
        "breakdown_causes": ["スマホ", "疲れ"],
        "one_change_tomorrow": "朝イチで過去問を開く",
    },
}

PROPOSAL = {
    "target_date": "2026-07-14",
    "main": ["院試", "研究", "筋トレ・健康"],
    "tasks": [
        {
            "area": "院試",
            "task": "過去問を大問1つ解く",
            "priority": 1,
            "minimum_line": "問題文を開いて、使う公式を1つ確認する",
        },
        {
            "area": "研究",
            "task": "RGS1とRGS2のスペクトル表示を確認する",
            "priority": 2,
            "minimum_line": "スペクトル図を1枚開く",
        },
        {
            "area": "筋トレ・健康",
            "task": "体調を見て軽く運動する",
            "priority": 3,
            "minimum_line": "プロテインを飲む",
        },
    ],
    "one_change_tomorrow": "朝イチで過去問を開く",
}


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _save_phase2_day(tmp_path, approve: bool = True):
    raw_path = tmp_path / "raw.txt"
    review_path = tmp_path / "review.json"
    proposal_path = tmp_path / "proposal.json"
    raw_path.write_text(RAW_LOG, encoding="utf-8")
    _write_json(review_path, REVIEW)
    _write_json(proposal_path, PROPOSAL)

    assert (
        runner.invoke(
            app,
            [
                "save-raw",
                "--date",
                "2026-07-13",
                "--file",
                str(raw_path),
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "save-review",
                "--date",
                "2026-07-13",
                "--file",
                str(review_path),
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "save-proposal",
                "--date",
                "2026-07-13",
                "--file",
                str(proposal_path),
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    if approve:
        assert (
            runner.invoke(
                app, ["approve-plan", "--date", "2026-07-13", "--root", str(tmp_path)]
            ).exit_code
            == 0
        )


def test_japanese_multiline_raw_log_is_saved_verbatim(tmp_path):
    _save_phase2_day(tmp_path, approve=False)
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["raw_log"] == RAW_LOG
    assert "研究ではRGSのスペクトル" in entry["raw_log"]


def test_save_review_keeps_raw_log_and_writes_diary(tmp_path):
    _save_phase2_day(tmp_path, approve=False)
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["raw_log"] == RAW_LOG
    assert entry["diary"] == REVIEW["diary"]
    assert entry["structured_review"]["minimum_line"]["筋トレ・健康"] == "未達"


def test_proposal_stays_separate_until_approval(tmp_path):
    _save_phase2_day(tmp_path, approve=False)
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["tomorrow_plan_proposal"]["status"] == "pending_review"
    assert entry["tomorrow_plan_proposal"]["target_date"] == "2026-07-14"
    assert "tomorrow_plan_final" not in entry


def test_approval_keeps_proposal_and_creates_approved_final(tmp_path):
    _save_phase2_day(tmp_path)
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["tomorrow_plan_proposal"]["status"] == "pending_review"
    assert entry["tomorrow_plan_final"]["status"] == "approved"
    assert entry["tomorrow_plan_final"]["approved_at"]


def test_today_uses_target_date_and_is_short(tmp_path):
    _save_phase2_day(tmp_path)
    result = runner.invoke(
        app, ["today", "--date", "2026-07-14", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "今日の指示書｜2026-07-14" in result.output
    assert "1. 院試" in result.output
    assert "最低ライン: 問題文を開いて、使う公式を1つ確認する" in result.output
    assert "今日変えること" in result.output


def test_daily_markdown_contains_diary_and_final_plan(tmp_path):
    _save_phase2_day(tmp_path)
    markdown = (tmp_path / "logs" / "2026-07-13.md").read_text(encoding="utf-8")
    assert "## 日記" in markdown
    assert REVIEW["diary"] in markdown
    assert "## 明日の指示書・確定版" in markdown
    assert "状態：承認済み" in markdown
    assert "対象日：2026-07-14" in markdown


def test_one_day_weekly_summary_is_created_for_tuesday_to_monday(tmp_path):
    _save_phase2_day(tmp_path)
    result = runner.invoke(
        app, ["weekly", "--date", "2026-07-13", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "対象期間: 2026-07-07〜2026-07-13" in result.output
    assert "記録日数: 1" in result.output
    assert "記録日数が少ない" in result.output
    assert (tmp_path / "data" / "weekly" / "2026-07-07_2026-07-13.json").is_file()
    markdown = (tmp_path / "logs" / "weekly_2026-07-07_2026-07-13.md").read_text(
        encoding="utf-8"
    )
    assert "最低ライン達成率" in markdown
    assert "崩れた原因ランキング" in markdown
    assert "確定版指示書を作れた日数：1" in markdown


def test_weekly_period_is_tuesday_to_monday():
    assert week_range_for("2026-07-13") == ("2026-07-07", "2026-07-13")


def test_status_shows_phase2_progress(tmp_path):
    _save_phase2_day(tmp_path)
    result = runner.invoke(
        app, ["status", "--date", "2026-07-13", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "生ログ        保存済み" in result.output
    assert "整形ログ      保存済み" in result.output
    assert "提案版        保存済み" in result.output
    assert "確定版        承認済み" in result.output
    assert "対象日        2026-07-14" in result.output
