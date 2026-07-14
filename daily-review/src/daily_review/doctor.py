from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import __version__
from .chat_schema import SCHEMA_VERSION
from .date_utils import week_range_for
from .handoff import HANDOFF_STATUSES, HANDOFF_VERSION, HandoffError, is_expired
from .goals import GoalError, load_goals, milestones_of, validate_goal
from .session import SESSION_STATUSES
from .storage import (
    CHAT_IMPORT_PROMPT_NAME,
    DATA_DIRS,
    REQUIRED_TEMPLATE_NAMES,
    priorities_path,
    read_json_file,
)
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
            elif relative == Path("data/drafts"):
                issues.append(_issue("WARN", "data/drafts がありません。daily-review organize 実行時に自動作成されます"))
            elif relative == Path("data/sessions"):
                issues.append(_issue("WARN", "data/sessions がありません。daily-review chat 実行時に自動作成されます"))
            elif relative == Path("data/handoffs"):
                issues.append(_issue("WARN", "data/handoffs がありません。daily-review handoff 実行時に自動作成されます"))
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
    chat_prompt = root / "templates" / CHAT_IMPORT_PROMPT_NAME
    if chat_prompt.is_file():
        try:
            prompt_text = chat_prompt.read_text(encoding="utf-8")
        except OSError as exc:
            issues.append(_issue("ERROR", f"ChatGPT取込テンプレートを読み込めません: {exc}"))
        else:
            required_prompt_fields = ('"schema_version": "1.0"', '"raw_text"', '"unclassified"')
            if all(field in prompt_text for field in required_prompt_fields):
                checks.append("chat import prompt")
            else:
                issues.append(_issue("ERROR", "chat import promptのschema_versionまたは必須変数が不足しています"))
    else:
        issues.append(_issue("WARN", f"ChatGPT取込テンプレートがありません: templates/{CHAT_IMPORT_PROMPT_NAME}（daily-review init で作成できます）"))
    if SCHEMA_VERSION == "1.0":
        checks.append("chat import schema")
    else:
        issues.append(_issue("ERROR", f"chat import schemaのバージョンが不正です: {SCHEMA_VERSION}"))
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
    drafts_dir = root / "data" / "drafts"
    draft_status_ok = True
    if drafts_dir.is_dir():
        for path in sorted(drafts_dir.glob("*.json")):
            try:
                value = read_json_file(path)
                if not isinstance(value, dict) or not isinstance(value.get("source_entry_ids"), list):
                    raise ValueError("source_entry_idsがありません")
            except (OSError, ValueError) as exc:
                issues.append(_issue("ERROR", f"draft JSONを読み込めません: {path.name} ({exc})"))
                draft_status_ok = False
                continue
            status = value.get("status")
            if status is None:
                issues.append(_issue("WARN", f"{path.name}: statusがありません（旧ドラフトとしてdraft扱いです）"))
            elif status not in {"draft", "approved"}:
                issues.append(_issue("ERROR", f"{path.name}: draft statusが不正です（{status}）"))
                draft_status_ok = False
            elif status == "approved":
                if not isinstance(value.get("approved_at"), str) or not value["approved_at"].strip():
                    issues.append(_issue("ERROR", f"{path.name}: approvedなのにapproved_atがありません"))
                    draft_status_ok = False
                approved_path = value.get("approved_daily_path")
                target = root / approved_path if isinstance(approved_path, str) else None
                if not approved_path or not target or not target.is_file():
                    issues.append(_issue("ERROR", f"{path.name}: approvedなのに確定日次ファイルがありません"))
                    draft_status_ok = False
            elif value.get("approved_at") or value.get("approved_daily_path"):
                issues.append(_issue("WARN", f"{path.name}: draftなのに承認情報が残っています"))
            for field in ("today.main_candidates", "tomorrow.main_candidates"):
                group, key = field.split(".", 1)
                values = (value.get(group) or {}).get(key)
                if isinstance(values, list) and len(values) > 3:
                    issues.append(_issue("ERROR", f"{path.name}: {field} は最大3件です"))
                    draft_status_ok = False
    if drafts_dir.is_dir() and draft_status_ok:
        checks.append("drafts status")
    sessions_dir = root / "data" / "sessions"
    sessions_ok = sessions_dir.is_dir()
    if sessions_dir.is_dir():
        for path in sorted(sessions_dir.glob("*.json")):
            try:
                value = read_json_file(path)
                if not isinstance(value, dict) or not isinstance(value.get("date"), str):
                    raise ValueError("dateがありません")
                status = value.get("status")
                if status not in SESSION_STATUSES:
                    raise ValueError("statusが不正です")
            except (OSError, ValueError) as exc:
                issues.append(_issue("WARN", f"chat sessionを読み込めません: {path.name} ({exc})"))
                sessions_ok = False
                continue
            if status == "approved" and not (root / "data" / "daily" / f"{value['date']}.json").is_file():
                issues.append(_issue("WARN", f"{path.name}: approvedなのに日次データがありません"))
                sessions_ok = False
            if status == "draft" and not (root / "data" / "drafts" / f"{value['date']}.json").is_file():
                issues.append(_issue("WARN", f"{path.name}: draftなのにドラフトがありません"))
                sessions_ok = False
    if sessions_ok:
        checks.append("chat sessions")

    handoffs_dir = root / "data" / "handoffs"
    handoffs_ok = handoffs_dir.is_dir()
    handoff_consistent = handoffs_ok
    if handoffs_dir.is_dir():
        seen_ids: set[str] = set()
        seen_hashes: set[str] = set()
        for path in sorted(handoffs_dir.glob("*.json")):
            try:
                value = read_json_file(path)
                if not isinstance(value, dict) or not isinstance(value.get("date"), str) or not isinstance(value.get("handoffs"), list):
                    raise ValueError("dateまたはhandoffsが不正です")
            except (OSError, ValueError) as exc:
                issues.append(_issue("ERROR", f"handoff JSONを読み込めません: {path.name} ({exc})"))
                handoffs_ok = handoff_consistent = False
                continue
            for item in value["handoffs"]:
                if not isinstance(item, dict):
                    issues.append(_issue("ERROR", f"{path.name}: handoff項目が不正です")); handoffs_ok = False; continue
                session_id = item.get("session_id")
                status = item.get("status")
                if not isinstance(session_id, str) or not session_id or session_id in seen_ids:
                    issues.append(_issue("ERROR", f"{path.name}: session_idが不正または重複しています")); handoffs_ok = False
                else:
                    seen_ids.add(session_id)
                if status not in HANDOFF_STATUSES:
                    issues.append(_issue("ERROR", f"{path.name}: handoff statusが不正です")); handoffs_ok = False
                if not isinstance(item.get("prompt_hash"), str) or not item["prompt_hash"].startswith("sha256:"):
                    issues.append(_issue("ERROR", f"{path.name}: prompt_hashがありません")); handoffs_ok = False
                content_hash = item.get("import_hash")
                if isinstance(content_hash, str):
                    if content_hash in seen_hashes:
                        issues.append(_issue("ERROR", f"{path.name}: import_hashが重複しています")); handoffs_ok = False
                    seen_hashes.add(content_hash)
                if status == "approved" and not (root / "data" / "daily" / f"{value['date']}.json").is_file():
                    issues.append(_issue("ERROR", f"{path.name}: approvedなのに日次データがありません")); handoffs_ok = False
                if status == "received" and not (root / "data" / "drafts" / f"{value['date']}.json").is_file():
                    issues.append(_issue("WARN", f"{path.name}: receivedなのにドラフトがありません")); handoff_consistent = False
                if status == "issued":
                    try:
                        if is_expired(item):
                            issues.append(_issue("WARN", f"{path.name}: issued handoffが期限切れです")); handoff_consistent = False
                    except HandoffError as exc:
                        issues.append(_issue("ERROR", f"{path.name}: {exc}")); handoffs_ok = False
                session_file = root / "data" / "sessions" / f"{value['date']}.json"
                if session_file.is_file():
                    try:
                        session_value = read_json_file(session_file)
                        expected_status = {"issued": "waiting_for_chatgpt", "received": "draft", "approved": "approved"}.get(status)
                        if (
                            isinstance(session_value, dict)
                            and session_value.get("handoff_session_id") == session_id
                            and expected_status
                            and session_value.get("status") != expected_status
                        ):
                            issues.append(_issue("WARN", f"{path.name}: handoffとsessionのstatusが不整合です")); handoff_consistent = False
                    except (OSError, ValueError):
                        issues.append(_issue("WARN", f"{path.name}: 対応するchat sessionを読み込めません")); handoff_consistent = False
    if handoffs_ok:
        checks.append("handoffs")
    if HANDOFF_VERSION == "1.0":
        checks.append("handoff schema")
    else:
        issues.append(_issue("ERROR", f"handoff schemaのバージョンが不正です: {HANDOFF_VERSION}"))
    if handoff_consistent:
        checks.append("handoff-session consistency")

    priority_file = priorities_path(root)
    if not priority_file.is_file():
        issues.append(_issue("WARN", "優先順位設定がありません: config/priorities.json（daily-review init で作成できます）"))
    else:
        try:
            priority_data = read_json_file(priority_file)
            priorities = priority_data.get("priorities") if isinstance(priority_data, dict) else None
            if not isinstance(priorities, list) or not all(isinstance(item, str) and item.strip() for item in priorities):
                raise ValueError("prioritiesは空でない文字列の配列にしてください")
            if len(priorities) != len(set(priorities)):
                raise ValueError("prioritiesに重複があります")
        except (OSError, ValueError) as exc:
            issues.append(_issue("ERROR", f"優先順位設定が不正です: {exc}"))
        else:
            checks.append("priorities config")

    goal_errors = False
    try:
        goals = load_goals(root)
    except (OSError, ValueError, GoalError) as exc:
        issues.append(_issue("ERROR", f"goal JSONを読み込めません: {exc}"))
        goal_errors = True
        goals = []
    for goal in goals:
        try:
            validate_goal(goal, goals)
        except (GoalError, ValueError) as exc:
            goal_errors = True
            issues.append(_issue("ERROR", f"goal {goal.get('id', '不明')}: {exc}"))
    if not goal_errors:
        checks.append("goals schema")
        checks.append("goal relationships")
        checks.append("goal metrics")
        checks.append("goal milestones")
        checks.append("milestone dependencies")
        checks.append("goal steps")
        for goal in goals:
            for milestone in milestones_of(goal):
                pending = [step for step in milestone.get("steps") or [] if step.get("status") not in {"done", "cancelled"}]
                if milestone.get("status") == "completed" and pending:
                    issues.append(_issue("WARN", f"goal {goal['id']}: completedマイルストーンに未完了ステップがあります"))
        checks.append("roadmap consistency")
    if (root / "data" / "goals" / "items").is_dir():
        checks.append("goals directory")

    try:
        source_root = Path(__file__).resolve().parents[2]
        gitignore = (source_root / ".gitignore").read_text(encoding="utf-8")
        if all(value in gitignore for value in ("data/", "logs/", "config/priorities.json")):
            checks.append("runtime data ignored by git")
        else:
            issues.append(_issue("WARN", "実行時データまたは優先順位設定のGit除外を確認できません"))
    except OSError:
        issues.append(_issue("WARN", ".gitignoreを読み込めません"))
    return {"root": root, "daily_count": len(daily_files), "issues": issues, "checks": checks}
