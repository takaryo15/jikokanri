from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_review.command_api import CommandExecutor


DAY = "2026-07-15"
NOW = datetime(2026, 7, 15, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def _execute(root, key, commands, *, mode="preview", token=None, policy="atomic"):
    return CommandExecutor(root, clock=lambda: NOW).execute(
        {
            "version": "1",
            "request_id": f"req-{key}",
            "idempotency_key": key,
            "mode": mode,
            "effective_date": DAY,
            "source": "test",
            "execution_policy": policy,
            "confirmation_token": token,
            "commands": commands,
        }
    )


def _commit(root, key, commands):
    preview = _execute(root, key, commands)
    assert preview.status == "preview_ready", preview.model_dump()
    commit = _execute(
        root, key, commands, mode="commit", token=preview.confirmation_token
    )
    assert commit.status == "committed", commit.model_dump()
    return commit


def test_task_create_update_reschedule_complete_and_list(tmp_path):
    create = _commit(
        tmp_path,
        "task-flow",
        [
            {
                "type": "create_task",
                "payload": {
                    "title": "院試過去問",
                    "category": "exam",
                    "priority": "high",
                    "due_date": DAY,
                    "is_main_candidate": True,
                    "minimum_action": "1問読む",
                },
            },
            {
                "type": "update_task",
                "payload": {
                    "task_id": "$commands.0.result.task_id",
                    "priority": "medium",
                    "category": "study",
                },
            },
            {
                "type": "reschedule_task",
                "payload": {
                    "task_id": "$commands.0.result.task_id",
                    "new_due_date": "2026-07-16",
                    "reason": "体調",
                },
            },
            {
                "type": "complete_task",
                "payload": {"task_id": "$commands.0.result.task_id"},
            },
        ],
    )
    task_id = create.result["commands"][0]["result"]["task_id"]
    read = CommandExecutor(tmp_path, clock=lambda: NOW).execute(
        {
            "version": "1",
            "effective_date": DAY,
            "commands": [{"type": "list_tasks", "payload": {"status": "completed"}}],
        }
    )
    task = read.result["commands"][0]["result"]["tasks"][0]
    assert task["id"] == task_id
    assert (task["priority"], task["category"], task["due_date"], task["status"]) == (
        "medium",
        "study",
        "2026-07-16",
        "completed",
    )


def test_task_title_unique_ambiguous_none_and_already_completed(tmp_path):
    _commit(
        tmp_path,
        "seed",
        [
            {
                "type": "create_task",
                "payload": {"title": "院試 2024", "category": "exam"},
            },
            {
                "type": "create_task",
                "payload": {"title": "院試 2023", "category": "exam"},
            },
            {
                "type": "create_task",
                "payload": {"title": "筋トレ", "category": "health"},
            },
        ],
    )
    ambiguous = _execute(
        tmp_path, "ambiguous", [{"type": "complete_task", "payload": {"title": "院試"}}]
    )
    missing = _execute(
        tmp_path, "missing", [{"type": "complete_task", "payload": {"title": "読書"}}]
    )
    assert (
        ambiguous.errors[0].code == "TASK_AMBIGUOUS"
        and len(ambiguous.errors[0].details["candidates"]) == 2
    )
    assert missing.errors[0].code == "TASK_NOT_FOUND"
    first = _commit(
        tmp_path,
        "complete",
        [{"type": "complete_task", "payload": {"title": "筋トレ"}}],
    )
    assert first.status == "committed"
    already = _execute(
        tmp_path, "already", [{"type": "complete_task", "payload": {"title": "筋トレ"}}]
    )
    assert already.warnings[0].code == "TASK_ALREADY_COMPLETED"


def test_instruction_generate_revise_main_overflow_approve_get(tmp_path):
    seed_commands = [
        {
            "type": "create_task",
            "payload": {
                "title": title,
                "priority": priority,
                "due_date": "2026-07-16",
                "is_main_candidate": main,
                "minimum_action": "着手",
            },
        }
        for title, priority, main in (
            ("A", "high", True),
            ("B", "medium", False),
            ("C", "low", False),
            ("D", "low", False),
        )
    ]
    _commit(tmp_path, "instruction-seed", seed_commands)
    generated = _commit(
        tmp_path,
        "generate",
        [{"type": "generate_instruction", "payload": {"target_date": "2026-07-16"}}],
    )
    instruction_id = generated.result["commands"][0]["result"]["instruction_id"]
    assert len(generated.result["commands"][0]["result"]["instruction"]["main"]) == 3

    revised = _commit(
        tmp_path,
        "revise",
        [
            {
                "type": "revise_instruction",
                "payload": {
                    "instruction_id": instruction_id,
                    "main": ["A", "B", "C", "D"],
                    "minimum": ["1分"],
                    "optional": ["E"],
                },
            }
        ],
    )
    instruction = revised.result["commands"][0]["result"]["instruction"]
    assert instruction["main"] == ["A", "B", "C"]
    assert instruction["optional"] == ["E", "D"]
    assert revised.warnings[0].code == "MAIN_LIMIT_EXCEEDED"

    approved = _commit(
        tmp_path,
        "approve",
        [
            {
                "type": "approve_instruction",
                "payload": {"instruction_id": instruction_id},
            }
        ],
    )
    assert approved.result["commands"][0]["result"]["status"] == "approved"
    read = CommandExecutor(tmp_path, clock=lambda: NOW).execute(
        {
            "version": "1",
            "effective_date": DAY,
            "commands": [
                {"type": "get_instruction", "payload": {"target_date": "2026-07-16"}}
            ],
        }
    )
    assert read.result["commands"][0]["result"]["kind"] == "tomorrow_plan_final"
    double = _execute(
        tmp_path,
        "approve-again",
        [
            {
                "type": "approve_instruction",
                "payload": {"instruction_id": instruction_id},
            }
        ],
    )
    assert double.errors[0].code == "INSTRUCTION_ALREADY_APPROVED"


def test_legacy_daily_task_can_be_completed_without_schema_conversion(tmp_path):
    daily = tmp_path / "data/daily"
    daily.mkdir(parents=True)
    entry = {
        "date": "2026-07-14",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "unknown": {"keep": True},
        "tomorrow_plan_final": {
            "status": "approved",
            "target_date": DAY,
            "main": ["exam"],
            "tasks": [
                {
                    "id": "legacy-task",
                    "area": "exam",
                    "task": "古いタスク",
                    "priority": 1,
                    "minimum_line": "開く",
                }
            ],
            "one_change_tomorrow": "続ける",
            "approved_at": NOW.isoformat(),
        },
    }
    (daily / "2026-07-14.json").write_text(
        json.dumps(entry, ensure_ascii=False), encoding="utf-8"
    )
    _commit(
        tmp_path,
        "legacy-complete",
        [{"type": "complete_task", "payload": {"task_id": "legacy-task"}}],
    )
    stored = json.loads((daily / "2026-07-14.json").read_text(encoding="utf-8"))
    assert stored["unknown"] == {"keep": True}
    assert stored["task_results"][0]["status"] == "completed"
