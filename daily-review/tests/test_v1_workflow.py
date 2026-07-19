from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()


def _json_file(tmp_path, name: str, payload: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_v1_first_use_night_approval_and_results_workflow(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["doctor", "--root", str(tmp_path)]).exit_code == 0
    for command in ("home", "summary", "start"):
        result = runner.invoke(
            app, [command, "--date", "2026-07-14", "--root", str(tmp_path)]
        )
        assert result.exit_code == 0
        expected = (
            "daily-review handoff --date 2026-07-14 --copy"
            if command == "home"
            else "close-day"
        )
        assert expected in result.output

    raw = tmp_path / "raw.txt"
    raw.write_text("生ログをそのまま残す", encoding="utf-8")
    assert (
        runner.invoke(
            app,
            [
                "save-raw",
                "--date",
                "2026-07-14",
                "--file",
                str(raw),
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    review = _json_file(
        tmp_path,
        "review.json",
        {
            "diary": "任意の日記",
            "structured_review": {
                "today_main": [{"area": "院試", "status": "完了"}],
                "minimum_line": {"院試": "達成"},
                "what_went_well": ["進めた"],
                "breakdown_causes": [],
                "one_change_tomorrow": "続ける",
            },
        },
    )
    assert (
        runner.invoke(
            app,
            [
                "save-review",
                "--date",
                "2026-07-14",
                "--file",
                review,
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    proposal = _json_file(
        tmp_path,
        "proposal.json",
        {
            "target_date": "2026-07-15",
            "main": ["院試"],
            "tasks": [
                {
                    "area": "院試",
                    "task": "過去問",
                    "priority": 1,
                    "minimum_line": "開く",
                }
            ],
            "one_change_tomorrow": "続ける",
        },
    )
    assert (
        runner.invoke(
            app,
            [
                "save-proposal",
                "--date",
                "2026-07-14",
                "--file",
                proposal,
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        "未承認"
        in runner.invoke(
            app, ["show-proposal", "--date", "2026-07-14", "--root", str(tmp_path)]
        ).output
    )
    assert (
        runner.invoke(
            app, ["approve-plan", "--date", "2026-07-14", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert (
        "今日の指示書｜2026-07-15"
        in runner.invoke(
            app, ["today", "--date", "2026-07-15", "--root", str(tmp_path)]
        ).output
    )

    results = _json_file(
        tmp_path,
        "results.json",
        {
            "task_results": [
                {
                    "task_id": "task-1",
                    "status": "partial",
                    "note": "途中まで",
                    "minimum_line_achieved": True,
                }
            ]
        },
    )
    assert (
        runner.invoke(
            app,
            [
                "record-results",
                "--date",
                "2026-07-15",
                "--file",
                results,
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    entry = load_daily(tmp_path, "2026-07-14")
    assert entry["raw_log"] == "生ログをそのまま残す"
    assert entry["diary"] == "任意の日記"
    assert entry["tomorrow_plan_proposal"]["status"] == "pending_review"
    assert entry["tomorrow_plan_final"]["status"] == "approved"
    carryover = runner.invoke(
        app, ["carryover", "--date", "2026-07-15", "--root", str(tmp_path)]
    )
    assert carryover.exit_code == 0
    assert "過去問" in carryover.output


def test_v1_week_month_backup_restore_and_release_check(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    for day in ("2026-07-14", "2026-07-20", "2026-07-31", "2026-08-01"):
        _json_file(
            tmp_path,
            f"{day}.json",
            {
                "date": day,
                "raw_log": day,
                "created_at": f"{day}T22:00:00+09:00",
                "updated_at": f"{day}T22:00:00+09:00",
            },
        )
        source = tmp_path / f"{day}.json"
        destination = tmp_path / "data" / "daily" / f"{day}.json"
        destination.write_bytes(source.read_bytes())
    weekly = runner.invoke(
        app, ["weekly", "--date", "2026-07-20", "--root", str(tmp_path)]
    )
    monthly = runner.invoke(
        app, ["monthly", "--date", "2026-07-31", "--root", str(tmp_path)]
    )
    assert weekly.exit_code == 0
    assert monthly.exit_code == 0
    assert (tmp_path / "data" / "weekly" / "2026-07-14_2026-07-20.json").is_file()
    assert (tmp_path / "data" / "monthly" / "2026-07.json").is_file()

    archive = tmp_path / "v1-backup.zip"
    assert (
        runner.invoke(
            app, ["backup", "--root", str(tmp_path), "--output", str(archive)]
        ).exit_code
        == 0
    )
    restored = tmp_path / "restored"
    assert (
        runner.invoke(app, ["restore", str(archive), "--root", str(restored)]).exit_code
        == 0
    )
    assert (restored / "data" / "daily" / "2026-07-14.json").read_text(
        encoding="utf-8"
    ) == (tmp_path / "data" / "daily" / "2026-07-14.json").read_text(encoding="utf-8")
    assert runner.invoke(app, ["release-check", "--root", str(tmp_path)]).exit_code == 0


def test_v1_summary_reports_invalid_json_without_traceback(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    broken = tmp_path / "data" / "daily" / "2026-07-14.json"
    broken.write_text("{", encoding="utf-8")
    result = runner.invoke(
        app, ["summary", "--date", "2026-07-14", "--root", str(tmp_path)]
    )
    assert result.exit_code == 3
    assert "ERROR:" in result.output
    assert "Traceback" not in result.output


def test_release_check_is_static_and_does_not_mutate_uninitialized_root(tmp_path):
    result = runner.invoke(app, ["release-check", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "daily-review release-check: OK" in result.output
    assert not list(tmp_path.iterdir())
