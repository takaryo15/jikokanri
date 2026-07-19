#!/usr/bin/env python3
"""Disposable v1.2 release smoke test; never resolves or writes the real root."""

from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def run(root: Path, *args: str) -> str:
    result = runner.invoke(app, [*args, "--root", str(root)])
    if result.exit_code != 0:
        raise RuntimeError(
            f"{' '.join(args)} failed ({result.exit_code})\n{result.output}"
        )
    return result.output


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="daily-review-v12-smoke-") as directory:
        root = Path(directory).resolve()
        run(root, "init")
        run(root, "migrate", "--yes")

        output = run(root, "goal", "design", "--text", "大学院入試に合格したい")
        design_id = output.split("design_id: ", 1)[1].splitlines()[0]
        run(
            root,
            "goal",
            "design",
            "answer",
            design_id,
            "--answer",
            "2026-08-31までに過去問5年分を解ける",
        )
        proposal = {
            "goal": {
                "title": "大学院入試に合格する",
                "level": "medium",
                "category": "院試",
                "due_date": "2026-08-31",
                "qualitative": ["解法を説明できる"],
            },
            "milestones": [
                {
                    "title": "過去問一周",
                    "due_date": "2026-07-31",
                    "steps": [{"title": "2025年度を解く", "minimum": "問題文を読む"}],
                }
            ],
        }
        run(
            root,
            "goal",
            "design",
            "receive",
            design_id,
            "--json-text",
            json.dumps(proposal, ensure_ascii=False),
        )
        run(root, "goal", "design", "review", design_id)
        applied = run(root, "goal", "design", "apply", design_id, "--yes")
        goal_id = applied.split("目標ID: ", 1)[1].splitlines()[0]
        goal = json.loads(
            (root / "data/goals/items" / f"{goal_id}.json").read_text(encoding="utf-8")
        )
        milestone_id = goal["milestones"][0]["id"]
        step_id = goal["milestones"][0]["steps"][0]["id"]

        run(root, "plan", "week", "--date", "2026-07-14", "--save")
        run(root, "plan", "review", "--week", "2026-07-14")
        run(root, "plan", "apply", "--week", "2026-07-14", "--yes")
        start = date(2026, 7, 14)
        for offset in range(7):
            day = (start + timedelta(days=offset)).isoformat()
            run(root, "plan", "today", "--date", day, "--save")
            run(root, "plan", "apply", "--date", day, "--yes")
            daily = {
                "date": day,
                "structured_review": {
                    "today_main": [
                        {
                            "area": "院試",
                            "status": "完了" if offset == 0 else "一部進んだ",
                        }
                    ],
                    "minimum_line": {"院試": "達成"},
                    "what_went_well": ["継続した"],
                    "breakdown_causes": [],
                },
            }
            (root / "data/daily" / f"{day}.json").write_text(
                json.dumps(daily, ensure_ascii=False), encoding="utf-8"
            )
        run(
            root,
            "goal",
            "link",
            "--date",
            "2026-07-14",
            "--main-index",
            "1",
            "--goal",
            goal_id,
            "--milestone",
            milestone_id,
            "--step",
            step_id,
        )
        run(root, "goal", "progress", "--date", "2026-07-14", "--apply", "--yes")

        for week_start in ("2026-07-14", "2026-07-21", "2026-07-28", "2026-08-04"):
            run(root, "goal", "evaluate", "week", "--date", week_start, "--save")
            run(root, "goal", "evaluate", "apply", "--week", week_start, "--yes")
        created = json.loads(
            run(root, "goal", "replan", "--week", "2026-07-14", "--save", "--json")
        )
        run(
            root,
            "goal",
            "replan",
            "edit",
            created["id"],
            "--approve",
            created["proposals"][0]["id"],
        )
        run(root, "goal", "replan", "apply", created["id"], "--yes")
        run(root, "goal", "evaluate", "month", "--month", "2026-07", "--save")
        run(root, "goal", "evaluate", "apply", "--month", "2026-07", "--yes")
        run(root, "plan", "week", "--date", "2026-08-11")
        run(root, "goal", "evaluate", "month", "--month", "2026-08")
        run(root, "doctor")
        run(root, "v11-check")
        run(root, "v12-check")
    release = runner.invoke(app, ["release-check"])
    if release.exit_code != 0:
        raise RuntimeError(release.output)
    print("daily-review v1.2 smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
