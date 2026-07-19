from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_review.command_api import CommandExecutor
from daily_review.scheduler import DEFAULT_SCHEDULER_CONFIG


NOW = datetime(2026, 7, 20, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def _config(root):
    value = json.loads(json.dumps(DEFAULT_SCHEDULER_CONFIG))
    value["enabled"] = True
    for job_id, job in value["jobs"].items():
        job["enabled"] = job_id == "review_reminder"
    path = root / "config/scheduler.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _payload(command, mode="preview", token=None):
    return {
        "version": "1",
        "request_id": "phase5-command-api",
        "idempotency_key": "phase5-run-due",
        "mode": mode,
        "timezone": "Asia/Tokyo",
        "effective_date": "2026-07-20",
        "source": "test",
        "commands": [command],
        "confirmation_token": token,
    }


def test_read_only_scheduler_commands_need_no_confirmation(tmp_path):
    _config(tmp_path)
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    for command in (
        {"type": "scheduler_status", "payload": {"at": NOW.isoformat()}},
        {"type": "scheduler_due", "payload": {"at": NOW.isoformat()}},
        {"type": "scheduler_history", "payload": {}},
    ):
        payload = _payload(command)
        payload["idempotency_key"] = None
        result = executor.execute(payload)
        assert result.status == "success"
        assert result.confirmation_required is False


def test_scheduler_run_due_uses_preview_commit_and_idempotency(tmp_path):
    _config(tmp_path)
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    command = {"type": "scheduler_run_due", "payload": {"at": NOW.isoformat()}}
    preview = executor.execute(_payload(command))
    assert preview.status == "preview_ready"
    assert not (tmp_path / "data/scheduler/history.json").exists()
    committed = executor.execute(
        _payload(command, mode="commit", token=preview.confirmation_token)
    )
    assert committed.status == "committed"
    assert (tmp_path / "data/scheduler/history.json").is_file()
    replay = executor.execute(_payload(command, mode="commit"))
    assert replay.status == "idempotent_replay"


def test_morning_flow_command_is_confirmation_protected(tmp_path):
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    payload = _payload(
        {
            "type": "run_morning_flow",
            "payload": {"date": "2026-07-20", "at": NOW.isoformat()},
        }
    )
    payload["idempotency_key"] = "morning-flow"
    preview = executor.execute(payload)
    assert preview.status == "preview_ready"
    assert preview.result["commands"][0]["result"]["dry_run"] is True
