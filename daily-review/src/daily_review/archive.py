"""Portable, verified ZIP backups with backwards-compatible manifests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from . import __version__
from .models import now_iso
from .operation_lock import WorkspaceLock
from .storage import atomic_write_json_data, read_json_file


ARCHIVE_ROOTS = ("data", "logs", "templates", "config")
MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1
MAX_FILES = 50_000
MAX_TOTAL_SIZE = 100 * 1024 * 1024
MAX_SINGLE_FILE = 25 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
SECRET_FRAGMENTS = (".env", "secret", "token", "credential", "api_key", "apikey")
DEFAULT_BACKUP_CONFIG = {
    "enabled": True,
    "directory": "backups",
    "before_restore": True,
    "before_repair": True,
    "retention_count": 20,
    "retention_days": 90,
    "verify_after_create": True,
}


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Tokyo"))


def _timestamp() -> str:
    return _now().strftime("%Y%m%dT%H%M%S%z")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def backup_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_backup_config(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = dict(DEFAULT_BACKUP_CONFIG)
    path = root / "config" / "recovery.json"
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"config/recovery.jsonを読み込めません: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("config/recovery.jsonはJSONオブジェクトにしてください")
        section = value.get("backup", value)
        if not isinstance(section, dict):
            raise ValueError("backup設定はJSONオブジェクトにしてください")
        for key in result:
            if key in section:
                result[key] = section[key]
    for key in ("retention_count", "retention_days"):
        if (
            not isinstance(result[key], int)
            or isinstance(result[key], bool)
            or result[key] < 0
        ):
            raise ValueError(f"backup.{key}は0以上の整数にしてください")
    for key in ("enabled", "before_restore", "before_repair", "verify_after_create"):
        if not isinstance(result[key], bool):
            raise ValueError(f"backup.{key}はtrueまたはfalseにしてください")
    if not isinstance(result["directory"], str) or not result["directory"].strip():
        raise ValueError("backup.directoryは空でない文字列にしてください")
    return result


def _excluded(relative: PurePosixPath) -> str | None:
    lowered = relative.as_posix().casefold()
    name = relative.name.casefold()
    if any(fragment in name for fragment in SECRET_FRAGMENTS):
        return "秘密情報候補"
    if name in {".ds_store", "thumbs.db"} or name.endswith((".pyc", ".tmp", ".lock")):
        return "一時・OSファイル"
    parts = relative.parts
    if parts[:2] in {("data", "tmp"), ("data", "transactions")}:
        return "再生成可能な一時データ"
    if "__pycache__" in parts or ".pytest_cache" in parts or ".git" in parts:
        return "キャッシュまたはGitデータ"
    if parts and parts[0] == "data" and "backups" in parts:
        return "バックアップの再帰取り込み防止"
    if lowered.startswith("config/") and name.endswith((".pem", ".key")):
        return "秘密鍵"
    return None


def plan_backup(root: Path, output: Path | None = None) -> dict[str, Any]:
    members: list[tuple[str, Path]] = []
    excluded: list[dict[str, str]] = []
    total_size = 0
    for name in ARCHIVE_ROOTS:
        directory = root / name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            relative = PurePosixPath(path.relative_to(root).as_posix())
            reason = _excluded(relative)
            if path.is_symlink():
                reason = "シンボリックリンク"
            if reason:
                excluded.append({"path": relative.as_posix(), "reason": reason})
                continue
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > MAX_SINGLE_FILE:
                raise ValueError(
                    f"バックアップ対象の単一ファイル上限を超えています: {relative}"
                )
            total_size += size
            members.append((relative.as_posix(), path))
    if len(members) > MAX_FILES or total_size > MAX_TOTAL_SIZE:
        raise ValueError("バックアップ対象が安全上限を超えています")
    destination = resolve_backup_output(root, output)
    return {
        "output": destination,
        "members": members,
        "excluded": excluded,
        "file_count": len(members),
        "estimated_size": total_size,
    }


def resolve_backup_output(root: Path, output: Path | None = None) -> Path:
    suffix = uuid.uuid4().hex[:6]
    default_name = f"daily-review-backup-{_timestamp()}-{suffix}.zip"
    if output is None:
        config = load_backup_config(root)
        return root / str(config["directory"]) / default_name
    output = output.expanduser()
    return output if output.suffix.lower() == ".zip" else output / default_name


def _counts(paths: list[str]) -> dict[str, int]:
    return {
        "reviews": sum(
            path.startswith("data/daily/") and path.endswith(".json") for path in paths
        ),
        "tasks": sum(path == "data/api/tasks.json" for path in paths),
        "instructions": sum(
            path.startswith("data/daily/") and path.endswith(".json") for path in paths
        ),
        "weekly": sum(
            path.startswith("data/weekly/") and path.endswith(".json") for path in paths
        ),
        "monthly": sum(
            path.startswith("data/monthly/") and path.endswith(".json")
            for path in paths
        ),
    }


def create_backup(
    root: Path,
    output: Path | None = None,
    *,
    manual: bool = True,
    acquire_lock: bool = True,
    idempotency_key: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    context = WorkspaceLock(root, "backup") if acquire_lock else _NullContext()
    with context:
        idempotency_digest = (
            hashlib.sha256(idempotency_key.encode()).hexdigest()
            if idempotency_key
            else None
        )
        idem_path = (
            root / "data" / "backup" / "idempotency" / f"{idempotency_digest}.json"
            if idempotency_digest
            else None
        )
        intent_hash = _sha256_bytes(
            json.dumps(
                {
                    "output": str(output) if output is not None else None,
                    "manual": manual,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )
        if idem_path and idem_path.exists():
            existing = read_json_file(idem_path)
            if existing.get("request_hash") != intent_hash:
                raise ValueError(
                    "同じidempotency keyが異なるbackup内容で使われています"
                )
            existing_path = Path(str(existing.get("path")))
            if not existing_path.is_file():
                raise ValueError("idempotency記録が参照するbackupがありません")
            verify_backup(existing_path)
            return existing_path, existing["manifest"]
        plan = plan_backup(root, output)
        destination: Path = plan["output"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"バックアップ先がすでに存在します: {destination}")
        files = []
        for archive_name, source in plan["members"]:
            content = source.read_bytes()
            files.append(
                {
                    "path": archive_name,
                    "size": len(content),
                    "sha256": _sha256_bytes(content),
                }
            )
        backup_id = f"backup_{_timestamp()}_{uuid.uuid4().hex[:8]}"
        manifest = {
            "backup_format_version": "1",
            "format_version": FORMAT_VERSION,
            "app_version": __version__,
            "created_at": now_iso(),
            "timezone": "Asia/Tokyo",
            "source_data_version": "3",
            "backup_id": backup_id,
            "manual": manual,
            "included_paths": list(ARCHIVE_ROOTS),
            "file_count": len(files),
            "total_size": plan["estimated_size"],
            "files": files,
            "counts": _counts([item["path"] for item in files]),
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            with zipfile.ZipFile(
                temp_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for archive_name, source in plan["members"]:
                    archive.write(source, archive_name)
                archive.writestr(
                    MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )
            os.link(temp_path, destination)
        except FileExistsError:
            raise FileExistsError(
                f"バックアップ先がすでに存在します: {destination}"
            ) from None
        finally:
            temp_path.unlink(missing_ok=True)
        if load_backup_config(root)["verify_after_create"]:
            verify_backup(destination)
        if idem_path:
            try:
                atomic_write_json_data(
                    idem_path,
                    {
                        "idempotency_key_hash": idempotency_digest,
                        "request_hash": intent_hash,
                        "path": str(destination),
                        "backup_id": backup_id,
                        "created_at": now_iso(),
                        "manifest": manifest,
                    },
                )
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        return destination, manifest


class _NullContext:
    def __enter__(self) -> "_NullContext":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


def _safe_member_name(name: str) -> str:
    if not name or "\\" in name or "~" in PurePosixPath(name).parts:
        raise ValueError(f"不正なアーカイブ内パスです: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or len(path.parts) < 2
        or ".." in path.parts
        or path.name in {"", "."}
    ):
        raise ValueError(f"不正なアーカイブ内パスです: {name!r}")
    if path.parts[0] not in ARCHIVE_ROOTS:
        raise ValueError(f"復元対象外のパスが含まれています: {name}")
    if _excluded(path):
        raise ValueError(f"バックアップ対象外のパスが含まれています: {name}")
    return path.as_posix()


def inspect_backup(backup_file: Path) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    try:
        with zipfile.ZipFile(backup_file) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES + 1:
                raise ValueError("アーカイブのファイル数が上限を超えています")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("アーカイブ内に重複したパスがあります")
            if MANIFEST_NAME not in names:
                raise ValueError("manifest.json がありません")
            try:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("manifest.json を読み込めません") from exc
            version = (
                manifest.get("backup_format_version")
                if isinstance(manifest, dict)
                else None
            )
            if version is None and isinstance(manifest, dict):
                version = manifest.get("format_version")
            if str(version) != "1":
                raise ValueError("対応していないバックアップ形式です")
            declared = manifest.get("files")
            if not isinstance(declared, list) or manifest.get("file_count") != len(
                declared
            ):
                raise ValueError("manifest のファイル一覧が不正です")
            hashes: dict[str, tuple[str | None, int | None]] = {}
            for item in declared:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise ValueError("manifest のファイル情報が不正です")
                name = _safe_member_name(item["path"])
                digest = item.get("sha256")
                size = item.get("size")
                if digest is not None and (
                    not isinstance(digest, str) or len(digest) != 64
                ):
                    raise ValueError(f"manifest のSHA-256が不正です: {name}")
                if size is not None and (
                    not isinstance(size, int) or isinstance(size, bool) or size < 0
                ):
                    raise ValueError(f"manifest のsizeが不正です: {name}")
                if name in hashes:
                    raise ValueError(f"manifest に重複したパスがあります: {name}")
                hashes[name] = (digest, size)
            restored: list[tuple[str, bytes]] = []
            expanded = 0
            for info in infos:
                if info.filename == MANIFEST_NAME or info.is_dir():
                    continue
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG}:
                    raise ValueError(f"特殊ファイルは復元できません: {info.filename}")
                name = _safe_member_name(info.filename)
                if name not in hashes:
                    raise ValueError(f"manifest にないファイルが含まれています: {name}")
                if info.file_size > MAX_SINGLE_FILE:
                    raise ValueError(f"単一ファイル上限を超えています: {name}")
                if (
                    info.compress_size
                    and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ValueError(f"圧縮率が安全上限を超えています: {name}")
                expanded += info.file_size
                if expanded > MAX_TOTAL_SIZE:
                    raise ValueError("展開サイズが安全上限を超えています")
                content = archive.read(info)
                digest, declared_size = hashes[name]
                if declared_size is not None and declared_size != len(content):
                    raise ValueError(f"ファイルサイズがmanifestと一致しません: {name}")
                if digest and _sha256_bytes(content) != digest:
                    raise ValueError(f"SHA-256が一致しません: {name}")
                restored.append((name, content))
            if {name for name, _ in restored} != set(hashes):
                raise ValueError("manifest とアーカイブのファイル一覧が一致しません")
            return manifest, restored
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIPファイルとして読み込めません") from exc


def verify_backup(backup_file: Path) -> dict[str, Any]:
    manifest, members = inspect_backup(backup_file)
    return {
        "valid": True,
        "backup_id": manifest.get("backup_id"),
        "file_count": len(members),
        "sha256": backup_sha256(backup_file),
        "manifest": manifest,
    }


def list_backups(root: Path, directory: Path | None = None) -> list[dict[str, Any]]:
    base = directory or root / str(load_backup_config(root)["directory"])
    values = []
    for path in sorted(base.rglob("*.zip")) if base.is_dir() else []:
        try:
            result = verify_backup(path)
            manifest = result["manifest"]
            values.append(
                {
                    "backup_id": manifest.get("backup_id", path.stem),
                    "created_at": manifest.get("created_at"),
                    "file_name": path.name,
                    "size": path.stat().st_size,
                    "app_version": manifest.get("app_version"),
                    "data_version": manifest.get("source_data_version"),
                    "counts": manifest.get("counts", {}),
                    "verified": True,
                    "manual": manifest.get("manual", True),
                    "path": str(path),
                }
            )
        except (OSError, ValueError) as exc:
            values.append(
                {
                    "backup_id": path.stem,
                    "file_name": path.name,
                    "size": path.stat().st_size,
                    "verified": False,
                    "error": str(exc),
                    "path": str(path),
                }
            )
    values.sort(
        key=lambda item: (str(item.get("created_at", "")), item["file_name"]),
        reverse=True,
    )
    return values


def retention_candidates(
    root: Path, directory: Path | None = None
) -> list[dict[str, Any]]:
    config = load_backup_config(root)
    backups = [
        item for item in list_backups(root, directory) if not item.get("manual", True)
    ]
    cutoff = _now() - timedelta(days=config["retention_days"])
    candidates = []
    for index, item in enumerate(backups):
        try:
            created = datetime.fromisoformat(str(item.get("created_at")))
        except ValueError:
            created = datetime.fromtimestamp(
                Path(item["path"]).stat().st_mtime, ZoneInfo("Asia/Tokyo")
            )
        if index >= config["retention_count"] or created < cutoff:
            candidates.append(item)
    return candidates


def prune_backups(
    root: Path, directory: Path | None = None, *, dry_run: bool = True
) -> list[dict[str, Any]]:
    candidates = retention_candidates(root, directory)
    if not dry_run:
        with WorkspaceLock(root, "backup-retention"):
            for item in candidates:
                Path(item["path"]).unlink()
    return candidates


def delete_backup_files(
    root: Path, paths: list[Path], *, idempotency_key: str | None = None
) -> dict[str, Any]:
    normalized = sorted(str(path.expanduser().resolve()) for path in paths)
    request_hash = _sha256_bytes(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    idempotency_digest = (
        hashlib.sha256(idempotency_key.encode()).hexdigest()
        if idempotency_key
        else None
    )
    idem_path = (
        root / "data" / "backup" / "idempotency" / f"delete-{idempotency_digest}.json"
        if idempotency_digest
        else None
    )
    if idem_path and idem_path.exists():
        existing = read_json_file(idem_path)
        if existing.get("request_hash") != request_hash:
            raise ValueError("同じidempotency keyが異なる削除内容で使われています")
        return {**existing["result"], "status": "idempotent_replay"}
    staged: list[tuple[Path, Path]] = []
    with WorkspaceLock(root, "backup-delete"):
        try:
            for text in normalized:
                path = Path(text)
                verify_backup(path)
                temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.deleting")
                os.replace(path, temporary)
                staged.append((path, temporary))
            result = {
                "status": "deleted",
                "paths": normalized,
                "deleted_count": len(normalized),
            }
            if idem_path:
                atomic_write_json_data(
                    idem_path,
                    {
                        "idempotency_key_hash": idempotency_digest,
                        "request_hash": request_hash,
                        "completed_at": now_iso(),
                        "result": result,
                    },
                )
        except Exception:
            for original, temporary in reversed(staged):
                if temporary.exists():
                    os.replace(temporary, original)
            raise
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)
    return result


def restore_backup(
    root: Path,
    backup_file: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Compatibility wrapper for the v1 restore command."""
    from .recovery import apply_legacy_restore

    return apply_legacy_restore(root, backup_file, dry_run=dry_run, force=force)
