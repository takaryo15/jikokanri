from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.notifications import (
    dispatch_notifications,
    evaluate_notifications,
    load_history,
    load_notification_config,
)


runner = CliRunner()
DAY = "2026-07-15"
NOW = datetime(2026, 7, 15, 21, 30, tzinfo=ZoneInfo("Asia/Tokyo"))


def _invoke(root: Path, *args: str):
    return runner.invoke(
        app,
        [
            "notifications",
            "check",
            "--date",
            DAY,
            "--time",
            "21:30",
            *args,
            "--root",
            str(root),
        ],
    )


def _write_daily(root: Path) -> bytes:
    daily = root / "data" / "daily"
    daily.mkdir(parents=True)
    previous = {
        "date": "2026-07-14",
        "created_at": "2026-07-14T22:00:00+09:00",
        "updated_at": "2026-07-14T22:00:00+09:00",
        "tomorrow_plan_final": {
            "status": "approved",
            "target_date": DAY,
            "main": ["院試"],
            "tasks": [
                {
                    "id": "today-main",
                    "area": "院試",
                    "task": "過去問",
                    "priority": 1,
                    "minimum_line": "1問",
                }
            ],
        },
    }
    overdue = {
        "date": "2026-07-12",
        "created_at": "2026-07-12T22:00:00+09:00",
        "updated_at": "2026-07-12T22:00:00+09:00",
        "tomorrow_plan_final": {
            "status": "approved",
            "target_date": "2026-07-13",
            "main": ["研究"],
            "tasks": [
                {
                    "id": "overdue",
                    "area": "研究",
                    "task": "解析",
                    "priority": 2,
                    "minimum_line": "開く",
                }
            ],
        },
    }
    current = {
        "date": DAY,
        "created_at": f"{DAY}T20:00:00+09:00",
        "updated_at": f"{DAY}T20:00:00+09:00",
        "tomorrow_plan_proposal": {
            "status": "pending_review",
            "target_date": "2026-07-16",
            "main": ["運動"],
            "tasks": [
                {
                    "id": "tomorrow",
                    "area": "運動",
                    "task": "散歩",
                    "priority": 3,
                    "minimum_line": "1分",
                }
            ],
        },
    }
    for value in (previous, overdue, current):
        (daily / f"{value['date']}.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
    path = daily / f"{DAY}.json"
    return path.read_bytes()


def test_dry_run_reports_candidates_without_writes(tmp_path):
    result = _invoke(tmp_path, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "review_missing" in result.output
    assert "送信・履歴保存は行いませんでした" in result.output
    assert not (tmp_path / "data").exists()


def test_before_reminder_with_no_data_has_no_candidate(tmp_path):
    result = runner.invoke(
        app,
        [
            "notifications",
            "check",
            "--date",
            DAY,
            "--time",
            "20:00",
            "--dry-run",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "送信すべき通知はありません" in result.output
    assert not (tmp_path / "data").exists()


def test_events_cover_review_instruction_overdue_main_and_minimum(tmp_path):
    before = _write_daily(tmp_path)
    values = evaluate_notifications(tmp_path, day=DAY, current=NOW)
    kinds = {item.notification_type for item in values}
    assert {
        "review_missing",
        "instruction_proposed",
        "instruction_unapproved",
        "task_overdue",
        "main_incomplete",
        "minimum_incomplete",
    } <= kinds
    assert (tmp_path / f"data/daily/{DAY}.json").read_bytes() == before


def test_confirmed_instruction_event(tmp_path):
    daily = tmp_path / "data" / "daily"
    daily.mkdir(parents=True)
    value = {
        "date": DAY,
        "raw_log": "記録済み",
        "tomorrow_plan_final": {
            "status": "approved",
            "target_date": "2026-07-16",
            "main": [],
            "tasks": [],
        },
    }
    (daily / f"{DAY}.json").write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8"
    )
    kinds = {
        item.notification_type
        for item in evaluate_notifications(tmp_path, day=DAY, current=NOW)
    }
    assert "instruction_confirmed" in kinds
    assert "review_missing" not in kinds


def test_send_history_and_deduplication(tmp_path):
    first = _invoke(tmp_path)
    second = _invoke(tmp_path)
    assert first.exit_code == second.exit_code == 0
    assert "送信: 2件" in first.output
    assert "重複スキップ: 2件" in second.output
    records = load_history(tmp_path)["records"]
    assert len(records) == 2
    assert {item["destination"] for item in records} == {"console", "file"}
    assert len(list((tmp_path / "data/notifications/events").glob("*.json"))) == 1


def test_disabled_and_old_partial_config_are_compatible(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "notifications.json").write_text(
        '{"enabled": false, "unknown": 1}', encoding="utf-8"
    )
    assert evaluate_notifications(tmp_path, day=DAY, current=NOW) == []
    (config_dir / "notifications.json").write_text(
        '{"console": {"enabled": false}}', encoding="utf-8"
    )
    config = load_notification_config(tmp_path)
    assert config["console"]["enabled"] is False
    assert config["file"]["enabled"] is True
    assert evaluate_notifications(tmp_path, day=DAY, current=NOW)


def test_invalid_config_type_and_old_naive_history_are_handled(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "notifications.json").write_text(
        '{"console": {"enabled": "yes"}}', encoding="utf-8"
    )
    invalid = _invoke(tmp_path, "--dry-run")
    assert invalid.exit_code == 3 and "trueまたはfalse" in invalid.output

    (config_dir / "notifications.json").write_text("{}", encoding="utf-8")
    history = tmp_path / "data" / "notifications" / "history.json"
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "status": "sent",
                        "sent_at": "2026-07-15T21:00:00",
                        "deduplication_key": "legacy",
                        "destination": "console",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = dispatch_notifications(tmp_path, [], current=NOW)
    assert result["failed"] == 0


def test_sender_failure_is_isolated_and_daily_data_is_unchanged(tmp_path):
    before = _write_daily(tmp_path)
    values = evaluate_notifications(tmp_path, day=DAY, current=NOW)

    class BrokenSender:
        destination = "broken"

        def send(self, _notification):
            raise RuntimeError("injected")

    result = dispatch_notifications(
        tmp_path, values[:1], current=NOW, senders=[BrokenSender()]
    )
    assert result["failed"] == 1 and result["sent"] == 0
    assert result["records"][0]["error"] == "injected"
    assert (tmp_path / f"data/daily/{DAY}.json").read_bytes() == before


def test_history_command_and_invalid_config(tmp_path):
    empty = runner.invoke(app, ["notifications", "history", "--root", str(tmp_path)])
    assert empty.exit_code == 0 and "通知履歴はありません" in empty.output
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "notifications.json").write_text(
        '{"reminder_review": {"time": "bad"}}', encoding="utf-8"
    )
    invalid = _invoke(tmp_path, "--dry-run")
    assert invalid.exit_code == 3 and "ERROR" in invalid.output
