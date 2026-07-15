from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from daily_review.integrity import (
    apply_integrity_repair,
    preview_integrity_repair,
    run_integrity_check,
)
from daily_review.migration import (
    RECOVERY_MIGRATION_ID,
    apply_migration,
    load_migration_history,
)
from daily_review.operation_lock import OperationLockedError, WorkspaceLock
from daily_review.storage import atomic_write_json_data


def _write(root, relative: str, value) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_integrity_codes_severity_and_safe_repair_with_backup(tmp_path):
    _write(
        tmp_path,
        "data/daily/2026-07-15.json",
        {
            "date": "2026-07-15",
            "created_at": "2026-07-15T20:00:00+09:00",
            "tomorrow_plan_proposal": {
                "target_date": "2026-07-16",
                "main": ["a", "b", "c", "d"],
                "tasks": [],
            },
        },
    )
    _write(
        tmp_path,
        "data/api/tasks.json",
        {
            "version": "1",
            "tasks": [
                {
                    "id": "t1",
                    "title": "task",
                    "status": "pending",
                    "priority": "high",
                    "due_date": "2026-07-16",
                    "rollover_count": -1,
                }
            ],
        },
    )
    report = run_integrity_check(tmp_path)
    codes = {item["code"] for item in report["issues"]}
    assert {
        "MISSING_RAW_LOG",
        "MISSING_UPDATED_AT",
        "MAIN_LIMIT_EXCEEDED",
        "INVALID_ROLLOVER_COUNT",
        "MISSING_ORIGINAL_DUE_DATE",
    } <= codes
    assert all(
        item["severity"] in {"info", "warning", "error", "critical"}
        for item in report["issues"]
    )
    preview = preview_integrity_repair(tmp_path)
    assert preview["fix_count"] >= 4
    assert any(item["code"] == "MISSING_RAW_LOG" for item in preview["manual_review"])

    result = apply_integrity_repair(tmp_path, idempotency_key="repair-request")
    assert result["fixed_count"] >= 4
    assert result["backup_path"]
    daily = json.loads(
        (tmp_path / "data/daily/2026-07-15.json").read_text(encoding="utf-8")
    )
    assert daily["tomorrow_plan_proposal"]["main"] == ["a", "b", "c"]
    assert daily["tomorrow_plan_proposal"]["optional"] == ["d"]
    assert "raw_log" not in daily
    task = json.loads((tmp_path / "data/api/tasks.json").read_text(encoding="utf-8"))[
        "tasks"
    ][0]
    assert task["rollover_count"] == 0
    assert task["original_due_date"] == "2026-07-16"
    replay = apply_integrity_repair(tmp_path, idempotency_key="repair-request")
    assert replay["status"] == "idempotent_replay"
    assert replay["repair_id"] == result["repair_id"]


def test_integrity_detects_corrupt_json_and_lock_blocks_normal_writes(tmp_path):
    bad = tmp_path / "data/daily/bad.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{", encoding="utf-8")
    report = run_integrity_check(tmp_path)
    assert any(
        item["code"] == "INVALID_JSON" and item["severity"] == "critical"
        for item in report["issues"]
    )

    target = tmp_path / "data/api/value.json"
    with WorkspaceLock(tmp_path, "test-operation"):
        with pytest.raises(OperationLockedError):
            # Simulate a second process by clearing only the in-process ownership marker.
            from daily_review import operation_lock

            operation_lock._HELD_ROOTS.clear()
            atomic_write_json_data(target, {"x": 1})
            operation_lock._HELD_ROOTS.add(tmp_path.resolve())
    assert not (tmp_path / ".daily-review-operation.lock").exists()


def test_stale_lock_recovers_and_exception_always_releases_lock(tmp_path):
    lock = tmp_path / ".daily-review-operation.lock"
    lock.mkdir()
    acquired = datetime.now(ZoneInfo("Asia/Tokyo")) - timedelta(hours=3)
    (lock / "owner.json").write_text(
        json.dumps({"operation": "abandoned", "acquired_at": acquired.isoformat()}),
        encoding="utf-8",
    )
    with WorkspaceLock(tmp_path, "recovered"):
        owner = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
        assert owner["operation"] == "recovered"
    assert not lock.exists()

    with pytest.raises(RuntimeError, match="simulated"):
        with WorkspaceLock(tmp_path, "failing"):
            raise RuntimeError("simulated operation failure")
    assert not lock.exists()


def test_recovery_migration_creates_only_missing_paths_and_preserves_old_data(tmp_path):
    old = {
        "version": "0",
        "tasks": [{"id": "old", "title": "旧タスク", "unknown": {"keep": True}}],
    }
    _write(tmp_path, "data/api/tasks.json", old)
    before = (tmp_path / "data/api/tasks.json").read_bytes()
    result = apply_migration(tmp_path)
    assert any("data/restore" in item for item in result["changes"])
    assert (tmp_path / "data/api/tasks.json").read_bytes() == before
    history = load_migration_history(tmp_path)
    assert RECOVERY_MIGRATION_ID in {item["id"] for item in history["migrations"]}
