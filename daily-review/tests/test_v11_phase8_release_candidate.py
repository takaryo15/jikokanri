from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def test_release_candidate_version_release_check_and_runtime_git_exclusions(tmp_path):
    assert runner.invoke(app, ["--version"]).output.strip() == "daily-review 1.1.0rc1"
    release = runner.invoke(app, ["release-check", "--root", str(tmp_path)])
    assert release.exit_code == 0, release.output
    assert "v1.1.0rc1 is ready" in release.output

    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["migrate", "--root", str(tmp_path), "--yes"]).exit_code == 0
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert doctor.exit_code == 0, doctor.output
    check = runner.invoke(app, ["v11-check", "--root", str(tmp_path), "--verbose"])
    assert check.exit_code == 0, check.output
    assert "daily-review v11-check: OK" in check.output

    payload = json.loads(runner.invoke(app, ["v11-check", "--root", str(tmp_path), "--json"]).output)
    assert payload["root"] == str(tmp_path)
