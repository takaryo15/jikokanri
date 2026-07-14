from __future__ import annotations

from pathlib import Path
from typing import Any

from .storage import DATA_DIRS, TEMPLATE_CONTENTS, read_json_file
from .validation import ALLOWED_TASK_RESULT_STATUSES, validate_plan, validate_task_results


def _issue(level: str, message: str) -> dict[str, str]:
    return {"level": level, "message": message}


def _check_daily(path: Path, issues: list[dict[str, str]]) -> None:
    try:
        entry = read_json_file(path)
    except (OSError, ValueError) as exc:
        issues.append(_issue("ERROR", f"日次JSONを読み込めません: {path.name} ({exc})")); return
    if not isinstance(entry, dict) or not isinstance(entry.get("date"), str):
        issues.append(_issue("ERROR", f"日次JSONの必須dateが不正です: {path.name}")); return
    proposal, final = entry.get("tomorrow_plan_proposal"), entry.get("tomorrow_plan_final")
    if proposal:
        for message in validate_plan(proposal, entry["date"], final=False).errors:
            issues.append(_issue("ERROR", f"{path.name}: {message}"))
        if proposal.get("status") == "approved":
            issues.append(_issue("ERROR", f"{path.name}: proposalがapprovedになっています"))
    if final:
        for message in validate_plan(final, entry["date"], final=True).errors:
            issues.append(_issue("ERROR", f"{path.name}: {message}"))
        for message in validate_task_results(entry).errors:
            issues.append(_issue("ERROR", f"{path.name}: {message}"))
    for item in entry.get("task_results") or []:
        if item.get("status") not in ALLOWED_TASK_RESULT_STATUSES:
            issues.append(_issue("ERROR", f"{path.name}: 実行結果のstatusが不正です（{item.get('status')}）"))


def run_doctor(root: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    for relative in DATA_DIRS:
        if not (root / relative).is_dir():
            issues.append(_issue("ERROR", f"必要なディレクトリがありません: {relative}"))
    for name in TEMPLATE_CONTENTS:
        if not (root / "templates" / name).is_file():
            issues.append(_issue("ERROR", f"必要なテンプレートがありません: templates/{name}"))
    daily_dir = root / "data" / "daily"
    daily_files = sorted(daily_dir.glob("*.json")) if daily_dir.is_dir() else []
    for path in daily_files:
        _check_daily(path, issues)
        if not (root / "logs" / f"{path.stem}.md").is_file():
            issues.append(_issue("WARN", f"Markdownが存在しない日次データ: {path.name}"))
    for folder, prefix in (("weekly", "weekly_"), ("monthly", "monthly_")):
        directory = root / "data" / folder
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            try:
                value = read_json_file(path)
                if not isinstance(value, dict):
                    raise ValueError("JSONオブジェクトではありません")
            except (OSError, ValueError) as exc:
                issues.append(_issue("ERROR", f"{folder}JSONを読み込めません: {path.name} ({exc})"))
                continue
            markdown_name = f"{prefix}{path.stem}.md" if folder == "weekly" else f"monthly_{path.stem}.md"
            if not (root / "logs" / markdown_name).is_file():
                issues.append(_issue("WARN", f"Markdownが存在しない{folder}データ: {path.name}"))
    return {"daily_count": len(daily_files), "issues": issues}
