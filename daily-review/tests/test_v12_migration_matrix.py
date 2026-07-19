from __future__ import annotations

import hashlib
import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v10_v11_and_partial_workspaces_migrate_without_rewriting_data(tmp_path):
    for name in ("v10", "v11", "partial"):
        root = tmp_path / name
        (root / "data/daily").mkdir(parents=True)
        daily = root / "data/daily/2026-07-14.json"
        daily.write_text(
            json.dumps(
                {"date": "2026-07-14", "unknown": {"keep": True}}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        if name == "v11":
            (root / "data/inbox").mkdir()
            (root / "data/inbox/2026-07-14.json").write_text(
                '{"date":"2026-07-14","entries":[]}', encoding="utf-8"
            )
        before = digest(daily)
        assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
        first = runner.invoke(app, ["migrate", "--yes", "--root", str(root)])
        second = runner.invoke(app, ["migrate", "--yes", "--root", str(root)])
        assert first.exit_code == second.exit_code == 0
        assert digest(daily) == before
        history = json.loads(
            (root / "data/migrations.json").read_text(encoding="utf-8")
        )["migrations"]
        ids = [item["id"] for item in history]
        assert len(ids) == len(set(ids)) and "v1.2-final" in ids


def test_migration_never_overwrites_invalid_existing_config(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    config = tmp_path / "config/priorities.json"
    config.write_text('{"priorities": "broken"}', encoding="utf-8")
    before = config.read_bytes()
    result = runner.invoke(app, ["migrate", "--yes", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert config.read_bytes() == before
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert doctor.exit_code == 1 and "優先順位設定が不正" in doctor.output
