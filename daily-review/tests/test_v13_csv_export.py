from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()
DAY = "2026-07-15"


def _write_daily(root: Path) -> None:
    daily = root / "data" / "daily"
    daily.mkdir(parents=True)
    entry = {
        "date": DAY,
        "created_at": f"{DAY}T20:00:00+09:00",
        "updated_at": f"{DAY}T22:00:00+09:00",
        "diary": '日本語, 改行\n引用符 "保持"',
        "quick_review": {
            "done": ["完了"],
            "not_done": ["未完了"],
            "causes": ["原因"],
            "tomorrow": ["=SUM(A1:A2)"],
            "minimum": ["1分"],
        },
        "tomorrow_plan_proposal": {
            "status": "pending_review",
            "target_date": "2026-07-16",
            "main": ["=SUM(A1:A2)"],
            "tasks": [
                {
                    "id": "task-formula",
                    "area": "=SUM(A1:A2)",
                    "task": "=SUM(A1:A2)",
                    "priority": 1,
                    "minimum_line": "+1分",
                }
            ],
        },
    }
    (daily / f"{DAY}.json").write_text(
        json.dumps(entry, ensure_ascii=False), encoding="utf-8"
    )


def _run(root: Path, *args: str):
    return runner.invoke(app, ["export", "csv", *args, "--root", str(root)])


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_export_all_creates_fixed_csvs_and_preserves_japanese(tmp_path):
    _write_daily(tmp_path)
    output = tmp_path / "out"
    result = _run(tmp_path, "--type", "all", "--output", str(output))
    assert result.exit_code == 0, result.output
    assert {path.name for path in output.iterdir()} == {
        "reviews.csv",
        "tasks.csv",
        "instructions.csv",
    }
    review = _rows(output / "reviews.csv")[0]
    assert review["journal"] == '日本語, 改行\n引用符 "保持"'
    assert json.loads(review["completed_items"]) == ["完了"]
    assert _rows(output / "tasks.csv")[0]["title"] == "'=SUM(A1:A2)"
    instruction = _rows(output / "instructions.csv")[0]
    assert instruction["main_1"] == "'=SUM(A1:A2)"
    assert json.loads(instruction["minimum"]) == ["+1分"]


def test_empty_export_has_header_and_excel_mode_has_bom(tmp_path):
    output = tmp_path / "empty.csv"
    result = _run(tmp_path, "--type", "reviews", "--output", str(output), "--excel")
    assert result.exit_code == 0
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    assert _rows(output) == []
    assert output.read_text(encoding="utf-8-sig").startswith("date,")


def test_existing_output_requires_force_and_force_is_deterministic(tmp_path):
    _write_daily(tmp_path)
    output = tmp_path / "tasks.csv"
    first = _run(tmp_path, "--type", "tasks", "--output", str(output))
    original = output.read_bytes()
    conflict = _run(tmp_path, "--type", "tasks", "--output", str(output))
    forced = _run(tmp_path, "--type", "tasks", "--output", str(output), "--force")
    assert first.exit_code == forced.exit_code == 0
    assert conflict.exit_code == 4 and "--force" in conflict.output
    assert output.read_bytes() == original


def test_date_range_week_month_and_invalid_combination(tmp_path):
    _write_daily(tmp_path)
    day_path = tmp_path / "day.csv"
    outside_path = tmp_path / "outside.csv"
    week_path = tmp_path / "week.csv"
    month_path = tmp_path / "month.csv"
    assert (
        _run(
            tmp_path, "--type", "reviews", "--date", DAY, "--output", str(day_path)
        ).exit_code
        == 0
    )
    assert len(_rows(day_path)) == 1
    assert (
        _run(
            tmp_path,
            "--type",
            "reviews",
            "--from",
            "2026-07-01",
            "--to",
            "2026-07-14",
            "--output",
            str(outside_path),
        ).exit_code
        == 0
    )
    assert _rows(outside_path) == []
    assert (
        _run(
            tmp_path,
            "--type",
            "reviews",
            "--period",
            "week",
            "--date",
            DAY,
            "--output",
            str(week_path),
        ).exit_code
        == 0
    )
    assert len(_rows(week_path)) == 1
    assert (
        _run(
            tmp_path,
            "--type",
            "reviews",
            "--period",
            "month",
            "--date",
            DAY,
            "--output",
            str(month_path),
        ).exit_code
        == 0
    )
    assert len(_rows(month_path)) == 1
    invalid = _run(tmp_path, "--type", "reviews", "--date", DAY, "--from", DAY)
    assert invalid.exit_code != 0
