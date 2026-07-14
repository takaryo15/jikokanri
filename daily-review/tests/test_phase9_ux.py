from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.storage import resolve_root


runner = CliRunner()


def _write_json(root, relative, payload):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_summary_handles_a_day_with_no_data(tmp_path):
    result = runner.invoke(app, ["summary", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "今日の確定版: 未記録" in result.output
    assert "タスク結果: 未記録" in result.output
    assert "次の操作: daily-review close-day --date 2026-07-14" in result.output


def test_summary_and_home_show_saved_plan_and_next_step(tmp_path):
    _write_json(tmp_path, "data/daily/2026-07-13.json", {
        "date": "2026-07-13",
        "tomorrow_plan_final": {
            "status": "approved", "target_date": "2026-07-14", "main": ["院試"],
            "tasks": [{"id": "task-1", "area": "院試", "task": "過去問", "priority": 1, "minimum_line": "開く"}],
            "one_change_tomorrow": "続ける",
        },
        "task_results": [],
    })
    summary = runner.invoke(app, ["summary", "--date", "2026-07-14", "--root", str(tmp_path)])
    home = runner.invoke(app, ["home", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert summary.exit_code == 0
    assert "今日の確定版: 記録済み" in summary.output
    assert "1. 院試" in summary.output
    assert "次の操作: daily-review today --date 2026-07-14" in summary.output
    assert home.exit_code == 0
    assert "未完了タスク:" in home.output
    assert "過去問" in home.output


def test_root_discovery_prefers_explicit_path_and_finds_child_project(tmp_path, monkeypatch):
    project = tmp_path / "daily-review"
    (project / "src" / "daily_review").mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'daily-review'\n", encoding="utf-8")
    explicit = tmp_path / "custom-root"
    monkeypatch.chdir(tmp_path)
    assert resolve_root() == project
    assert resolve_root(explicit) == explicit.resolve()


def test_doctor_has_consistent_success_footer_and_invalid_date_is_a_user_error(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    invalid_date = runner.invoke(app, ["summary", "--date", "2026/07/14", "--root", str(tmp_path)])
    assert doctor.exit_code == 0
    assert doctor.output.rstrip().endswith("daily-review doctor: OK")
    assert invalid_date.exit_code == 2
    assert "日付は YYYY-MM-DD" in invalid_date.output
