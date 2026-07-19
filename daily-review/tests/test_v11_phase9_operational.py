from __future__ import annotations

import json
from datetime import date, timedelta
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

import daily_review.cli as cli
import daily_review.handoff as handoff
from daily_review.cli import app


runner = CliRunner()


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setattr(
        handoff,
        "local_now",
        lambda: datetime(2026, 7, 14, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )


def test_formal_release_version_is_110():
    assert runner.invoke(app, ["--version"]).output.strip() == "daily-review 1.3.0"


def _init(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    assert runner.invoke(app, ["migrate", "--yes", "--root", str(root)]).exit_code == 0


def _payload(day: str, item: dict, *, tomorrow: list[str], completed: bool = True):
    task = f"{day}の院試"
    return {
        "schema_version": "1.0",
        "handoff": {
            "version": "1.0",
            "session_id": item["session_id"],
            "date": day,
            "prompt_hash": item["prompt_hash"],
        },
        "date": day,
        "raw_text": f"{task}を進めた。",
        "today": {
            "main": [task],
            "completed": [task] if completed else [],
            "partial": [] if completed else [task],
            "not_completed": [],
        },
        "reflection": {
            "good": ["継続できた"],
            "problems": [],
            "causes": [],
            "change_next": ["朝に始める"],
        },
        "tomorrow": {"main": tomorrow, "other_tasks": [], "minimum": ["問題文を開く"]},
        "journal": [],
        "unclassified": [],
    }


def _issue(root, day: str):
    issued = runner.invoke(app, ["handoff", "--date", day, "--root", str(root)])
    assert issued.exit_code == 0, issued.output
    return json.loads(
        (root / "data" / "handoffs" / f"{day}.json").read_text(encoding="utf-8")
    )["handoffs"][-1]


def test_three_day_operational_flow_keeps_dates_and_sessions_separate(
    tmp_path, monkeypatch
):
    _init(tmp_path)
    day1, day2, day3 = "2026-07-14", "2026-07-15", "2026-07-16"

    first = _issue(tmp_path, day1)
    received = runner.invoke(
        app,
        [
            "receive",
            "--json-text",
            json.dumps(
                _payload(day1, first, tomorrow=["研究を進める"]), ensure_ascii=False
            ),
            "--root",
            str(tmp_path),
        ],
    )
    assert received.exit_code == 0, received.output
    assert (
        runner.invoke(
            app, ["review", "--date", day1, "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "edit-draft",
                "--date",
                day1,
                "--set",
                "journal=初日",
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["approve", "--date", day1, "--yes", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )

    second = _issue(tmp_path, day2)
    prompt = runner.invoke(
        app, ["chat", "--date", day2, "--prompt-only", "--root", str(tmp_path)]
    )
    assert "前日からの引き継ぎ:" in prompt.output
    assert "研究を進める" in prompt.output
    assert (
        runner.invoke(
            app,
            [
                "receive",
                "--json-text",
                json.dumps(
                    _payload(day2, second, tomorrow=["院試を進める"], completed=False),
                    ensure_ascii=False,
                ),
                "--yes",
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )

    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    rejected = runner.invoke(
        app,
        [
            "reflect",
            "--date",
            day3,
            "--text",
            "なんとなく過ごした。",
            "--yes",
            "--root",
            str(tmp_path),
        ],
    )
    assert rejected.exit_code == 2
    assert (
        runner.invoke(
            app,
            [
                "edit-draft",
                "--date",
                day3,
                "--set",
                "unclassified=",
                "--set",
                "today.main_candidates=院試を進める",
                "--set",
                "tomorrow.main_candidates=研究を進める",
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["approve", "--date", day3, "--yes", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )

    for day in (day1, day2, day3):
        assert (tmp_path / "data" / "inbox" / f"{day}.json").is_file()
        assert (tmp_path / "data" / "drafts" / f"{day}.json").is_file()
        assert (tmp_path / "data" / "daily" / f"{day}.json").is_file()
    assert (tmp_path / "data" / "sessions" / f"{day1}.json").is_file()
    assert (tmp_path / "data" / "sessions" / f"{day2}.json").is_file()
    assert (
        "今週の記録日数: 3日"
        in runner.invoke(
            app, ["summary", "--date", day3, "--root", str(tmp_path)]
        ).output
    )
    assert runner.invoke(app, ["doctor", "--root", str(tmp_path)]).exit_code == 0


def test_home_and_checks_handle_a_year_of_daily_files(tmp_path):
    _init(tmp_path)
    daily = tmp_path / "data" / "daily"
    start = date(2025, 7, 21)
    for offset in range(365):
        day = (start + timedelta(days=offset)).isoformat()
        (daily / f"{day}.json").write_text(json.dumps({"date": day}), encoding="utf-8")
    target = "2026-07-14"
    assert (
        runner.invoke(
            app, ["home", "--date", target, "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["summary", "--date", target, "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["doctor", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["v11-check", "--root", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(
            app, ["handoff-list", "--date", target, "--json", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
