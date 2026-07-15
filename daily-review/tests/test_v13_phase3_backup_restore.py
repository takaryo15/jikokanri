from __future__ import annotations

import json
import stat
import zipfile

import pytest
from typer.testing import CliRunner

from daily_review.archive import (
    create_backup,
    delete_backup_files,
    inspect_backup,
    list_backups,
    prune_backups,
    verify_backup,
)
from daily_review.cli import app
from daily_review.recovery import apply_restore, preview_restore


runner = CliRunner()


def _write(root, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_backup_create_manifest_hash_exclusions_dry_run_and_list(tmp_path):
    _write(tmp_path, "data/daily/2026-07-15.json", '{"date":"2026-07-15"}')
    _write(tmp_path, "logs/日本語.md", "日本語")
    _write(tmp_path, "config/priorities.json", '{"priorities":[]}')
    _write(tmp_path, "config/api-token.json", '{"token":"do-not-copy"}')
    _write(tmp_path, "data/backups/daily/old.json", "{}")

    dry = runner.invoke(
        app,
        ["backup", "create", "--root", str(tmp_path), "--dry-run", "--format", "json"],
    )
    assert dry.exit_code == 0, dry.output
    dry_payload = json.loads(dry.output)
    assert dry_payload["status"] == "dry_run"
    assert not (tmp_path / "backups").exists()
    assert any(
        item["path"] == "config/api-token.json" for item in dry_payload["excluded"]
    )

    output = tmp_path / "external" / "snapshot.zip"
    path, manifest = create_backup(tmp_path, output)
    assert path == output
    assert manifest["backup_format_version"] == "1"
    assert manifest["backup_id"].startswith("backup_")
    assert all(
        item["size"] >= 0 and len(item["sha256"]) == 64 for item in manifest["files"]
    )
    names = {item["path"] for item in manifest["files"]}
    assert "logs/日本語.md" in names
    assert "config/api-token.json" not in names
    assert "data/backups/daily/old.json" not in names
    assert verify_backup(path)["valid"] is True
    listed = list_backups(tmp_path, output.parent)
    assert listed[0]["backup_id"] == manifest["backup_id"]
    with pytest.raises(FileExistsError):
        create_backup(tmp_path, output)


def test_backup_create_and_delete_are_idempotent_with_explicit_key(tmp_path):
    _write(tmp_path, "data/daily/2026-07-15.json", '{"date":"2026-07-15"}')
    output = tmp_path / "external" / "snapshot.zip"
    first_path, first_manifest = create_backup(
        tmp_path, output, idempotency_key="backup-request"
    )
    replay_path, replay_manifest = create_backup(
        tmp_path, output, idempotency_key="backup-request"
    )
    assert replay_path == first_path
    assert replay_manifest["backup_id"] == first_manifest["backup_id"]
    assert list(output.parent.glob("*.zip")) == [output]
    with pytest.raises(ValueError, match="idempotency"):
        create_backup(
            tmp_path,
            tmp_path / "external" / "different.zip",
            idempotency_key="backup-request",
        )

    deleted = delete_backup_files(tmp_path, [output], idempotency_key="delete-request")
    replay = delete_backup_files(tmp_path, [output], idempotency_key="delete-request")
    assert deleted["deleted_count"] == 1
    assert replay["status"] == "idempotent_replay"
    assert not output.exists()


def test_backup_verify_rejects_tamper_traversal_symlink_and_missing_manifest(tmp_path):
    source = tmp_path / "source"
    _write(source, "data/daily/a.json", "{}")
    archive, _ = create_backup(source, tmp_path / "valid.zip")
    manifest, members = inspect_backup(archive)

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr(members[0][0], b"changed")
    with pytest.raises(ValueError, match="SHA-256|ファイルサイズ"):
        verify_backup(tampered)

    traversal = tmp_path / "traversal.zip"
    bad_manifest = {
        "format_version": 1,
        "file_count": 1,
        "files": [{"path": "../x", "sha256": "0" * 64}],
    }
    with zipfile.ZipFile(traversal, "w") as output:
        output.writestr("manifest.json", json.dumps(bad_manifest))
        output.writestr("../x", b"x")
    with pytest.raises(ValueError, match="不正"):
        verify_backup(traversal)

    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing, "w") as output:
        output.writestr("data/daily/a.json", "{}")
    with pytest.raises(ValueError, match="manifest"):
        verify_backup(missing)

    symlink = tmp_path / "symlink.zip"
    symlink_manifest = {
        "format_version": 1,
        "file_count": 1,
        "files": [{"path": "data/link", "sha256": "0" * 64}],
    }
    with zipfile.ZipFile(symlink, "w") as output:
        output.writestr("manifest.json", json.dumps(symlink_manifest))
        info = zipfile.ZipInfo("data/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(info, "outside")
    with pytest.raises(ValueError, match="特殊ファイル"):
        verify_backup(symlink)


def test_backup_creation_failure_leaves_no_partial_archive(tmp_path, monkeypatch):
    _write(tmp_path, "data/daily/a.json", "{}")
    output = tmp_path / "failed.zip"

    def fail_write(*_args, **_kwargs):
        raise OSError("simulated archive failure")

    monkeypatch.setattr(zipfile.ZipFile, "write", fail_write)
    with pytest.raises(OSError, match="simulated"):
        create_backup(tmp_path, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".failed.zip.*.tmp"))


