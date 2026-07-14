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
    return any(
        isinstance(item, dict) and item.get("id") == MIGRATION_ID
        for item in load_migration_history(root)["migrations"]
    )


def migration_plan(root: Path) -> list[dict[str, str]]:
    """Return only missing, safe-to-create v1.1 workspace items."""
    plan: list[dict[str, str]] = []
    for relative in MIGRATION_DIRECTORIES:
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
    """Create missing v1.1 support files without changing existing user data."""
    history = load_migration_history(root)
    if any(isinstance(item, dict) and item.get("id") == MIGRATION_ID for item in history["migrations"]):
        return {"already_migrated": True, "changes": [], "skipped": migration_plan(root)}

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

    history["migrations"].append({
        "id": MIGRATION_ID,
        "applied_at": now_iso(),
        "from_version": "1.0.0",
        "to_version": __version__,
        "changes": changes,
    })
    atomic_write_json_data(migration_history_path(root), history)
    return {"already_migrated": False, "changes": changes, "skipped": [item for item in plan if item["action"] == "existing"]}
