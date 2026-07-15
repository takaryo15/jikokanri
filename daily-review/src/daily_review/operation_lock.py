"""Workspace-wide, dependency-free process lock for destructive operations."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


LOCK_RELATIVE_PATH = Path(".daily-review-operation.lock")
LOCK_STALE_AFTER = timedelta(hours=2)
_HELD_ROOTS: set[Path] = set()


class OperationLockedError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Tokyo"))


def lock_path(root: Path) -> Path:
    return root.resolve() / LOCK_RELATIVE_PATH


def is_current_process_lock(root: Path) -> bool:
    return root.resolve() in _HELD_ROOTS


def _owner(path: Path) -> dict[str, Any]:
    try:
        value = json.loads((path / "owner.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def is_stale_lock(root: Path, *, now: datetime | None = None) -> bool:
    path = lock_path(root)
    if not path.exists():
        return False
    owner = _owner(path)
    try:
        acquired = datetime.fromisoformat(str(owner["acquired_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    return (now or _now()) - acquired > LOCK_STALE_AFTER


def clear_stale_lock(root: Path) -> bool:
    path = lock_path(root)
    if not is_stale_lock(root):
        return False
    shutil.rmtree(path)
    return True


def workspace_root_for_path(path: Path) -> Path | None:
    resolved = path.resolve()
    for parent in (resolved.parent, *resolved.parents):
        if parent.name == "data":
            return parent.parent
        if (parent / LOCK_RELATIVE_PATH).exists():
            return parent
    return None


def assert_workspace_writable(path: Path) -> None:
    root = workspace_root_for_path(path)
    if root is None or root.resolve() in _HELD_ROOTS:
        return
    current_lock = lock_path(root)
    if current_lock.exists():
        raise OperationLockedError(
            f"別の重要操作が実行中です: {current_lock}。"
            "長時間残っている場合はdoctor checkでstale lockを確認してください"
        )


class WorkspaceLock:
    def __init__(self, root: Path, operation: str):
        self.root = root.resolve()
        self.operation = operation
        self.path = lock_path(self.root)
        self.acquired = False

    def __enter__(self) -> "WorkspaceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and is_stale_lock(self.root):
            clear_stale_lock(self.root)
        try:
            self.path.mkdir()
        except FileExistsError as exc:
            owner = _owner(self.path)
            detail = owner.get("operation") or "不明な操作"
            raise OperationLockedError(f"ワークスペースは使用中です: {detail}") from exc
        owner = {
            "operation": self.operation,
            "pid": os.getpid(),
            "acquired_at": _now().isoformat(timespec="seconds"),
        }
        try:
            (self.path / "owner.json").write_text(
                json.dumps(owner, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(self.path, ignore_errors=True)
            raise
        _HELD_ROOTS.add(self.root)
        self.acquired = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.acquired:
            _HELD_ROOTS.discard(self.root)
            shutil.rmtree(self.path, ignore_errors=True)
            self.acquired = False
