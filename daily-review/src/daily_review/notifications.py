"""Notification event evaluation, delivery abstraction, history and deduplication."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from .date_utils import parse_date
from .models import now_iso
from .storage import atomic_write_json_data, load_daily, read_json_file
from .task_service import query_tasks, task_fingerprint


DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "console": {"enabled": True},
    "file": {"enabled": True},
    "reminder_review": {"enabled": True, "time": "21:00"},
    "instruction_approval": {"enabled": True},
    "task_alerts": {"enabled": True},
    "deduplication_hours": 24,
}


class NotificationError(ValueError):
    pass


@dataclass(frozen=True)
class Notification:
    notification_type: str
    target_date: str
    related_entity_id: str
    title: str
    message: str
    deduplication_key: str


@dataclass(frozen=True)
class SendResult:
    success: bool
    error: str | None = None


class NotificationSender(Protocol):
    destination: str

    def send(self, notification: Notification) -> SendResult: ...


class ConsoleSender:
    destination = "console"

    def __init__(self, writer: Callable[[str], None] = print):
        self.writer = writer

    def send(self, notification: Notification) -> SendResult:
        self.writer(f"NOTIFY [{notification.notification_type}] {notification.message}")
        return SendResult(True)


class FileSender:
    destination = "file"

    def __init__(self, root: Path):
        self.root = root

    def send(self, notification: Notification) -> SendResult:
        try:
            identifier = f"notification-{uuid.uuid4().hex[:12]}"
            atomic_write_json_data(
                self.root / "data" / "notifications" / "events" / f"{identifier}.json",
                {
                    "notification_id": identifier,
                    **asdict(notification),
                    "sent_at": now_iso(),
                },
            )
            return SendResult(True)
        except OSError as exc:
            return SendResult(False, str(exc))


def _merge_config(defaults: dict[str, Any], custom: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(defaults))
    for key, value in custom.items():
        if key not in result:
            continue
        if isinstance(result[key], dict) and isinstance(value, dict):
            result[key].update(
                {
                    name: setting
                    for name, setting in value.items()
                    if name in result[key]
                }
            )
        elif isinstance(value, type(result[key])) and not isinstance(value, dict):
            result[key] = value
    return result


def load_notification_config(root: Path) -> dict[str, Any]:
    path = root / "config" / "notifications.json"
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    value = read_json_file(path)
    if not isinstance(value, dict):
        raise NotificationError("notifications.jsonはJSONオブジェクトにしてください")
    config = _merge_config(DEFAULT_CONFIG, value)
    if not isinstance(config["enabled"], bool):
        raise NotificationError("enabledはtrueまたはfalseにしてください")
    for section in (
        "console",
        "file",
        "reminder_review",
        "instruction_approval",
        "task_alerts",
    ):
        if not isinstance(config[section].get("enabled"), bool):
            raise NotificationError(f"{section}.enabledはtrueまたはfalseにしてください")
    try:
        datetime.strptime(config["reminder_review"]["time"], "%H:%M")
    except (TypeError, ValueError) as exc:
        raise NotificationError(
            "reminder_review.timeはHH:MM形式にしてください"
        ) from exc
    hours = config["deduplication_hours"]
    if not isinstance(hours, int) or isinstance(hours, bool) or hours < 0:
        raise NotificationError("deduplication_hoursは0以上の整数にしてください")
    return config


def _notification(
    kind: str, day: str, entity: str, title: str, message: str
) -> Notification:
    raw = f"{kind}|{day}|{entity}"
    return Notification(
        kind,
        day,
        entity,
        title,
        message,
        hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def evaluate_notifications(
    root: Path, *, day: str, current: datetime, config: dict[str, Any] | None = None
) -> list[Notification]:
    parse_date(day)
    config = config or load_notification_config(root)
    if not config["enabled"]:
        return []
    values: list[Notification] = []
    entry = load_daily(root, day) or {}
    reminder_at = datetime.combine(
        parse_date(day),
        datetime.strptime(config["reminder_review"]["time"], "%H:%M").time(),
        tzinfo=ZoneInfo("Asia/Tokyo"),
    )
    if (
        config["reminder_review"]["enabled"]
        and current >= reminder_at
        and not (
            entry.get("raw_log") or entry.get("structured_review") or entry.get("diary")
        )
    ):
        values.append(
            _notification(
                "review_missing",
                day,
                f"review:{day}",
                "夜の振り返り",
                f"{day}の夜の振り返りが未実施です",
            )
        )
    proposal = entry.get("tomorrow_plan_proposal")
    final = entry.get("tomorrow_plan_final")
    if isinstance(proposal, dict):
        fingerprint = hashlib.sha256(
            json.dumps(proposal, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        values.append(
            _notification(
                "instruction_proposed",
                day,
                f"proposal:{day}:{fingerprint}",
                "明日の指示書案",
                "明日の指示書案が生成されました",
            )
        )
        if config["instruction_approval"]["enabled"] and not isinstance(final, dict):
            values.append(
                _notification(
                    "instruction_unapproved",
                    day,
                    f"proposal:{day}:{fingerprint}",
                    "指示書の承認",
                    "明日の指示書が未承認です",
                )
            )
    if isinstance(final, dict):
        fingerprint = hashlib.sha256(
            json.dumps(final, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        values.append(
            _notification(
                "instruction_confirmed",
                day,
                f"final:{day}:{fingerprint}",
                "指示書確定",
                f"{final.get('target_date', '翌日')}の指示書が確定しました",
            )
        )
    if config["task_alerts"]["enabled"]:
        overdue = query_tasks(root, today=day, due="overdue")
        for task in overdue:
            values.append(
                _notification(
                    "task_overdue",
                    day,
                    f"{task['id']}:{task_fingerprint(task)}",
                    "期限超過タスク",
                    f"期限超過: {task['title']}",
                )
            )
        today_tasks = query_tasks(root, today=day, due="today")
        for task in today_tasks:
            if task["is_main"] and task["status"] != "completed":
                values.append(
                    _notification(
                        "main_incomplete",
                        day,
                        f"{task['id']}:{task_fingerprint(task)}",
                        "Main未完了",
                        f"今日のMainが未完了です: {task['title']}",
                    )
                )
            if task["is_minimum"] and task.get("minimum_achieved") is not True:
                values.append(
                    _notification(
                        "minimum_incomplete",
                        day,
                        f"{task['id']}:{task_fingerprint(task)}",
                        "最低限未完了",
                        f"最低限が未完了です: {task['minimum_line']}",
                    )
                )
    values.sort(
        key=lambda item: (
            item.notification_type,
            item.related_entity_id,
            item.deduplication_key,
        )
    )
    return values


def history_path(root: Path) -> Path:
    return root / "data" / "notifications" / "history.json"


def load_history(root: Path) -> dict[str, Any]:
    path = history_path(root)
    if not path.exists():
        return {"records": []}
    value = read_json_file(path)
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise NotificationError("通知履歴JSONが不正です")
    if not all(isinstance(item, dict) for item in value["records"]):
        raise NotificationError("通知履歴recordsの項目が不正です")
    return value


def _history_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise NotificationError("通知履歴sent_atが不正です")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise NotificationError("通知履歴sent_atがISO 8601形式ではありません") from exc
    # Early development builds could write a local time without an offset.
    # Read it as the project's established local timezone for compatibility.
    return (
        parsed
        if parsed.tzinfo is not None
        else parsed.replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    )


def dispatch_notifications(
    root: Path,
    notifications: list[Notification],
    *,
    current: datetime,
    config: dict[str, Any] | None = None,
    senders: list[NotificationSender] | None = None,
) -> dict[str, Any]:
    config = config or load_notification_config(root)
    if senders is None:
        senders = []
        if config["console"]["enabled"]:
            senders.append(ConsoleSender())
        if config["file"]["enabled"]:
            senders.append(FileSender(root))
    history = load_history(root)
    records = history["records"]
    cutoff = current - timedelta(hours=config["deduplication_hours"])
    sent_keys: set[tuple[Any, Any]] = set()
    for item in records:
        if item.get("status") != "sent":
            continue
        if _history_time(item.get("sent_at")) >= cutoff:
            sent_keys.add((item.get("deduplication_key"), item.get("destination")))
    sent = failed = skipped = 0
    new_records = []
    for notification in notifications:
        for sender in senders:
            if (notification.deduplication_key, sender.destination) in sent_keys:
                skipped += 1
                continue
            attempted = current.isoformat(timespec="seconds")
            try:
                result = sender.send(notification)
            except Exception as exc:  # Sender isolation is a deliberate boundary.
                result = SendResult(False, str(exc))
            record = {
                "notification_id": f"notification-{uuid.uuid4().hex[:12]}",
                "notification_type": notification.notification_type,
                "target_date": notification.target_date,
                "related_entity_id": notification.related_entity_id,
                "destination": sender.destination,
                "status": "sent" if result.success else "failed",
                "attempted_at": attempted,
                "sent_at": attempted if result.success else None,
                "error": result.error,
                "deduplication_key": notification.deduplication_key,
            }
            new_records.append(record)
            if result.success:
                sent += 1
                sent_keys.add((notification.deduplication_key, sender.destination))
            else:
                failed += 1
    if new_records:
        history["records"] = (records + new_records)[-5000:]
        atomic_write_json_data(history_path(root), history)
    return {
        "candidates": len(notifications),
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "records": new_records,
    }


def parse_current(day: str, clock: str | None) -> datetime:
    target = parse_date(day)
    if clock is None:
        return datetime.now(ZoneInfo("Asia/Tokyo"))
    try:
        parsed = datetime.strptime(clock, "%H:%M").time()
    except ValueError as exc:
        raise NotificationError("timeはHH:MM形式にしてください") from exc
    return datetime.combine(target, parsed, tzinfo=ZoneInfo("Asia/Tokyo"))
