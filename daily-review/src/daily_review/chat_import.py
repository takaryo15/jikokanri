"""Conversion and safety helpers for validated ChatGPT structured imports."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .models import now_iso
from .organizer import add_draft_revision, normalize_draft
from .storage import draft_path


PARSER_VERSION = "chat-schema-1.0"


def import_hash(payload: dict[str, Any]) -> str:
    """Return a stable identifier for a validated import's contents."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_draft(
    payload: dict[str, Any],
    *,
    input_id: str,
    content_hash: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Map the explicit schema to the existing, user-reviewable draft shape."""
    day = payload["date"]
    draft = normalize_draft({}, day)
    draft["source_entry_ids"] = [input_id]
    draft["parser_version"] = PARSER_VERSION
    draft["import_source"] = "chat_import"
    draft["import_hash"] = content_hash
    draft["import_warnings"] = list(warnings)
    draft["today"] = {
        "main_candidates": list(payload["today"]["main"]),
        "completed": list(payload["today"]["completed"]),
        "partial": list(payload["today"]["partial"]),
        "not_completed": list(payload["today"]["not_completed"]),
    }
    draft["reflection"] = {
        "good": list(payload["reflection"]["good"]),
        "problems": list(payload["reflection"]["problems"]),
        "causes": list(payload["reflection"]["causes"]),
        "change_next": list(payload["reflection"]["change_next"]),
    }
    draft["tomorrow"] = {
        "main_candidates": list(payload["tomorrow"]["main"]),
        "other_tasks": list(payload["tomorrow"]["other_tasks"]),
        "minimum_candidates": list(payload["tomorrow"]["minimum"]),
    }
    draft["journal"] = list(payload["journal"])
    draft["unclassified"] = list(payload["unclassified"])
    add_draft_revision(draft, ["chat_import"])
    draft["updated_at"] = now_iso()
    return draft


def backup_unapproved_draft(root: Path, day: str) -> Path:
    """Copy an existing draft before a forced import replaces it."""
    source = draft_path(root, day)
    if not source.is_file():
        raise FileNotFoundError(f"バックアップ対象のドラフトがありません: {source}")
    timestamp = now_iso().replace(":", "").replace("+", "_")
    destination = root / "data" / "backups" / "drafts" / f"{day}_{timestamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    sequence = 1
    while destination.exists():
        destination = destination.with_name(f"{day}_{timestamp}_{sequence}.json")
        sequence += 1
    shutil.copy2(source, destination)
    return destination
