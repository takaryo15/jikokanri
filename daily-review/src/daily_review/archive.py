from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from . import __version__
from .models import now_iso


ARCHIVE_ROOTS = ("data", "logs", "templates")
MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1


def _timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d-%H%M%S")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _archive_members(root: Path) -> list[tuple[str, Path]]:
    members: list[tuple[str, Path]] = []
    for name in ARCHIVE_ROOTS:
        directory = root / name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                members.append((path.relative_to(root).as_posix(), path))
    return members


def resolve_backup_output(root: Path, output: Path | None = None) -> Path:
    default_name = f"daily-review-backup-{_timestamp()}.zip"
    if output is None:
        return root / "backups" / default_name
    output = output.expanduser()
    return output if output.suffix.lower() == ".zip" else output / default_name


def create_backup(root: Path, output: Path | None = None) -> tuple[Path, dict[str, Any]]:
    destination = resolve_backup_output(root, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"バックアップ先がすでに存在します: {destination}")
    members = _archive_members(root)
    files = []
    for archive_name, source in members:
        files.append({"path": archive_name, "sha256": _sha256_bytes(source.read_bytes())})
    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": now_iso(),
        "app_version": __version__,
        "included_paths": list(ARCHIVE_ROOTS),
        "file_count": len(files),
        "files": files,
    }
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for archive_name, source in members:
                archive.write(source, archive_name)
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        # link() is exclusive: a concurrently created destination is never overwritten.
        os.link(temp_path, destination)
    except FileExistsError:
        raise FileExistsError(f"バックアップ先がすでに存在します: {destination}") from None
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return destination, manifest


def _safe_member_name(name: str) -> str:
    if not name or "\\" in name:
        raise ValueError(f"不正なアーカイブ内パスです: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or len(path.parts) < 2 or ".." in path.parts or path.name in {"", "."}:
        raise ValueError(f"不正なアーカイブ内パスです: {name!r}")
    if path.parts[0] not in ARCHIVE_ROOTS:
        raise ValueError(f"復元対象外のパスが含まれています: {name}")
    return path.as_posix()


def inspect_backup(backup_file: Path) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    try:
        with zipfile.ZipFile(backup_file) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("アーカイブ内に重複したパスがあります")
            if MANIFEST_NAME not in names:
                raise ValueError("manifest.json がありません")
            try:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("manifest.json を読み込めません") from exc
            if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
                raise ValueError("対応していないバックアップ形式です")
            declared = manifest.get("files")
            if not isinstance(declared, list) or manifest.get("file_count") != len(declared):
                raise ValueError("manifest のファイル一覧が不正です")
            hashes: dict[str, str] = {}
            for item in declared:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise ValueError("manifest のファイル情報が不正です")
                name = _safe_member_name(item["path"])
                digest = item.get("sha256")
                if digest is not None and (not isinstance(digest, str) or len(digest) != 64):
                    raise ValueError(f"manifest のSHA-256が不正です: {name}")
                if name in hashes:
                    raise ValueError(f"manifest に重複したパスがあります: {name}")
                hashes[name] = digest
            restored: list[tuple[str, bytes]] = []
            for info in infos:
                if info.filename == MANIFEST_NAME:
                    continue
                if info.is_dir() or info.filename.endswith("/"):
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"シンボリックリンクは復元できません: {info.filename}")
                name = _safe_member_name(info.filename)
                if name not in hashes:
                    raise ValueError(f"manifest にないファイルが含まれています: {name}")
                content = archive.read(info)
                if hashes[name] and _sha256_bytes(content) != hashes[name]:
                    raise ValueError(f"SHA-256が一致しません: {name}")
                restored.append((name, content))
            if {name for name, _ in restored} != set(hashes):
                raise ValueError("manifest とアーカイブのファイル一覧が一致しません")
            return manifest, restored
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIPファイルとして読み込めません") from exc


def restore_backup(root: Path, backup_file: Path, *, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    manifest, members = inspect_backup(backup_file)
    conflicts = [name for name, _ in members if (root / name).exists()]
    new_files = [name for name, _ in members if name not in conflicts]
    result = {"manifest": manifest, "files": [name for name, _ in members], "new_files": new_files, "conflicts": conflicts, "skipped": conflicts if not force else []}
    if dry_run:
        return result
    if conflicts and not force:
        raise FileExistsError("既存ファイルと競合するため復元を中止しました: " + ", ".join(conflicts))
    if force and conflicts:
        safety_archive, _ = create_backup(root, root / "backups" / f"pre-restore-{_timestamp()}.zip")
        result["safety_backup"] = str(safety_archive)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".daily-review-restore-", dir=root))
    try:
        for name, content in members:
            staged = staging / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)
        for name, _ in members:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / name, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return result