def test_restore_preview_apply_auto_backup_replay_and_stale(tmp_path):
    source = tmp_path / "source"
    _write(source, "data/daily/2026-07-15.json", '{"date":"2026-07-15","raw_log":"x"}')
    archive, _ = create_backup(source, tmp_path / "backup.zip")
    target = tmp_path / "target"
    target.mkdir()

    preview = preview_restore(target, archive, mode="merge")
    assert preview["counts"]["added"] == 1
    assert not (target / "data/daily/2026-07-15.json").exists()
    result = apply_restore(
        target,
        archive,
        mode="merge",
        confirmation_token=preview["confirmation_token"],
        idempotency_key="restore-one",
    )
    assert result["status"] == "applied"
    assert (target / "data/daily/2026-07-15.json").is_file()
    assert result["pre_restore_backup"]
    replay = apply_restore(
        target,
        archive,
        mode="merge",
        confirmation_token=preview["confirmation_token"],
        idempotency_key="restore-one",
    )
    assert replay["status"] in {"applied", "idempotent_replay"}

    second_target = tmp_path / "second"
    second_target.mkdir()
    stale = preview_restore(second_target, archive, mode="merge")
    _write(second_target, "data/daily/other.json", "{}")
    with pytest.raises(ValueError, match="変更"):
        apply_restore(
            second_target,
            archive,
            mode="merge",
            confirmation_token=stale["confirmation_token"],
        )


def test_restore_modes_conflicts_and_token_required_cli(tmp_path):
    source = tmp_path / "source"
    _write(source, "data/daily/a.json", '{"date":"a"}')
    archive, _ = create_backup(source, tmp_path / "backup.zip")
    target = tmp_path / "target"
    _write(target, "data/daily/a.json", '{"different":true}')

    merge = preview_restore(target, archive, mode="merge")
    missing = preview_restore(target, archive, mode="missing-only")
    replace = preview_restore(target, archive, mode="replace")
    assert merge["counts"]["conflicts"] == 1
    assert missing["counts"]["skipped"] == 1
    assert replace["counts"]["updated"] == 1

    cli = runner.invoke(
        app,
        ["restore", "apply", str(archive), "--root", str(target)],
    )
    assert cli.exit_code == 4
    assert "confirmation-token" in cli.output


def test_restore_rejects_expired_token_and_disabled_safety_backup(tmp_path):
    source = tmp_path / "source"
    _write(source, "data/daily/2026-07-15.json", '{"date":"2026-07-15"}')
    archive, _ = create_backup(source, tmp_path / "backup.zip")

    expired_target = tmp_path / "expired"
    expired_target.mkdir()
    expired = preview_restore(expired_target, archive)
    token_path = (
        expired_target
        / "data/transactions/restore"
        / f"{expired['confirmation_token']}.json"
    )
    token = json.loads(token_path.read_text(encoding="utf-8"))
    token["expires_at"] = "2000-01-01T00:00:00+09:00"
    token_path.write_text(json.dumps(token), encoding="utf-8")
    with pytest.raises(ValueError, match="有効期限"):
        apply_restore(
            expired_target,
            archive,
            mode="merge",
            confirmation_token=expired["confirmation_token"],
        )

    disabled_target = tmp_path / "disabled"
    _write(
        disabled_target,
        "config/recovery.json",
        '{"backup":{"before_restore":false}}',
    )
    disabled = preview_restore(disabled_target, archive)
    with pytest.raises(ValueError, match="バックアップを無効化"):
        apply_restore(
            disabled_target,
            archive,
            mode="merge",
            confirmation_token=disabled["confirmation_token"],
        )
    assert not (disabled_target / "data/daily/2026-07-15.json").exists()


def test_restore_rejects_symlink_in_destination_path(tmp_path):
    source = tmp_path / "source"
    _write(source, "data/daily/2026-07-15.json", '{"date":"2026-07-15"}')
    archive, _ = create_backup(source, tmp_path / "backup.zip")
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "data").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="ルートの外|シンボリックリンク"):
        preview_restore(target, archive)
    assert not (outside / "daily/2026-07-15.json").exists()


def test_retention_ignores_manual_and_restore_failure_rolls_back(tmp_path, monkeypatch):
    _write(
        tmp_path,
        "data/daily/2026-07-15.json",
        '{"date":"2026-07-15","raw_log":"old"}',
    )
    _write(
        tmp_path,
        "config/recovery.json",
        '{"backup":{"retention_count":1,"retention_days":90}}',
    )
    create_backup(tmp_path, tmp_path / "backups/auto-1.zip", manual=False)
    create_backup(tmp_path, tmp_path / "backups/auto-2.zip", manual=False)
    create_backup(tmp_path, tmp_path / "backups/manual.zip", manual=True)
    candidates = prune_backups(tmp_path, dry_run=True)
    assert len(candidates) == 1
    assert candidates[0]["manual"] is False

    source = tmp_path / "source"
    _write(
        source,
        "data/daily/2026-07-15.json",
        '{"date":"2026-07-15","raw_log":"new"}',
    )
    _write(source, "logs/2026-07-15.md", "new log")
    archive, _ = create_backup(source, tmp_path / "restore.zip")
    preview = preview_restore(tmp_path, archive, mode="replace")
    from daily_review import recovery

    original_replace = recovery.os.replace
    attempts = 0

    def fail_on_real_second(source_path, destination_path):
        nonlocal attempts
        destination = str(destination_path)
        if (
            destination.startswith(str(tmp_path))
            and "daily-review-restore-check" not in destination
        ):
            attempts += 1
            if attempts == 2:
                raise OSError("simulated replace failure")
        return original_replace(source_path, destination_path)

    monkeypatch.setattr(recovery.os, "replace", fail_on_real_second)
    with pytest.raises(OSError, match="simulated"):
        apply_restore(
            tmp_path,
            archive,
            mode="replace",
            confirmation_token=preview["confirmation_token"],
        )
    current = json.loads(
        (tmp_path / "data/daily/2026-07-15.json").read_text(encoding="utf-8")
    )
    assert current["raw_log"] == "old"
