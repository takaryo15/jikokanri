from __future__ import annotations

import hashlib
import json
import zipfile

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()


def _write(root, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _backup(root, output):
    result = runner.invoke(app, ["backup", "--root", str(root), "--output", str(output)])
    assert result.exit_code == 0, result.output


def _zip_with_manifest(path, members: dict[str, bytes], *, hashes: bool = True) -> None:
    files = [{"path": name, **({"sha256": hashlib.sha256(value).hexdigest()} if hashes else {})} for name, value in members.items()]
    manifest = {"format_version": 1, "file_count": len(files), "files": files}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, value in members.items():
            archive.writestr(name, value)


def test_backup_includes_data_logs_templates_and_manifest_without_mutating_source(tmp_path):
    _write(tmp_path, "data/daily/2026-07-14.json", '{"date":"2026-07-14"}')
    _write(tmp_path, "logs/2026-07-14.md", "log")
    _write(tmp_path, "templates/night_review_prompt.md", "template")
    output = tmp_path / "out" / "backup.zip"
    _backup(tmp_path, output)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert set(archive.namelist()) == {"manifest.json", "data/daily/2026-07-14.json", "logs/2026-07-14.md", "templates/night_review_prompt.md"}
        assert manifest["file_count"] == 3
        assert manifest["files"][0]["sha256"]
    assert (tmp_path / "data/daily/2026-07-14.json").read_text(encoding="utf-8") == '{"date":"2026-07-14"}'


def test_backup_never_overwrites_named_output(tmp_path):
    output = tmp_path / "backup.zip"
    _backup(tmp_path, output)
    result = runner.invoke(app, ["backup", "--root", str(tmp_path), "--output", str(output)])
    assert result.exit_code == 1
    assert "すでに存在" in result.output


def test_restore_dry_run_does_not_write_and_conflict_stops(tmp_path):
    archive = tmp_path / "backup.zip"
    _zip_with_manifest(archive, {"data/daily/new.json": b"new"})
    target = tmp_path / "target"
    result = runner.invoke(app, ["restore", str(archive), "--root", str(target), "--dry-run"])
    assert result.exit_code == 0
    assert not target.exists()
    _write(target, "data/daily/new.json", "old")
    result = runner.invoke(app, ["restore", str(archive), "--root", str(target)])
    assert result.exit_code == 1
    assert (target / "data/daily/new.json").read_text(encoding="utf-8") == "old"


def test_restore_writes_new_validated_files(tmp_path):
    archive = tmp_path / "backup.zip"
    _zip_with_manifest(archive, {"data/daily/new.json": b"new", "templates/prompt.md": b"prompt"})
    target = tmp_path / "target"
    result = runner.invoke(app, ["restore", str(archive), "--root", str(target)])
    assert result.exit_code == 0, result.output
    assert (target / "data/daily/new.json").read_bytes() == b"new"
    assert (target / "templates/prompt.md").read_bytes() == b"prompt"


def test_restore_rejects_missing_manifest_traversal_and_bad_hash(tmp_path):
    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("data/daily/x.json", "x")
    traversal = tmp_path / "traversal.zip"
    _zip_with_manifest(traversal, {"../outside.txt": b"bad"})
    bad_hash = tmp_path / "bad-hash.zip"
    with zipfile.ZipFile(bad_hash, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format_version": 1, "file_count": 1, "files": [{"path": "data/daily/x.json", "sha256": "0" * 64}]}))
        archive.writestr("data/daily/x.json", b"actual")
    for archive in (missing, traversal, bad_hash):
        result = runner.invoke(app, ["restore", str(archive), "--root", str(tmp_path / "target")])
        assert result.exit_code == 1


def test_doctor_reports_errors_without_changing_data_and_version(tmp_path):
    init = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert init.exit_code == 0
    _write(tmp_path, "data/daily/bad.json", "{")
    before = (tmp_path / "data/daily/bad.json").read_bytes()
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert (tmp_path / "data/daily/bad.json").read_bytes() == before
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert version.output.strip() == "daily-review 1.2.0"


def test_doctor_detects_plan_limits_and_task_result_status_but_allows_unknown_fields(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    _write(tmp_path, "data/daily/2026-07-14.json", json.dumps({
        "date": "2026-07-14", "unknown_future_field": {"keep": True},
        "tomorrow_plan_final": {"status": "approved", "approved_at": "2026-07-14T22:00:00+09:00", "target_date": "2026-07-15", "main": ["a", "b", "c", "d"], "tasks": [{"id": "task-1", "area": "a", "task": "x", "priority": 1, "minimum_line": ""}], "one_change_tomorrow": "x"},
        "task_results": [{"task_id": "task-1", "status": "invalid", "minimum_line_achieved": True}],
    }))
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Mainは最大3つ" in result.output
    assert "最低ライン" in result.output
    assert "statusが不正" in result.output


def test_legacy_daily_weekly_and_missing_monthly_remain_readable(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    legacy = {"date": "2026-07-14", "created_at": "2026-07-14T22:00:00+09:00", "updated_at": "2026-07-14T22:00:00+09:00", "raw_log": "old", "unknown_future_field": "keep"}
    _write(tmp_path, "data/daily/2026-07-14.json", json.dumps(legacy, ensure_ascii=False))
    _write(tmp_path, "data/weekly/2026-07-07_2026-07-13.json", json.dumps({"start_date": "2026-07-07", "end_date": "2026-07-13", "recorded_days": 1}))
    loaded = load_daily(tmp_path, "2026-07-14")
    assert loaded["unknown_future_field"] == "keep"
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "WARN" in result.output
