from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import __version__
from .date_utils import week_range_for
from .storage import DATA_DIRS, REQUIRED_TEMPLATE_NAMES, read_json_file
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
    checks: list[str] = []
    if root.exists() and root.is_dir():
        checks.append("保存先ルート")
    else:
        issues.append(_issue("ERROR", f"保存先ルートを確認できません: {root}"))
    for relative in DATA_DIRS:
        path = root / relative
        if not path.is_dir():
            if relative == Path("data/inbox"):
                issues.append(_issue("WARN", "data/inbox がありません。daily-review input 実行時に自動作成されます"))
            else:
                issues.append(_issue("ERROR", f"必要なディレクトリがありません: {relative}"))
        elif not os.access(path, os.W_OK):
            issues.append(_issue("WARN", f"書き込みを確認できません: {relative}"))
        else:
            checks.append(str(relative))
    for name in REQUIRED_TEMPLATE_NAMES:
        if not (root / "templates" / name).is_file():
            issues.append(_issue("ERROR", f"必要なテンプレートがありません: templates/{name}"))
    if all((root / "templates" / name).is_file() for name in REQUIRED_TEMPLATE_NAMES):
        checks.append("必須テンプレート")
    if week_range_for("2026-07-08") == ("2026-07-07", "2026-07-13"):
        checks.append("火曜始まりの週")
    else:
        issues.append(_issue("ERROR", "週の開始曜日が火曜日ではありません"))
    if __version__:
        checks.append(f"package version {__version__}")
    else:
        issues.append(_issue("ERROR", "package versionを取得できません"))
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
    inbox_dir = root / "data" / "inbox"
    if inbox_dir.is_dir():
        for path in sorted(inbox_dir.glob("*.json")):
            try:
                value = read_json_file(path)
                if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
                    raise ValueError("entriesがありません")
            except (OSError, ValueError) as exc:
                issues.append(_issue("ERROR", f"inbox JSONを読み込めません: {path.name} ({exc})"))
    return {"root": root, "daily_count": len(daily_files), "issues": issues, "checks": checks}
