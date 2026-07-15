from __future__ import annotations

import json

from daily_review.rollover import apply_rollover, preview_rollover, rollover_history


def _tasks(root, tasks):
    path = root / "data" / "api" / "tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": "1", "tasks": tasks}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_rollover_preview_filters_warns_limits_main_and_preserves_data(tmp_path):
    _tasks(
        tmp_path,
        [
            {
                "id": "t1",
                "title": "院試",
                "status": "pending",
                "priority": "high",
                "category": "院試",
                "due_date": "2026-07-15",
                "minimum_line": "1問",
                "source_review_date": "2026-07-15",
                "rollover_count": 2,
            },
            {
                "id": "t2",
                "title": "研究",
                "status": "pending",
                "priority": "high",
                "category": "研究",
                "due_date": "2026-07-15",
                "minimum_line": "開く",
                "source_review_date": "2026-07-15",
                "rollover_count": 4,
            },
            {
                "id": "t3",
                "title": "筋トレ",
                "status": "pending",
                "priority": "medium",
                "category": "筋トレ",
                "due_date": "2026-07-15",
                "minimum_line": "1回",
                "source_review_date": "2026-07-15",
            },
            {
                "id": "t4",
                "title": "読書",
                "status": "pending",
                "priority": "low",
                "category": "読書",
                "due_date": "2026-07-15",
                "minimum_line": "1頁",
                "source_review_date": "2026-07-15",
            },
            {
                "id": "done",
                "title": "完了",
                "status": "completed",
                "priority": "high",
                "completed_at": "2026-07-15T10:00:00+09:00",
                "due_date": "2026-07-15",
                "source_review_date": "2026-07-15",
            },
            {
                "id": "cancel",
                "title": "中止",
                "status": "cancelled",
                "priority": "high",
                "due_date": "2026-07-15",
                "source_review_date": "2026-07-15",
            },
            {
                "id": "never",
                "title": "禁止",
                "status": "pending",
                "priority": "high",
                "due_date": "2026-07-15",
                "source_review_date": "2026-07-15",
                "rollover_policy": "never",
            },
        ],
    )
    before = (tmp_path / "data/api/tasks.json").read_bytes()
    preview = preview_rollover(tmp_path, "2026-07-16", idempotency_key="roll-one")
    assert len(preview["main_task_ids"]) == 3
    assert len(preview["optional_task_ids"]) == 1
    decisions = {item["task_id"]: item for item in preview["candidates"]}
    assert decisions["t1"]["rollover_count_after"] == 3
    assert decisions["t1"]["minimum_is_suggestion"] is True
    assert decisions["t2"]["decision"] == "split_suggested"
    assert {item["task_id"] for item in preview["ignored"]} >= {
        "done",
        "cancel",
        "never",
    }
    assert (tmp_path / "data/api/tasks.json").read_bytes() == before


def test_rollover_apply_is_idempotent_does_not_duplicate_and_keeps_due_date(tmp_path):
    _tasks(
        tmp_path,
        [
            {
                "id": "t1",
                "title": "院試",
                "status": "pending",
                "priority": "high",
                "category": "院試",
                "due_date": "2026-07-15",
                "minimum_line": "1問",
                "source_review_date": "2026-07-15",
            }
        ],
    )
    preview = preview_rollover(tmp_path, "2026-07-16", idempotency_key="same-day")
    result = apply_rollover(
        tmp_path,
        "2026-07-16",
        confirmation_token=preview["confirmation_token"],
        idempotency_key="same-day",
    )
    replay = apply_rollover(
        tmp_path,
        "2026-07-16",
        confirmation_token=preview["confirmation_token"],
        idempotency_key="same-day",
    )
    assert result["applied_count"] == 1
    assert replay["task_ids"] == ["t1"]
    payload = json.loads((tmp_path / "data/api/tasks.json").read_text(encoding="utf-8"))
    assert len(payload["tasks"]) == 1
    task = payload["tasks"][0]
    assert task["due_date"] == "2026-07-15"
    assert task["original_due_date"] == "2026-07-15"
    assert task["planned_date"] == "2026-07-16"
    assert task["rollover_count"] == 1
    assert len(rollover_history(tmp_path)) == 1
