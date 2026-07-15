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
    Path("data/plans"), Path("data/plans/weekly"), Path("data/plans/daily"), Path("data/backups/plans"),
)
EVALUATION_MIGRATION_DIRECTORIES = (
    Path("data/evaluations"), Path("data/evaluations/weekly"), Path("data/evaluations/monthly"),
    Path("data/backups/evaluations"), Path("data/replans"), Path("data/backups/replans"), Path("data/tmp"),
    Path("data/goal-designs"), Path("data/backups/goal-designs"), Path("data/transactions"),
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
    applied = {item.get("id") for item in load_migration_history(root)["migrations"] if isinstance(item, dict)}
    return {MIGRATION_ID, GOALS_MIGRATION_ID, ROADMAP_MIGRATION_ID, PLANNING_MIGRATION_ID, EVALUATION_MIGRATION_ID, FINAL_MIGRATION_ID} <= applied


def migration_plan(root: Path) -> list[dict[str, str]]:
    """Return only missing, safe-to-create v1.1 workspace items."""
    plan: list[dict[str, str]] = []
    for relative in MIGRATION_DIRECTORIES:
        plan.append({"path": str(relative), "action": "existing" if (root / relative).exists() else "create"})
    for relative in GOALS_MIGRATION_DIRECTORIES:
        plan.append({"path": str(relative), "action": "existing" if (root / relative).exists() else "create"})
    for relative in PLANNING_MIGRATION_DIRECTORIES:
        plan.append({"path": str(relative), "action": "existing" if (root / relative).exists() else "create"})
    for relative in EVALUATION_MIGRATION_DIRECTORIES:
        plan.append({"path": str(relative), "action": "existing" if (root / relative).exists() else "create"})
    priorities = priorities_path(root)
    plan.append({
        "path": "config/priorities.json",
        "action": "existing" if priorities.exists() else "create",
    })
    prompt = root / "templates" / "chat_import_prompt.md"
    plan.append({
        "path": "templates/chat_import_prompt.md",
        "action": "existing" if prompt.exists() else "create",
    })
    return plan


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
            target.write_text(TEMPLATE_CONTENTS["chat_import_prompt.md"], encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
        changes.append(f"create {item['path']}")

    applied = {item.get("id") for item in history["migrations"] if isinstance(item, dict)}
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
    for record in new_records:
        history["migrations"].append({
            **record, "applied_at": now_iso(), "to_version": __version__, "changes": list(changes),
        })
    if new_records:
        atomic_write_json_data(migration_history_path(root), history)
    return {"already_migrated": not changes and not new_records, "changes": changes, "skipped": [item for item in plan if item["action"] == "existing"]}
