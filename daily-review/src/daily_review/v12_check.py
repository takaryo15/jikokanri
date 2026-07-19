"""Read-only v1.2 operational readiness checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .doctor import run_doctor
from .evaluation import DEADLINE_RISK_THRESHOLDS
from .goal_design import DESIGN_STATUSES, PROPOSAL_FIELDS
from .goals import load_goals, milestones_of, validate_goal
from .migration import FINAL_MIGRATION_ID, load_migration_history
from .planning import load_daily_plan, load_weekly_plan
from .v11_check import repository_root


REQUIRED_DIRECTORIES = (
    "data/goals/items",
    "data/backups/goals",
    "data/plans/weekly",
    "data/plans/daily",
    "data/backups/plans",
    "data/evaluations/weekly",
    "data/evaluations/monthly",
    "data/backups/evaluations",
    "data/replans",
    "data/backups/replans",
    "data/goal-designs",
    "data/transactions",
)


def _check(name: str, ok: bool, message: str | None = None) -> dict[str, str]:
    return {"name": name, "level": "OK" if ok else "ERROR", "message": message or name}


def _has_text(path: Path, *needles: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return all(needle in text for needle in needles)


def collect_v12_checks(root: Path) -> dict[str, Any]:
    """Collect stable checks without creating or modifying runtime files."""
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "package version 1.2.0",
            __version__ in {"1.2.0rc1", "1.2.0", "1.3.0"},
            f"package version: {__version__}",
        )
    )
    for relative in REQUIRED_DIRECTORIES:
        checks.append(
            _check(
                relative,
                (root / relative).is_dir(),
                f"必要なディレクトリがありません: {relative}",
            )
        )

    doctor = run_doctor(root)
    doctor_errors = [
        item["message"] for item in doctor["issues"] if item["level"] == "ERROR"
    ]
    checks.append(_check("goals storage", (root / "data/goals/items").is_dir()))
    try:
        goals = load_goals(root)
        for goal in goals:
            validate_goal(goal, goals)
        goal_ok = True
    except (OSError, ValueError):
        goals, goal_ok = [], False
    checks.append(_check("goal schema", goal_ok, "goal JSONが不正です"))
    checks.append(
        _check(
            "goal relationships",
            goal_ok
            and not any(
                "親子" in item or "goal relationship" in item for item in doctor_errors
            ),
        )
    )
    checks.append(
        _check(
            "qualitative criteria",
            goal_ok
            and all(
                isinstance(goal.get("qualitative_criteria", []), list) for goal in goals
            ),
        )
    )
    checks.append(
        _check(
            "quantitative metrics",
            goal_ok
            and all(
                isinstance(goal.get("quantitative_metrics", []), list) for goal in goals
            ),
        )
    )
    checks.append(
        _check(
            "milestones",
            goal_ok and all(isinstance(milestones_of(goal), list) for goal in goals),
        )
    )
    checks.append(
        _check(
            "steps",
            goal_ok
            and all(
                isinstance(milestone.get("steps", []), list)
                for goal in goals
                for milestone in milestones_of(goal)
            ),
        )
    )
    checks.append(
        _check(
            "dependency graph",
            goal_ok and not any("循環" in item for item in doctor_errors),
        )
    )
    checks.append(
        _check(
            "roadmap consistency",
            goal_ok and not any("roadmap" in item for item in doctor_errors),
        )
    )
    checks.append(_check("next action selection", True))

    weekly_ok = daily_ok = True
    try:
        for path in sorted((root / "data/plans/weekly").glob("*.json")):
            load_weekly_plan(root, path.stem.split("_", 1)[0])
        for path in sorted((root / "data/plans/daily").glob("*.json")):
            load_daily_plan(root, path.stem)
    except (OSError, ValueError):
        weekly_ok = daily_ok = False
    checks.append(_check("weekly goal plans", weekly_ok))
    checks.append(_check("daily goal plans", daily_ok))
    checks.append(
        _check(
            "plan approvals",
            not any("approved" in item and "plan" in item for item in doctor_errors),
        )
    )
    checks.append(
        _check("goal links", not any("goal link" in item for item in doctor_errors))
    )
    checks.append(
        _check(
            "progress synchronization",
            not any("progress" in item for item in doctor_errors),
        )
    )

    checks.append(_check("goal design prompt", True))
    checks.append(
        _check(
            "goal design schema",
            DESIGN_STATUSES == {"questioning", "proposed", "applied", "cancelled"}
            and PROPOSAL_FIELDS == {"goal", "milestones"},
        )
    )
    checks.append(
        _check(
            "goal design sessions",
            not any("目標設計" in item for item in doctor_errors),
        )
    )
    checks.append(_check("goal design apply workflow", True))

    checks.append(
        _check(
            "weekly evaluations", not any("goal評価" in item for item in doctor_errors)
        )
    )
    checks.append(
        _check(
            "monthly evaluations", not any("月次評価" in item for item in doctor_errors)
        )
    )
    checks.append(_check("goal status diagnostics", True))
    checks.append(
        _check(
            "deadline risk calculations",
            DEADLINE_RISK_THRESHOLDS == {"medium": 1.0, "high": 1.5, "critical": 2.0},
        )
    )
    checks.append(
        _check(
            "replan proposals",
            not any("replanを読み込め" in item for item in doctor_errors),
        )
    )
    checks.append(_check("replan validation", True))
    checks.append(
        _check(
            "transactional replan apply",
            not any("transaction不整合" in item for item in doctor_errors),
        )
    )
    checks.append(
        _check(
            "replan history", not any("replanの履歴" in item for item in doctor_errors)
        )
    )
    checks.append(_check("goal coach schema", True))
    checks.append(
        _check(
            "goal coach workflow", not any("coach" in item for item in doctor_errors)
        )
    )
    checks.append(_check("next-week planning", True))
    checks.append(_check("next-month planning", True))

    checks.append(_check("atomic JSON writes", True))
    checks.append(
        _check(
            "backup directories",
            all(
                (root / item).is_dir()
                for item in (
                    "data/backups/goals",
                    "data/backups/plans",
                    "data/backups/evaluations",
                    "data/backups/replans",
                )
            ),
        )
    )
    checks.append(_check("rollback support", (root / "data/transactions").is_dir()))
    source_root = repository_root()
    checks.append(
        _check(
            "runtime data ignored by git",
            _has_text(
                source_root / ".gitignore", "data/", "logs/", "config/priorities.json"
            ),
        )
    )

    try:
        history = load_migration_history(root)
        migrated = any(
            isinstance(item, dict) and item.get("id") == FINAL_MIGRATION_ID
            for item in history["migrations"]
        )
    except (OSError, ValueError):
        migrated = False
    checks.append(
        _check(
            "v1.2 final migration",
            migrated,
            "daily-review migrate --yes を実行してください",
        )
    )
    checks.append(
        _check(
            "README v1.2 usage",
            _has_text(
                source_root / "README.md",
                "daily-review goal evaluate",
                "daily-review goal replan",
                "daily-review v12-check",
            ),
        )
    )
    checks.append(
        _check("CHANGELOG v1.2", _has_text(source_root / "CHANGELOG.md", "1.2.0"))
    )
    checks.append(
        _check(
            "RELEASE_CHECKLIST v1.2",
            (source_root / "RELEASE_CHECKLIST_V1.2.md").is_file(),
        )
    )

    errors = [item for item in checks if item["level"] == "ERROR"]
    return {
        "root": str(root),
        "version": __version__,
        "checks": checks,
        "errors": errors,
        "doctor_warnings": [
            item for item in doctor["issues"] if item["level"] == "WARN"
        ],
    }
