from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def test_rc1_version_migration_doctor_and_release_check(tmp_path):
    version = runner.invoke(app, ["--version"])
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    migrated = runner.invoke(app, ["migrate", "--yes", "--root", str(tmp_path)])
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    release = runner.invoke(app, ["release-check"])
    assert version.output.strip() == "daily-review 1.3.0"
    assert migrated.exit_code == doctor.exit_code == release.exit_code == 0
    for relative in (
        "data/evaluations/weekly",
        "data/evaluations/monthly",
        "data/replans",
        "data/backups/evaluations",
        "data/backups/replans",
    ):
        assert (tmp_path / relative).is_dir()
    history = json.loads(
        (tmp_path / "data" / "migrations.json").read_text(encoding="utf-8")
    )
    assert any(
        item["id"] == "v1.2-goal-evaluation-rc1" for item in history["migrations"]
    )
    assert "OK   weekly and monthly evaluations" in release.output
