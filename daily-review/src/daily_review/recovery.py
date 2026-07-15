"""Preview-first restore workflows with conflict and stale-state protection."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .archive import (
    ARCHIVE_ROOTS,
    backup_sha256,
    create_backup,
    inspect_backup,
    load_backup_config,
    plan_backup,
)
from .models import now_iso
from .operation_lock import WorkspaceLock
from .storage import atomic_write_json_data, read_json_file


RESTORE_MODES = {"merge", "replace", "missing-only"}
RESTORE_TTL_MINUTES = 30


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _workspace_state_hash(root: Path) -> str:
    values = []
    for name in ARCHIVE_ROOTS:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith(
                ("data/tmp/", "data/transactions/", "data/restore/")
            ):
                continue
            if relative.startswith("data/backups/"):
                continue
            values.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return _hash(values)


def _confirmation_path(root: Path, token: str) -> Path:
    if (
        not token.startswith("restore_confirm_")
        or not token.removeprefix("restore_confirm_").isalnum()
    ):
        raise ValueError("restore confirmation tokenの形式が不正です")
    return root / "data" / "transactions" / "restore" / f"{token}.json"


def _history_path(root: Path) -> Path:
    return root / "data" / "restore" / "history.json"


def _history(root: Path) -> dict[str, Any]:
    path = _history_path(root)
    if not path.exists():
        return {"version": "1", "records": []}
    value = read_json_file(path)
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError("復元履歴の形式が不正です")
    return value


def _current_files(root: Path) -> set[str]:
    return {
        name
        for name, _ in plan_backup(root)["members"]
        if not name.startswith(("data/restore/", "data/rollover/", "data/repairs/"))
    }


def _safe_restore_target(root: Path, name: str) -> Path:
    """Resolve a manifest path without following a workspace symlink outside root."""
    root_resolved = root.resolve()
    target = root / name
    candidate = target.resolve(strict=False)
    if not candidate.is_relative_to(root_resolved):
        raise ValueError(f"復元先が保存先ルートの外を参照しています: {name}")
    current = target
    while current != root and current != current.parent:
        if current.is_symlink():
            raise ValueError(f"復元先にシンボリックリンクが含まれています: {name}")
        current = current.parent
    return target


def _prospective_integrity(
    root: Path, writes: list[tuple[Path, bytes]], deletes: list[Path]
) -> dict[str, Any]:
    from .integrity import run_integrity_check

    with tempfile.TemporaryDirectory(prefix="daily-review-restore-check-") as name:
        staged_root = Path(name)
        for directory_name in ARCHIVE_ROOTS:
            source = root / directory_name
            if source.is_dir():
                shutil.copytree(
                    source, staged_root / directory_name, dirs_exist_ok=True
                )
        staged_writes = [
            (staged_root / target.relative_to(root), content)
            for target, content in writes
        ]
        staged_deletes = [staged_root / target.relative_to(root) for target in deletes]
        _apply_bytes_transaction(staged_root, staged_writes, staged_deletes)
        report = run_integrity_check(staged_root)
        changed = {target.relative_to(root).as_posix() for target, _ in writes}
        blocking = [
            item
            for item in report["issues"]
            if item["severity"] in {"error", "critical"}
            and any(
                str(item["path"]).split("#", 1)[0].startswith(path) for path in changed
            )
        ]
        if blocking:
            codes = ", ".join(sorted({item["code"] for item in blocking}))
            raise ValueError(f"復元後整合性検査に失敗しました: {codes}")
        return report


def build_restore_preview(
    root: Path, backup_file: Path, *, mode: str = "merge"
) -> dict[str, Any]:
    if mode not in RESTORE_MODES:
        raise ValueError("modeはmerge、replace、missing-onlyのいずれかにしてください")
    manifest, members = inspect_backup(backup_file)
    added: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    conflicts: list[dict[str, Any]] = []
    skipped: list[str] = []
    archive_names = {name for name, _ in members}
    for name, content in members:
        target = _safe_restore_target(root, name)
        if not target.exists():
            added.append(name)
        elif target.read_bytes() == content:
            unchanged.append(name)
        elif mode == "replace":
            updated.append(name)
        elif mode == "missing-only":
            skipped.append(name)
        else:
            conflicts.append(
                {
                    "path": name,
                    "code": "CONTENT_CONFLICT",
                    "message": "現在データとバックアップの内容が異なります",
                }
            )
    deleted = sorted(_current_files(root) - archive_names) if mode == "replace" else []
    summary = {
        "added": sorted(added),
        "updated": sorted(updated),
        "unchanged": sorted(unchanged),
        "skipped": sorted(skipped),
        "conflicts": conflicts,
        "deleted": deleted,
    }
    return {
        "status": "conflict" if conflicts else "preview_ready",
        "backup_id": manifest.get("backup_id", backup_file.stem),
        "backup_file": str(backup_file.resolve()),
        "backup_sha256": backup_sha256(backup_file),
        "mode": mode,
        "manifest": manifest,
        "changes": summary,
        "counts": {key: len(value) for key, value in summary.items()},
        "state_hash": _workspace_state_hash(root),
    }


def preview_restore(
    root: Path, backup_file: Path, *, mode: str = "merge"
) -> dict[str, Any]:
    preview = build_restore_preview(root, backup_file, mode=mode)
    token = f"restore_confirm_{uuid.uuid4().hex}"
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    preview["confirmation_required"] = True
    preview["confirmation_token"] = token
    preview["previewed_at"] = now.isoformat(timespec="seconds")
    confirmation = {
        "version": "1",
        "token": token,
        "backup_sha256": preview["backup_sha256"],
        "mode": mode,
        "diff_hash": _hash(preview["changes"]),
        "state_hash": preview["state_hash"],
        "issued_at": preview["previewed_at"],
        "expires_at": (now + timedelta(minutes=RESTORE_TTL_MINUTES)).isoformat(
            timespec="seconds"
        ),
        "used_at": None,
    }
    atomic_write_json_data(_confirmation_path(root, token), confirmation)
    history = _history(root)
    history["records"].append(
        {
            "restore_id": f"restore_{uuid.uuid4().hex[:12]}",
            "backup_id": preview["backup_id"],
            "backup_sha256": preview["backup_sha256"],
            "mode": mode,
            "previewed_at": preview["previewed_at"],
            "applied_at": None,
            "status": preview["status"],
            "added_count": preview["counts"]["added"],
            "updated_count": preview["counts"]["updated"],
            "skipped_count": preview["counts"]["skipped"],
            "conflict_count": preview["counts"]["conflicts"],
            "confirmation_token_id": token,
            "pre_restore_backup_id": None,
            "error": None,
            "source": "cli",
        }
    )
    atomic_write_json_data(_history_path(root), history)
    return preview


def _apply_bytes_transaction(
    root: Path, writes: list[tuple[Path, bytes]], deletes: list[Path]
) -> None:
    originals: dict[Path, bytes | None] = {}
    staged: list[tuple[Path, Path]] = []
    touched: list[Path] = []
    for target, content in writes:
        target.parent.mkdir(parents=True, exist_ok=True)
        originals[target] = target.read_bytes() if target.exists() else None
        fd, name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".restore", dir=target.parent
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        staged.append((target, Path(name)))
    for target in deletes:
        originals.setdefault(target, target.read_bytes() if target.exists() else None)
    try:
        for target, temporary in staged:
            os.replace(temporary, target)
            touched.append(target)
        for target in deletes:
            if target.exists():
                target.unlink()
                touched.append(target)
    except Exception:
        for target in reversed(touched):
            original = originals[target]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original)
        raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def apply_restore(
    root: Path,
    backup_file: Path,
    *,
    mode: str,
    confirmation_token: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    confirmation_path = _confirmation_path(root, confirmation_token)
    if not confirmation_path.exists():
        raise ValueError("restore confirmation tokenが見つかりません")
    confirmation = read_json_file(confirmation_path)
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    if confirmation.get("used_at"):
        return {
            "status": "idempotent_replay",
            "restore_id": confirmation.get("restore_id"),
            "pre_restore_backup": confirmation.get("pre_restore_backup"),
        }
    try:
        expires = datetime.fromisoformat(str(confirmation["expires_at"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("restore confirmation tokenが不正です") from exc
    if now >= expires:
        raise ValueError("restore confirmation tokenの有効期限が切れています")
    preview = build_restore_preview(root, backup_file, mode=mode)
    if (
        confirmation.get("backup_sha256") != preview["backup_sha256"]
        or confirmation.get("mode") != mode
    ):
        raise ValueError("restore confirmation tokenと復元内容が一致しません")
    if confirmation.get("state_hash") != preview["state_hash"] or confirmation.get(
        "diff_hash"
    ) != _hash(preview["changes"]):
        raise ValueError("復元preview後にデータが変更されました。再previewしてください")
    if preview["changes"]["conflicts"]:
        raise ValueError("未解決の復元競合があります")
    idem_path = (
        root
        / "data"
        / "restore"
        / "idempotency"
        / f"{hashlib.sha256(idempotency_key.encode()).hexdigest()}.json"
        if idempotency_key
        else None
    )
    request_hash = _hash({"backup": preview["backup_sha256"], "mode": mode})
    if idem_path and idem_path.exists():
        record = read_json_file(idem_path)
        if record.get("request_hash") != request_hash:
            raise ValueError("同じidempotency keyが異なる復元内容で使われています")
        return record["result"]
    manifest, members = inspect_backup(backup_file)
    for name, content in members:
        if name.endswith(".json"):
            try:
                value = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"復元対象JSONが不正です: {name}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"復元対象JSONはオブジェクトにしてください: {name}")
    writes = [
        (_safe_restore_target(root, name), content)
        for name, content in members
        if name in set(preview["changes"]["added"] + preview["changes"]["updated"])
    ]
    deletes = [
        _safe_restore_target(root, name) for name in preview["changes"]["deleted"]
    ]
    prospective = _prospective_integrity(root, writes, deletes)
    with WorkspaceLock(root, "restore"):
        config = load_backup_config(root)
        if not config["enabled"] or not config["before_restore"]:
            raise ValueError("安全のため復元前バックアップを無効化できません")
        safety_path, safety_manifest = create_backup(
            root,
            root / str(config["directory"]) / "automatic",
            manual=False,
            acquire_lock=False,
        )
        restore_id = f"restore_{uuid.uuid4().hex[:12]}"
        result = {
            "status": "applied",
            "restore_id": restore_id,
            "backup_id": manifest.get("backup_id", backup_file.stem),
            "mode": mode,
            "added": len(preview["changes"]["added"]),
            "updated": len(preview["changes"]["updated"]),
            "skipped": len(
                preview["changes"]["skipped"] + preview["changes"]["unchanged"]
            ),
            "deleted": len(preview["changes"]["deleted"]),
            "pre_restore_backup": str(safety_path),
            "pre_restore_backup_id": safety_manifest.get("backup_id"),
            "integrity_status": prospective["status"],
        }
        confirmation["used_at"] = now.isoformat(timespec="seconds")
        confirmation["restore_id"] = restore_id
        confirmation["pre_restore_backup"] = str(safety_path)
        metadata_writes: list[tuple[Path, bytes]] = [
            (
                confirmation_path,
                (json.dumps(confirmation, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                ),
            )
        ]
        if idem_path:
            metadata_writes.append(
                (
                    idem_path,
                    (
                        json.dumps(
                            {
                                "idempotency_key": idempotency_key,
                                "request_hash": request_hash,
                                "completed_at": now_iso(),
                                "result": result,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n"
                    ).encode("utf-8"),
                )
            )
        history = _history(root)
        history["records"].append(
            {
                "restore_id": restore_id,
                "backup_id": result["backup_id"],
                "backup_sha256": preview["backup_sha256"],
                "mode": mode,
                "previewed_at": confirmation.get("issued_at"),
                "applied_at": now_iso(),
                "status": "applied",
                "added_count": result["added"],
                "updated_count": result["updated"],
                "skipped_count": result["skipped"],
                "conflict_count": 0,
                "pre_restore_backup_id": result["pre_restore_backup_id"],
                "confirmation_token_id": confirmation_token,
                "error": None,
                "source": "cli",
            }
        )
        metadata_writes.append(
            (
                _history_path(root),
                (json.dumps(history, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                ),
            )
        )
        _apply_bytes_transaction(root, writes + metadata_writes, deletes)
    return result


def restore_history(root: Path) -> list[dict[str, Any]]:
    records = list(_history(root)["records"])
    records.sort(
        key=lambda item: (
            str(item.get("applied_at") or item.get("previewed_at") or ""),
            str(item.get("restore_id", "")),
        ),
        reverse=True,
    )
    return records


def apply_legacy_restore(
    root: Path, backup_file: Path, *, dry_run: bool = False, force: bool = False
) -> dict[str, Any]:
    manifest, members = inspect_backup(backup_file)
    conflicts = [name for name, _ in members if (root / name).exists()]
    new_files = [name for name, _ in members if name not in conflicts]
    result = {
        "manifest": manifest,
        "files": [name for name, _ in members],
        "new_files": new_files,
        "conflicts": conflicts,
        "skipped": conflicts if not force else [],
    }
    if dry_run:
        return result
    if conflicts and not force:
        raise FileExistsError(
            "既存ファイルと競合するため復元を中止しました: " + ", ".join(conflicts)
        )
    with WorkspaceLock(root, "legacy-restore"):
        if force and conflicts:
            safety, _ = create_backup(
                root, root / "backups", manual=False, acquire_lock=False
            )
            result["safety_backup"] = str(safety)
        _apply_bytes_transaction(
            root, [(root / name, content) for name, content in members], []
        )
    return result
