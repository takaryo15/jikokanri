from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _init(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0


def _add(root, title="大学院入試に合格する", *, level="medium", extra=()):
    result = runner.invoke(app, ["goal", "add", "--title", title, "--level", level, *extra, "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.output.split("ID: ", 1)[1].splitlines()[0]


def test_add_all_levels_list_show_and_json(tmp_path):
    _init(tmp_path)
    ids = [_add(tmp_path, level=level) for level in ("vision", "long", "medium", "short")]
    listing = runner.invoke(app, ["goal", "list", "--level", "medium", "--root", str(tmp_path)])
    shown = runner.invoke(app, ["goal", "show", ids[2], "--root", str(tmp_path)])
    rendered = runner.invoke(app, ["goal", "show", ids[2], "--json", "--root", str(tmp_path)])
    assert listing.exit_code == shown.exit_code == rendered.exit_code == 0
    assert ids[2] in listing.output and "レベル: medium" in shown.output
    assert json.loads(rendered.output)["id"] == ids[2]


def test_edit_status_archive_and_backup(tmp_path):
    _init(tmp_path)
    goal_id = _add(tmp_path, extra=("--start-date", "2026-07-14", "--due-date", "2026-08-31"))
    edited = runner.invoke(app, ["goal", "edit", goal_id, "--title", "埼玉大学大学院入試に合格する", "--due-date", "2026-08-30", "--root", str(tmp_path)])
    completed = runner.invoke(app, ["goal", "status", goal_id, "completed", "--root", str(tmp_path)])
    stored = json.loads((tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_text(encoding="utf-8"))
    resumed = runner.invoke(app, ["goal", "status", goal_id, "active", "--root", str(tmp_path)])
    archived = runner.invoke(app, ["goal", "archive", goal_id, "--yes", "--root", str(tmp_path)])
    normal = runner.invoke(app, ["goal", "list", "--root", str(tmp_path)])
    all_items = runner.invoke(app, ["goal", "list", "--all", "--root", str(tmp_path)])
    assert edited.exit_code == completed.exit_code == resumed.exit_code == archived.exit_code == 0
    assert stored["completed_at"] and stored["revision"] == 3 and stored["history"][-2]["changed_fields"] == ["title", "due_date"]
    assert json.loads((tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_text(encoding="utf-8"))["completed_at"] is None
    assert goal_id not in normal.output and goal_id in all_items.output
    assert list((tmp_path / "data" / "backups" / "goals").glob(f"{goal_id}_*.json"))


def test_goal_metrics_qualitative_home_doctor_and_release_check(tmp_path):
    _init(tmp_path)
    goal_id = _add(
        tmp_path,
        extra=(
            "--category", "院試", "--due-date", "2026-07-20",
            "--qualitative", "主要問題の解法を口頭で説明できる",
            "--metric", "過去問周回数|周|0|3|increase",
        ),
    )
    show = runner.invoke(app, ["goal", "show", goal_id, "--root", str(tmp_path)])
    home = runner.invoke(app, ["home", "--date", "2026-07-14", "--root", str(tmp_path)])
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    release = runner.invoke(app, ["release-check", "--root", str(tmp_path)])
    assert "進捗: 0%" in show.output
    assert "進行中の目標: 1件" in home.output and "残り6日" in home.output
    assert doctor.exit_code == release.exit_code == 0
    assert "OK   goals schema" in doctor.output and "OK   goal commands" in release.output
