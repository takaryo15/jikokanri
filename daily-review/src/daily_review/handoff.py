"""Creation, persistence, and validation of safe ChatGPT handoffs."""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .chat_import import import_hash
from .models import now_iso
from .storage import atomic_write_json_data, handoff_path, read_json_file


HANDOFF_VERSION = "1.0"
HANDOFF_STATUSES = {"issued", "received", "approved", "expired", "cancelled"}


class HandoffError(ValueError):
    pass


def expires_at(day: str) -> str:
    from .date_utils import parse_date

    next_day = parse_date(day) + timedelta(days=1)
    return datetime.combine(next_day, time(5), tzinfo=ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")


def new_session_id(day: str) -> str:
    return f"dr-{day.replace('-', '')}-{uuid.uuid4().hex[:8]}"


def load_handoffs(root: Path, day: str) -> dict[str, Any]:
    path = handoff_path(root, day)
    if not path.exists():
        return {"date": day, "handoffs": []}
    value = read_json_file(path)
    if not isinstance(value, dict) or value.get("date") not in (None, day):
        raise HandoffError("handoff JSONの日付が不正です")
    handoffs = value.get("handoffs")
    if not isinstance(handoffs, list) or not all(isinstance(item, dict) for item in handoffs):
        raise HandoffError("handoff JSONのhandoffsが不正です")
    return value


def save_handoffs(root: Path, day: str, payload: dict[str, Any]) -> Path:
    payload["date"] = day
    atomic_write_json_data(handoff_path(root, day), payload)
    return handoff_path(root, day)


def issue_handoff(root: Path, day: str, prompt: str, prompt_hash: str) -> dict[str, Any]:
    payload = load_handoffs(root, day)
    item = {
        "session_id": new_session_id(day),
        "created_at": now_iso(),
        "expires_at": expires_at(day),
        "prompt_hash": prompt_hash,
        "status": "issued",
        "received_at": None,
        "import_hash": None,
    }
    payload.setdefault("handoffs", []).append(item)
    save_handoffs(root, day, payload)
    return item


def render_handoff(day: str, prompt: str, item: dict[str, Any]) -> str:
    metadata = (
        "===== DAILY-REVIEW HANDOFF BEGIN =====\n"
        f"handoff_version: {HANDOFF_VERSION}\n"
        f"date: {day}\n"
        f"session_id: {item['session_id']}\n"
        f"prompt_hash: {item['prompt_hash']}\n"
        f"expires_at: {item['expires_at']}\n\n"
        "以下の指示に従って、今日の振り返りを整理してください。\n"
        "今日の振り返りをユーザーへ質問し、回答内容だけを使って整理してください。\n\n"
    )
    handoff_json = (
        '  "handoff": {\n'
        f'    "version": "{HANDOFF_VERSION}",\n'
        f'    "session_id": "{item["session_id"]}",\n'
        f'    "date": "{day}",\n'
        f'    "prompt_hash": "{item["prompt_hash"]}"\n'
        "  },\n"
    )
    bound_prompt = prompt.replace('  "schema_version": "1.0",\n  "date":', '  "schema_version": "1.0",\n' + handoff_json + '  "date":', 1)
    required = (
        "\n出力JSONには必ずhandoff情報を含めてください。\n"
        "```json\n"
        "{\n"
        '  "schema_version": "1.0",\n'
        + handoff_json.rstrip(",\n") + "\n"
        "}\n"
        "```\n"
        "===== DAILY-REVIEW HANDOFF END =====\n"
    )
    return metadata + bound_prompt.rstrip() + "\n" + required


def find_handoff(root: Path, day: str, session_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = load_handoffs(root, day)
    for item in payload["handoffs"]:
        if item.get("session_id") == session_id:
            return payload, item
    raise HandoffError("一致するhandoffが見つかりません")


def is_expired(item: dict[str, Any]) -> bool:
    value = item.get("expires_at")
    if not isinstance(value, str):
        raise HandoffError("handoffのexpires_atがありません")
    try:
        return datetime.now(ZoneInfo("Asia/Tokyo")) > datetime.fromisoformat(value)
    except ValueError as exc:
        raise HandoffError("handoffのexpires_atが不正です") from exc


def validate_response(root: Path, payload: dict[str, Any], *, requested_day: str | None, allow_expired: bool, force: bool) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """Verify a response before raw text or a draft can be written."""
    handoff = payload.get("handoff")
    if not isinstance(handoff, dict):
        raise HandoffError("handoff情報がありません")
    for key in ("version", "session_id", "date", "prompt_hash"):
        if not isinstance(handoff.get(key), str) or not handoff[key].strip():
            raise HandoffError(f"handoff.{key}がありません")
    if handoff["version"] != HANDOFF_VERSION:
        raise HandoffError(f"未対応のhandoff.versionです: {handoff['version']}")
    day = payload["date"]
    if requested_day is not None and requested_day != day:
        raise HandoffError(f"日付が一致しません\nCLI指定: {requested_day}\nJSON: {day}")
    if handoff["date"] != day:
        raise HandoffError(f"handoffの日付が一致しません\nhandoff: {handoff['date']}\nJSON: {day}")
    manifest, item = find_handoff(root, day, handoff["session_id"])
    if item.get("prompt_hash") != handoff["prompt_hash"]:
        raise HandoffError("handoffのprompt_hashが一致しません")
    status = item.get("status")
    if status == "cancelled":
        raise HandoffError("このhandoffはキャンセル済みです")
    if status == "approved":
        raise HandoffError("このhandoffはすでに承認済みです")
    if status == "received" and not force:
        raise HandoffError(f"このhandoffはすでに受信済みです\nsession_id: {handoff['session_id']}")
    expired = is_expired(item)
    if expired and not allow_expired:
        raise HandoffError(f"このhandoffは期限切れです\n対象日: {day}\n期限: {item['expires_at']}")
    if status == "expired" and not allow_expired:
        raise HandoffError("このhandoffは期限切れです")
    content_hash = import_hash(payload)
    for other in manifest["handoffs"]:
        if other.get("import_hash") == content_hash and not (force and other is item):
            raise HandoffError("同じChatGPTデータはすでに受信済みです")
    return manifest, item, handoff, content_hash


def update_handoff(root: Path, day: str, manifest: dict[str, Any], item: dict[str, Any], *, status: str, content_hash: str | None = None) -> None:
    if status not in HANDOFF_STATUSES:
        raise HandoffError(f"handoffのstatusが不正です: {status}")
    item["status"] = status
    if status in {"received", "approved"}:
        item["received_at"] = item.get("received_at") or now_iso()
    if content_hash:
        item["import_hash"] = content_hash
    save_handoffs(root, day, manifest)


def cancel_handoff(root: Path, day: str, *, session_id: str | None, latest: bool) -> dict[str, Any]:
    payload = load_handoffs(root, day)
    candidates = payload["handoffs"]
    if session_id:
        candidates = [item for item in candidates if item.get("session_id") == session_id]
    elif latest:
        candidates = [item for item in candidates if item.get("status") in {"issued", "received", "expired"}]
        candidates = candidates[-1:]
    else:
        raise HandoffError("--session-id または --latest を指定してください")
    if not candidates:
        raise HandoffError("キャンセル対象のhandoffが見つかりません")
    item = candidates[-1]
    if item.get("status") == "approved":
        raise HandoffError("承認済みhandoffはキャンセルできません")
    item["status"] = "cancelled"
    save_handoffs(root, day, payload)
    return item


def list_handoffs(root: Path, day: str, *, latest: bool) -> list[dict[str, Any]]:
    payload = load_handoffs(root, day)
    items = list(payload["handoffs"])
    for item in items:
        if item.get("status") == "issued" and is_expired(item):
            item["status"] = "expired"
    save_handoffs(root, day, payload) if items else None
    if latest:
        active = [item for item in items if item.get("status") == "issued"]
        return active[-1:] if active else []
    return items


def current_handoff_state(root: Path, day: str) -> str:
    """Read the newest relevant handoff without changing expiry state."""
    payload = load_handoffs(root, day)
    for item in reversed(payload["handoffs"]):
        status = item.get("status")
        if status == "issued":
            return "expired" if is_expired(item) else "issued"
        if status == "received":
            return "received"
    return "none"
