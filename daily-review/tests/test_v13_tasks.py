from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()
TODAY = "2026-07-15"


def _entry(source_day, target_day, tasks, results=None):
    return {
        "date": source_day,
        "created_at": f"{source_day}T20:00:00+09:00",
        "updated_at": f"{source_day}T22:00:00+09:00",
        "tomorrow_plan_final": {
            "status": "approved",
            "approved_at": f"{source_day}T22:00:00+09:00",
            "target_date": target_day,
            "main": [tasks[0]["area"]],
            "tasks": tasks,
            "one_change_tomorrow": "続ける",
        },
        "task_results": results or [],
    }


def _setup(root):
    daily = root / "data/daily"
    daily.mkdir(parents=True)
    data = [
        _entry(
            "2026-07-12",
            "2026-07-14",
            [
                {
                    "id": "overdue",
                    "area": "exam",
                    "task": "過去問",
                    "priority": 1,
                    "minimum_line": "1問",
                }
            ],
        ),
        _entry(
            "2026-07-14",
            TODAY,
            [
                {
                    "id": "today-high",
                    "area": "exam",
                    "task": "院試",
                    "priority": 1,
                    "minimum_line": "1問",
                },
                {
                    "id": "today-low",
                    "area": "research",
                    "task": "研究",
                    "priority": 3,
                    "minimum_line": "図を開く",
                },
            ],
            [
                {
                    "task_id": "today-low",
                    "status": "completed",
                    "minimum_line_achieved": True,
                    "recorded_at": "2026-07-15T20:00:00+09:00",
                }
            ],
        ),
        _entry(
            "2026-07-15",
            "2026-07-16",
            [
                {
                    "id": "tomorrow",
                    "area": "health",
                    "task": "筋トレ",
                    "priority": 2,
                    "minimum_line": "スクワット1回",
                }
            ],
        ),
    ]
    for value in data:
        (daily / f"{value['date']}.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )


def _json(root, *args):
    result = runner.invoke(
        app,
        [
            "tasks",
            "list",
            "--date",
            TODAY,
            "--format",
            "json",
            *args,
            "--root",
            str(root),
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["tasks"]


def test_default_hides_completed_and_sorts_overdue_first(tmp_path):
    _setup(tmp_path)
    values = _json(tmp_path)
    assert [item["id"] for item in values] == ["overdue", "today-high", "tomorrow"]
    assert "today-low" not in {item["id"] for item in values}


def test_status_priority_category_due_main_minimum_and_combined_filters(tmp_path):
    _setup(tmp_path)
    assert [item["id"] for item in _json(tmp_path, "--status", "completed")] == [
        "today-low"
    ]
    assert {item["id"] for item in _json(tmp_path, "--priority", "high")} == {
        "overdue",
        "today-high",
    }
    assert [item["id"] for item in _json(tmp_path, "--category", "health")] == [
        "tomorrow"
    ]
    assert [item["id"] for item in _json(tmp_path, "--due", "overdue")] == ["overdue"]
    assert [item["id"] for item in _json(tmp_path, "--due", "today")] == ["today-high"]
    assert [item["id"] for item in _json(tmp_path, "--due", "tomorrow")] == ["tomorrow"]
    assert {item["id"] for item in _json(tmp_path, "--main")} == {
        "overdue",
        "today-high",
        "tomorrow",
    }
    assert len(_json(tmp_path, "--minimum")) == 3
    assert [
        item["id"]
        for item in _json(tmp_path, "--due", "today", "--priority", "high", "--main")
    ] == ["today-high"]


def test_all_empty_text_json_and_invalid_options(tmp_path):
    _setup(tmp_path)
    assert len(_json(tmp_path, "--all")) == 4
    empty = runner.invoke(
        app,
        [
            "tasks",
            "list",
            "--category",
            "missing",
            "--date",
            TODAY,
            "--root",
            str(tmp_path),
        ],
    )
    empty_json = _json(tmp_path, "--category", "missing")
    invalid = runner.invoke(
        app, ["tasks", "list", "--status", "bad", "--root", str(tmp_path)]
    )
    assert "条件に一致するタスクはありません" in empty.output
    assert empty_json == [] and invalid.exit_code != 0


def test_human_detail_contains_operational_fields(tmp_path):
    _setup(tmp_path)
    result = runner.invoke(
        app, ["tasks", "list", "--date", TODAY, "--detail", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    for text in ("ID:", "優先度:", "カテゴリ:", "期限:", "参照元:", "Main", "最低限"):
        assert text in result.output
