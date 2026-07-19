"""Safe, idempotent workspace migration for the v1.1 workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .models import now_iso
from .storage import (
    DEFAULT_PRIORITIES,
    TEMPLATE_CONTENTS,
    atomic_write_json_data,
    priorities_path,
    read_json_file,
)


MIGRATION_ID = "v1.1-base"
GOALS_MIGRATION_ID = "v1.2-goals-base"
ROADMAP_MIGRATION_ID = "v1.2-goal-roadmap"
PLANNING_MIGRATION_ID = "v1.2-goal-planning"
EVALUATION_MIGRATION_ID = "v1.2-goal-evaluation-rc1"
FINAL_MIGRATION_ID = "v1.2-final"
RECOVERY_MIGRATION_ID = "v1.3-recovery-base"
V13_FINAL_MIGRATION_ID = "v1.3-final"
TARGET_SCHEMA_VERSION = "1.3"
MIGRATION_HISTORY_PATH = Path("data/migrations.json")
MIGRATION_DIRECTORIES = (
    Path("data/inbox"),
    Path("data/drafts"),
    Path("data/sessions"),
    Path("data/handoffs"),
    Path("data/backups"),
    Path("data/backups/daily"),
    Path("data/backups/drafts"),
    Path("config"),
)
GOALS_MIGRATION_DIRECTORIES = (
    Path("data/goals"),
    Path("data/goals/items"),
    Path("data/backups/goals"),
)
PLANNING_MIGRATION_DIRECTORIES = (
    Path("data/plans"),
    Path("data/plans/weekly"),
    Path("data/plans/daily"),
    Path("data/backups/plans"),
)
EVALUATION_MIGRATION_DIRECTORIES = (
    Path("data/evaluations"),
    Path("data/evaluations/weekly"),
    Path("data/evaluations/monthly"),
    Path("data/backups/evaluations"),
    Path("data/replans"),
    Path("data/backups/replans"),
    Path("data/tmp"),
    Path("data/goal-designs"),
    Path("data/backups/goal-designs"),
    Path("data/transactions"),
)
RECOVERY_MIGRATION_DIRECTORIES = (
    Path("data/api"),
    Path("data/api/audit"),
    Path("data/api/confirmations"),
    Path("data/api/idempotency"),
    Path("data/backup"),
    Path("data/backup/idempotency"),
    Path("data/restore"),
    Path("data/restore/idempotency"),
    Path("data/rollover"),
    Path("data/rollover/idempotency"),
    Path("data/repairs"),
    Path("data/repairs/idempotency"),
    Path("data/notifications"),
    Path("data/notifications/events"),
    Path("data/scheduler"),
    Path("data/scheduler/audit"),
    Path("data/scheduler/idempotency"),
    Path("data/scheduler/locks"),
)


def migration_history_path(root: Path) -> Path:
    return root / MIGRATION_HISTORY_PATH


def load_migration_history(root: Path) -> dict[str, Any]:
    path = migration_history_path(root)
    if not path.exists():
        return {"migrations": []}
    value = read_json_file(path)
    if not isinstance(value, dict) or not isinstance(value.get("migrations"), list):
        raise ValueError("migration履歴の形式が不正です")
    return value


def is_migrated(root: Path) -> bool:
    applied = {
        item.get("id")
        for item in load_migration_history(root)["migrations"]
        if isinstance(item, dict)
    }
    return {
        MIGRATION_ID,
        GOALS_MIGRATION_ID,
        ROADMAP_MIGRATION_ID,
        PLANNING_MIGRATION_ID,
        EVALUATION_MIGRATION_ID,
        FINAL_MIGRATION_ID,
        RECOVERY_MIGRATION_ID,
        V13_FINAL_MIGRATION_ID,
    } <= applied


def current_schema_version(root: Path) -> str:
    """Infer the newest applied schema without rewriting legacy data."""
    if not migration_history_path(root).exists():
        return "1.0"
    applied = {
        item.get("id")
        for item in load_migration_history(root)["migrations"]
        if isinstance(item, dict)
    }
    if V13_FINAL_MIGRATION_ID in applied:
        return TARGET_SCHEMA_VERSION
    if RECOVERY_MIGRATION_ID in applied:
        return "1.3-recovery"
    if FINAL_MIGRATION_ID in applied:
        return "1.2"
    if MIGRATION_ID in applied:
        return "1.1"
    return "1.0"


def migration_plan(root: Path) -> list[dict[str, str]]:
    """Return only missing, safe-to-create v1.1 workspace items."""
    plan: list[dict[str, str]] = []
    for relative in MIGRATION_DIRECTORIES:
        plan.append(
            {
                "path": str(relative),
                "action": "existing" if (root / relative).exists() else "create",
            }
        )
    for relative in GOALS_MIGRATION_DIRECTORIES:
        plan.append(
            {
                "path": str(relative),
                "action": "existing" if (root / relative).exists() else "create",
            }
        )
    for relative in PLANNING_MIGRATION_DIRECTORIES:
        plan.append(
            {
                "path": str(relative),
                "action": "existing" if (root / relative).exists() else "create",
            }
        )
    for relative in EVALUATION_MIGRATION_DIRECTORIES:
        plan.append(
            {
                "path": str(relative),
                "action": "existing" if (root / relative).exists() else "create",
            }
        )
    for relative in RECOVERY_MIGRATION_DIRECTORIES:
        plan.append(
            {
                "path": str(relative),
                "action": "existing" if (root / relative).exists() else "create",
            }
        )
    priorities = priorities_path(root)
    plan.append(
        {
            "path": "config/priorities.json",
            "action": "existing" if priorities.exists() else "create",
        }
    )
    prompt = root / "templates" / "chat_import_prompt.md"
    plan.append(
        {
            "path": "templates/chat_import_prompt.md",
            "action": "existing" if prompt.exists() else "create",
        }
    )
    return plan


def migration_check(root: Path) -> dict[str, Any]:
    plan = migration_plan(root)
    targets = [item for item in plan if item["action"] == "create"]
    return {
        "source_schema_version": current_schema_version(root),
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "target_app_version": "1.3.0",
        "already_migrated": is_migrated(root),
        "target_files": [item["path"] for item in targets],
        "changes": [
            "不足ディレクトリと配布テンプレートだけを作成",
            "migration履歴へv1.3-finalを追記",
        ],
        "existing_daily_weekly_monthly_rewritten": False,
        "backup_required": True,
        "compatibility_issues": [],
        "manual_checks": [
            "migration前バックアップを検証する",
            "migration後にdaily-review doctorを実行する",
        ],
        "plan": plan,
    }


def apply_migration(root: Path) -> dict[str, Any]:
    """Create missing v1.1/v1.2 support files without changing user data."""
    history = load_migration_history(root)
    plan = migration_plan(root)
    changes: list[str] = []
    for item in plan:
        relative = Path(item["path"])
        target = root / relative
        if item["action"] != "create":
            continue
        if relative == Path("config/priorities.json"):
            atomic_write_json_data(target, DEFAULT_PRIORITIES)
        elif relative == Path("templates/chat_import_prompt.md"):
            # The file is created only when absent; the normal init flow uses
            # the same repository-managed content.
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                TEMPLATE_CONTENTS["chat_import_prompt.md"], encoding="utf-8"
            )
        else:
            target.mkdir(parents=True, exist_ok=True)
        changes.append(f"create {item['path']}")

    applied = {
        item.get("id") for item in history["migrations"] if isinstance(item, dict)
    }
    new_records = []
    if MIGRATION_ID not in applied:
        new_records.append({"id": MIGRATION_ID, "from_version": "1.0.0"})
    if GOALS_MIGRATION_ID not in applied:
        new_records.append({"id": GOALS_MIGRATION_ID, "from_version": "1.1.0"})
    # Roadmap fields are intentionally optional.  Recording this migration
    # must never rewrite existing goal JSON just to add an empty list.
    if ROADMAP_MIGRATION_ID not in applied:
        new_records.append({"id": ROADMAP_MIGRATION_ID, "from_version": "1.1.0"})
    if PLANNING_MIGRATION_ID not in applied:
        new_records.append({"id": PLANNING_MIGRATION_ID, "from_version": "1.1.0"})
    if EVALUATION_MIGRATION_ID not in applied:
        new_records.append({"id": EVALUATION_MIGRATION_ID, "from_version": "1.1.0"})
    if FINAL_MIGRATION_ID not in applied:
        new_records.append({"id": FINAL_MIGRATION_ID, "from_version": "1.2.0rc1"})
    if RECOVERY_MIGRATION_ID not in applied:
        new_records.append({"id": RECOVERY_MIGRATION_ID, "from_version": "1.2.0"})
    if V13_FINAL_MIGRATION_ID not in applied:
        new_records.append(
            {
                "id": V13_FINAL_MIGRATION_ID,
                "from_version": current_schema_version(root),
            }
        )
    for record in new_records:
        history["migrations"].append(
            {
                **record,
                "applied_at": now_iso(),
                "to_version": __version__,
                "changes": list(changes),
            }
        )
    if new_records:
        atomic_write_json_data(migration_history_path(root), history)
    return {
        "already_migrated": not changes and not new_records,
        "changes": changes,
        "skipped": [item for item in plan if item["action"] == "existing"],
    }
