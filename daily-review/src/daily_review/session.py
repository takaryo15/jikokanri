"""Small, non-authoritative state records for the ChatGPT workflow."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import atomic_write_json_data, read_json_file, session_path


SESSION_STATUSES = {
    "prompt_ready",
    "waiting_for_chatgpt",
    "imported",
    "draft",
    "approved",
    "cancelled",
}


def load_session(root: Path, day: str) -> dict[str, Any] | None:
    path = session_path(root, day)
    if not path.exists():
        return None
    value = read_json_file(path)
    if not isinstance(value, dict) or value.get("date") not in (None, day):
        raise ValueError("chat sessionの日付が不正です")
    status = value.get("status")
    if status not in SESSION_STATUSES:
        raise ValueError("chat sessionのstatusが不正です")
    return value


def prompt_hash(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def save_session(root: Path, day: str, status: str, **updates: Any) -> dict[str, Any]:
    """Safely store workflow progress; this never changes daily or draft data."""
    if status not in SESSION_STATUSES:
        raise ValueError(f"chat sessionのstatusが不正です: {status}")
    # Preserve corrupt auxiliary data for doctor rather than replacing it.  The
    # workflow can continue without a session, but must not hide the problem.
    existing = load_session(root, day) or {}
    value = dict(existing)
    value.update(updates)
    value["date"] = day
    value["status"] = status
    value.setdefault("prompt_generated_at", None)
    value.setdefault("prompt_hash", None)
    value.setdefault("imported_at", None)
    value.setdefault("draft_path", None)
    value.setdefault("completed_at", None)
    atomic_write_json_data(session_path(root, day), value)
    return value


def save_prompt_session(root: Path, day: str, prompt: str) -> dict[str, Any]:
    return save_session(
        root,
        day,
        "waiting_for_chatgpt",
        prompt_generated_at=now_iso(),
        prompt_hash=prompt_hash(prompt),
        imported_at=None,
        draft_path=None,
        completed_at=None,
    )
