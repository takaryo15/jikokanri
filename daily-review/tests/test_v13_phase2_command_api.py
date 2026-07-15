from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from daily_review.command_api import CommandExecutor, request_hash
from daily_review.command_models import CommandRequest
import daily_review.storage as storage_module


DAY = "2026-07-15"
NOW = datetime(2026, 7, 15, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def _request(**overrides):
    value = {
        "version": "1",
        "request_id": "req_test_001",
        "idempotency_key": "review-2026-07-15",
        "mode": "preview",
        "timezone": "Asia/Tokyo",
        "effective_date": DAY,
        "source": "test",
        "raw_input": "今日の原文\n改行を保持",
        "commands": [
            {
                "type": "create_daily_review",
                "payload": {
                    "date": DAY,
                    "done": ["開発"],
                    "not_done": ["院試"],
                    "causes": ["眠気"],
                    "tomorrow": ["A", "B", "C", "D"],
                    "minimum": ["1問"],
                    "journal": "日記",
                },
            }
        ],
    }
    value.update(overrides)
    return value


def test_request_response_preview_commit_and_idempotent_replay(tmp_path):
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    preview = executor.execute(_request())
    assert preview.status == "preview_ready"
    assert preview.confirmation_required and preview.confirmation_token
    assert preview.changes[0]["main"] == ["A", "B", "C"]
    assert preview.changes[0]["backlog"] == ["D"]
    assert not (tmp_path / f"data/daily/{DAY}.json").exists()
    assert not (tmp_path / f"data/inbox/{DAY}.json").exists()

    commit_payload = _request(
        mode="commit", confirmation_token=preview.confirmation_token
    )
    committed = executor.execute(commit_payload)
    assert committed.status == "committed"
    entry = json.loads(
        (tmp_path / f"data/daily/{DAY}.json").read_text(encoding="utf-8")
    )
    inbox = json.loads(
        (tmp_path / f"data/inbox/{DAY}.json").read_text(encoding="utf-8")
    )
    assert entry["raw_log"] == "今日の原文\n改行を保持"
    assert entry.get("tomorrow_plan_final") is None
    assert inbox["entries"][0]["raw_text"] == "今日の原文\n改行を保持"
    assert (tmp_path / f"logs/{DAY}.md").is_file()

    before = (tmp_path / f"data/daily/{DAY}.json").read_bytes()
    replay = executor.execute(commit_payload)
    assert replay.status == "idempotent_replay"
    assert (tmp_path / f"data/daily/{DAY}.json").read_bytes() == before


def test_request_validation_version_hash_and_idempotency_conflict(tmp_path):
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    assert (
        executor.execute({"version": "2", "request_id": "x"}).errors[0].code
        == "UNSUPPORTED_VERSION"
    )
    invalid = executor.execute(
        {"version": "1", "effective_date": "bad", "commands": []}
    )
    assert invalid.status == "input_error" and invalid.errors
    first = executor.execute(_request())
    conflict = executor.execute(_request(raw_input="変更した原文"))
    assert first.status == "preview_ready"
    assert conflict.errors[0].code == "IDEMPOTENCY_CONFLICT"
    request1 = CommandRequest.model_validate(_request())
    request2 = request1.model_copy(update={"request_id": "different", "mode": "commit"})
    assert request_hash(request1) == request_hash(request2)


def test_confirmation_required_invalid_expired_and_stale(tmp_path):
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    missing = executor.execute(_request(mode="commit"))
    assert missing.errors[0].code == "CONFIRMATION_REQUIRED"

    preview = executor.execute(_request())
    invalid = executor.execute(
        _request(mode="commit", confirmation_token="confirm_" + "0" * 32)
    )
    assert invalid.errors[0].code == "CONFIRMATION_INVALID"

    expired_executor = CommandExecutor(
        tmp_path, clock=lambda: NOW + timedelta(minutes=31)
    )
    expired = expired_executor.execute(
        _request(mode="commit", confirmation_token=preview.confirmation_token)
    )
    assert expired.errors[0].code == "CONFIRMATION_EXPIRED"

    other_root = tmp_path / "stale"
    stale_executor = CommandExecutor(other_root, clock=lambda: NOW)
    stale_preview = stale_executor.execute(_request(idempotency_key="stale"))
    daily = other_root / "data/daily"
    daily.mkdir(parents=True)
    (daily / "2026-07-01.json").write_text(
        json.dumps(
            {
                "date": "2026-07-01",
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    stale = stale_executor.execute(
        _request(
            idempotency_key="stale",
            mode="commit",
            confirmation_token=stale_preview.confirmation_token,
        )
    )
    assert stale.errors[0].code == "PREVIEW_STALE"


def test_atomic_failure_and_best_effort(tmp_path):
    commands = [
        {"type": "create_task", "payload": {"title": "作成", "due_date": DAY}},
        {"type": "complete_task", "payload": {"task_id": "missing"}},
    ]
    atomic = CommandExecutor(tmp_path / "atomic", clock=lambda: NOW).execute(
        _request(idempotency_key="atomic", commands=commands, raw_input=None)
    )
    assert atomic.status == "needs_clarification"
    assert not (tmp_path / "atomic/data/api/tasks.json").exists()

    best = CommandExecutor(tmp_path / "best", clock=lambda: NOW).execute(
        _request(
            idempotency_key="best",
            commands=commands,
            raw_input=None,
            execution_policy="best_effort",
        )
    )
    assert best.status == "preview_ready" and best.errors
    commit = CommandExecutor(tmp_path / "best", clock=lambda: NOW).execute(
        _request(
            idempotency_key="best",
            commands=commands,
            raw_input=None,
            execution_policy="best_effort",
            mode="commit",
            confirmation_token=best.confirmation_token,
        )
    )
    assert commit.status == "partial_success"
    tasks = json.loads(
        (tmp_path / "best/data/api/tasks.json").read_text(encoding="utf-8")
    )["tasks"]
    assert [item["title"] for item in tasks] == ["作成"]


def test_stale_preview_can_be_refreshed_with_same_key(tmp_path):
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    first = executor.execute(_request(idempotency_key="refresh"))
    daily = tmp_path / "data/daily"
    daily.mkdir(parents=True)
    (daily / "2026-07-01.json").write_text(
        json.dumps(
            {
                "date": "2026-07-01",
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    refreshed = executor.execute(_request(idempotency_key="refresh"))
    assert refreshed.status == "preview_ready"
    assert refreshed.confirmation_token != first.confirmation_token


def test_commit_write_failure_rolls_back_major_data(tmp_path, monkeypatch):
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    preview = executor.execute(_request(idempotency_key="rollback"))
    original_replace = storage_module.os.replace
    calls = 0

    def fail_once(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        return original_replace(source, target)

    monkeypatch.setattr(storage_module.os, "replace", fail_once)
    result = executor.execute(
        _request(
            idempotency_key="rollback",
            mode="commit",
            confirmation_token=preview.confirmation_token,
        )
    )
    assert result.errors[0].code == "STORAGE_ERROR"
    assert not (tmp_path / f"data/daily/{DAY}.json").exists()
    assert not (tmp_path / f"data/inbox/{DAY}.json").exists()


def test_unknown_command_invalid_payload_zero_and_max_commands(tmp_path):
    executor = CommandExecutor(tmp_path, clock=lambda: NOW)
    unknown = executor.execute(
        {
            "version": "1",
            "effective_date": DAY,
            "commands": [{"type": "delete_everything", "payload": {}}],
        }
    )
    invalid = executor.execute(
        {
            "version": "1",
            "effective_date": DAY,
            "commands": [{"type": "create_task", "payload": {"title": []}}],
        }
    )
    empty = executor.execute({"version": "1", "effective_date": DAY, "commands": []})
    maximum = executor.execute(
        {
            "version": "1",
            "effective_date": DAY,
            "commands": [{"type": "list_tasks", "payload": {}} for _ in range(20)],
        }
    )
    too_many = executor.execute(
        {
            "version": "1",
            "effective_date": DAY,
            "commands": [{"type": "list_tasks", "payload": {}} for _ in range(21)],
        }
    )
    assert unknown.errors[0].code == "UNKNOWN_COMMAND"
    assert invalid.errors[0].code == "INVALID_PAYLOAD"
    assert empty.status == maximum.status == "success"
    assert too_many.errors[0].code == "INVALID_REQUEST"


def test_old_missing_and_invalid_api_config(tmp_path):
    assert (
        CommandExecutor(tmp_path / "old", clock=lambda: NOW)
        .execute({"version": "1", "effective_date": DAY, "commands": []})
        .status
        == "success"
    )
    config = tmp_path / "bad/config"
    config.mkdir(parents=True)
    (config / "api.json").write_text(
        '{"confirmation_ttl_minutes": 0}', encoding="utf-8"
    )
    response = CommandExecutor(tmp_path / "bad", clock=lambda: NOW).execute(
        {"version": "1", "effective_date": DAY, "commands": []}
    )
    assert response.errors[0].code == "INVALID_REQUEST"
