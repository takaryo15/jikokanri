from __future__ import annotations

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def test_start_guides_to_close_day_when_no_daily_data(tmp_path):
    result = runner.invoke(
        app, ["start", "--date", "2026-07-15", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "開始案内｜2026-07-15" in result.output
    assert (
        "daily-review close-day --date 2026-07-15 --clipboard --dry-run"
        in result.output
    )


def test_start_guides_to_today_for_an_approved_plan_without_writing(tmp_path):
    daily = tmp_path / "data" / "daily" / "2026-07-14.json"
    daily.parent.mkdir(parents=True)
    content = """{
  "date": "2026-07-14",
  "created_at": "2026-07-14T22:00:00+09:00",
  "updated_at": "2026-07-14T22:00:00+09:00",
  "tomorrow_plan_final": {
    "status": "approved",
    "target_date": "2026-07-15",
    "main": ["院試"],
    "tasks": [],
    "one_change_tomorrow": "続ける"
  }
}
"""
    daily.write_text(content, encoding="utf-8")
    result = runner.invoke(
        app, ["start", "--date", "2026-07-15", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "daily-review today --date 2026-07-15" in result.output
    assert daily.read_text(encoding="utf-8") == content


def test_start_guides_to_approval_for_a_pending_proposal(tmp_path):
    daily = tmp_path / "data" / "daily" / "2026-07-15.json"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        """{"date":"2026-07-15","tomorrow_plan_proposal":{"target_date":"2026-07-16"}}""",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["start", "--date", "2026-07-15", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "daily-review approve-plan --date 2026-07-15" in result.output
