from __future__ import annotations

import json
import hashlib
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from . import __version__
from .archive import (
    create_backup,
    delete_backup_files,
    inspect_backup,
    list_backups,
    plan_backup,
    prune_backups,
    restore_backup,
    verify_backup,
)
from .chat_import import backup_unapproved_draft, build_draft as build_chat_import_draft, import_hash
from .chat_schema import ChatSchemaError, extract_json as extract_chat_json, validate_payload as validate_chat_payload
from .chat_workflow import build_dynamic_prompt, chat_home_next_command, load_priorities, workflow_state
from .date_utils import month_range_for, parse_date, today_string, tomorrow_of, week_range_for
from .handoff import (
    HandoffError, cancel_handoff, current_handoff_state, is_expired, issue_handoff, list_handoffs, render_handoff, update_handoff,
)
from .markdown import render_daily, render_monthly, render_weekly
from .models import Plan, ProposalInput, ReviewInput, TaskResultsInput, dump_model, now_iso
from .storage import (
    atomic_write_json_many,
    atomic_write_json_data,
    CHAT_IMPORT_PROMPT_NAME,
    DEFAULT_PRIORITIES,
    daily_path,
    daily_log_path,
    draft_path,
    init_workspace,
    inbox_path,
    load_daily,
    load_or_create_daily,
    read_json_file,
    resolve_root,
    save_daily,
    weekly_log_path,
    weekly_path,
    monthly_log_path,
    monthly_path,
    write_text,
)
from .validation import ValidationResult, validate_daily, validate_plan
from .weekly import build_weekly_summary
from .reporting import build_report, weekly_trends
from .receive import prepare_receive
from .doctor import run_doctor
from .migration import apply_migration, is_migrated, migration_plan
from .dashboard import build_daily_summary, home_next_command, next_action_kind, next_command
from .organizer import EDITABLE_DRAFT_FIELDS, load_draft, organize_day, organize_entries
from .draft_workflow import approve_draft, build_daily_from_draft, replace_draft_fields
from .goals import (
    GOAL_LEVELS, GOAL_STATUSES, MILESTONE_STATUSES, STEP_STATUSES, GoalError, add_milestone,
    add_step, archive_goal, children_of, edit_goal, find_milestone, find_step, goal_progress,
    goal_summary as build_goal_summary, load_goal, load_goals, milestone_progress, milestone_warnings,
    milestones_of, new_goal, new_milestone, new_step, next_goal_action, parse_metric, reorder_milestone, reorder_step,
    save_goal, set_goal_status, step_warnings, update_milestone, update_step, validate_goal,
)
from .planning import (
    MAX_MAIN_CANDIDATES, PlanningError, apply_step_updates, approve_plan as approve_goal_plan,
    daily_plan_path, generate_daily_plan, generate_weekly_plan, load_daily_plan, load_weekly_plan,
    save_daily_plan, save_weekly_plan, update_daily_plan, update_weekly_plan, weekly_plan_path,
)
from .evaluation import (
    EvaluationError, approve_evaluation, generate_monthly_evaluation, generate_weekly_evaluation,
    load_monthly_evaluation, load_weekly_evaluation, save_evaluation,
)
from .replan import (
    ReplanError, apply_replan, cancel_replan, edit_replan, generate_replan, list_replans,
    load_replan, save_replan,
)
from .goal_coach import GoalCoachError, build_coach_prompt, receive_coach_payload
from .goal_design import (
    GoalDesignError, apply_design, create_design, load_design, receive_proposal,
    render_prompt as render_goal_design_prompt, save_answer,
)
from .session import prompt_hash, save_session
from .v11_check import collect_v11_checks, repository_root
from .v12_check import collect_v12_checks
from .task_service import TaskQueryError, query_tasks
from .quick_review import QuickReviewError, build_quick_entry, normalize_payload as normalize_quick_payload, save_quick_review
from .csv_export import ExportError, export_csv, period_range
from .notifications import (
    ConsoleSender, FileSender, NotificationError, dispatch_notifications, evaluate_notifications,
    load_history as load_notification_history, load_notification_config, parse_current,
)
from .command_api import CommandExecutor, load_audit_history
from .command_models import COMMAND_MODELS, ApiIssue, CommandRequest, CommandResponse
from .review_normalizer import NormalizationError, normalize_review
from .recovery import apply_restore, preview_restore, restore_history
from .rollover import apply_rollover, preview_rollover, rollover_history
from .integrity import (
    apply_integrity_repair,
    preview_integrity_repair,
    repair_history,
    run_integrity_check,
)
from .operation_lock import OperationLockedError


app = typer.Typer(
    help="毎日の振り返りと明日の指示書をローカル保存するCLIです。",
    no_args_is_help=True,
    invoke_without_command=True,
)
goal_app = typer.Typer(help="定性・定量指標を持つ目標を安全に管理します。")
milestone_app = typer.Typer(help="目標を期限付きマイルストーンへ分解します。")
step_app = typer.Typer(help="マイルストーンの具体的な実行ステップを管理します。")
plan_app = typer.Typer(help="目標由来の週次・日次計画を確認して承認します。")
evaluate_app = typer.Typer(help="目標を週次・月次で評価します。")
replan_app = typer.Typer(help="評価に基づく計画修正案を確認して適用します。", invoke_without_command=True)
design_app = typer.Typer(help="ChatGPTと往復する目標設計セッションを管理します。", invoke_without_command=True)
tasks_app = typer.Typer(help="日次指示書と目標計画のタスクを一覧表示します。")
export_app = typer.Typer(help="保存済みデータを分析用ファイルへ出力します。")
notifications_app = typer.Typer(help="通知候補の判定、送信、履歴確認を行います。")
api_app = typer.Typer(help="ChatGPTや外部プログラム向けのversioned JSON Command APIです。")
parse_app = typer.Typer(help="自然言語を安全な構造へルールベースで正規化します。")
backup_app = typer.Typer(help="検証可能なZIPバックアップを作成・管理します。", invoke_without_command=True)
rollover_app = typer.Typer(help="未完了タスクを複製せず翌日の計画へ引き継ぎます。")
doctor_app = typer.Typer(help="保存構造の点検と安全な修復を行います。", invoke_without_command=True)
app.add_typer(goal_app, name="goal")
app.add_typer(plan_app, name="plan")
goal_app.add_typer(milestone_app, name="milestone")
milestone_app.add_typer(step_app, name="step")
goal_app.add_typer(evaluate_app, name="evaluate")
goal_app.add_typer(replan_app, name="replan")
goal_app.add_typer(design_app, name="design")
app.add_typer(tasks_app, name="tasks")
app.add_typer(export_app, name="export")
app.add_typer(notifications_app, name="notifications")
app.add_typer(api_app, name="api")
app.add_typer(parse_app, name="parse")
app.add_typer(backup_app, name="backup")
app.add_typer(rollover_app, name="rollover")
app.add_typer(doctor_app, name="doctor")


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", help="バージョンを表示して終了します。", is_eager=True),
) -> None:
    if version:
        typer.echo(f"daily-review {__version__}")
        raise typer.Exit()


RootOption = typer.Option(None, "--root", help="保存先ルート。未指定ならカレントディレクトリです。")
DateOption = typer.Option(None, "--date", help="対象日（YYYY-MM-DD）。未指定なら今日です。")

TASK_STATUS_LABELS = {
    "completed": "完了",
    "partial": "一部進んだ",
    "minimum_only": "最低ラインのみ",
    "not_started": "未着手",
    "skipped": "意図的に見送り",
}
CARRYOVER_STATUSES = {"partial", "minimum_only", "not_started"}


def _root(path: Path | None) -> Path:
    return resolve_root(path)


def _metadata_version() -> str | None:
    """Read this source checkout's metadata before unrelated editable installs."""
    package_root = Path(__file__).resolve().parents[1]
    pkg_info = package_root / "daily_review.egg-info" / "PKG-INFO"
    if pkg_info.is_file():
        for line in pkg_info.read_text(encoding="utf-8").splitlines():
            if line.startswith("Version: "):
                value = line.removeprefix("Version: ")
                # Editable source checkouts can retain stale egg-info until a
                # build is performed.  The pyproject version is dynamic, so
                # the package source is authoritative in that situation.
                return __version__ if value != __version__ else value
    try:
        return package_version("daily-review")
    except PackageNotFoundError:
        return __version__


def _day(value: str | None) -> str:
    if value is None:
        return today_string()
    try:
        parse_date(value)
    except ValueError as exc:
        raise typer.BadParameter(
            "日付は YYYY-MM-DD 形式で指定してください。例: daily-review summary --date 2026-07-14"
        ) from exc
    return value


def _read_text_from_file_or_stdin(file: Path | None) -> str:
    if file:
        return file.read_text(encoding="utf-8")
    typer.echo("入力を貼り付けてください。終わったら Ctrl-D で保存します。", err=True)
    return sys.stdin.read()


def _stdin_is_piped() -> bool:
    try:
        return not sys.stdin.isatty()
    except (AttributeError, OSError):
        return False


def _read_natural_input(text: str | None, clipboard: bool) -> tuple[str, str]:
    if text is not None and clipboard:
        raise typer.BadParameter("--text と --clipboard は同時に使用できません")
    if text is not None:
        if _stdin_is_piped() and sys.stdin.read():
            raise typer.BadParameter("--text、--clipboard、標準入力は同時に使用できません")
        return text, "text"
    if clipboard:
        if _stdin_is_piped() and sys.stdin.read():
            raise typer.BadParameter("--text、--clipboard、標準入力は同時に使用できません")
        return _read_clipboard_text(), "clipboard"
    if _stdin_is_piped():
        return sys.stdin.read(), "stdin"
    typer.echo("入力を貼り付けてください。終わったら Ctrl-D で保存します。", err=True)
    return sys.stdin.read(), "interactive"


def _input_error(message: str) -> None:
    typer.echo(f"ERROR: {message}", err=True)
    raise typer.Exit(code=2)


def _read_json_from_file_or_stdin(file: Path | None) -> dict[str, Any]:
    source = str(file) if file else "標準入力"
    text = file.read_text(encoding="utf-8") if file else _read_stdin_json_text()
    return _parse_json_text(text, source)


def _read_stdin_json_text() -> str:
    typer.echo("JSONを貼り付けてください。終わったら Ctrl-D で保存します。", err=True)
    return sys.stdin.read()


def _read_clipboard_text() -> str:
    if platform.system() != "Darwin":
        raise typer.BadParameter("クリップボード入力はmacOSのみ対応しています。--file または標準入力を使ってください。")
    try:
        result = subprocess.run(["pbpaste"], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise typer.BadParameter("クリップボードを読み取れませんでした。--file または標準入力を使ってください。") from exc
    if not result.stdout.strip():
        raise typer.BadParameter("クリップボードが空です。ChatGPTのJSONをコピーしてから再実行してください。")
    return result.stdout


def _strip_single_json_code_block(text: str, source: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        if "```" in stripped and not stripped.startswith(("{", "[")):
            raise typer.BadParameter(
                f"{source} はJSONコードブロック以外の説明文を含んでいます。JSONだけをコピーしてください。"
            )
        return text
    lines = stripped.splitlines()
    fence_indexes = [index for index, line in enumerate(lines) if line.strip().startswith("```")]
    if len(fence_indexes) != 2 or fence_indexes[0] != 0 or fence_indexes[1] != len(lines) - 1:
        raise typer.BadParameter(
            f"{source} はJSONコードブロック以外の説明文、または複数のコードブロックを含んでいます。JSONだけをコピーしてください。"
        )
    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        raise typer.BadParameter(f"{source} のコードブロック種別はjsonだけ対応しています。")
    return "\n".join(lines[1:-1])


def _parse_json_text(text: str, source: str) -> dict[str, Any]:
    cleaned = _strip_single_json_code_block(text, source)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            f"JSONの形式が不正です: {source} の {exc.lineno}行{exc.colno}列付近（{exc.msg}）"
        ) from exc


def _read_json_for_command(file: Path | None, clipboard: bool) -> dict[str, Any]:
    if file and clipboard:
        raise typer.BadParameter("--file と --clipboard は同時に指定できません。どちらか一方だけ使ってください。")
    if clipboard:
        return _parse_json_text(_read_clipboard_text(), "クリップボード")
    return _read_json_from_file_or_stdin(file)


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["入力内容に問題があります。"]
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        lines.append(f"- {loc}: {error['msg']}")
    return "\n".join(lines)


def _regenerate_daily_markdown(root: Path, day: str, entry: dict[str, Any]) -> Path:
    return write_text(daily_log_path(root, day), render_daily(entry))


def _print_validation(result: ValidationResult) -> None:
    if not result.has_errors:
        typer.echo("検証結果: エラーはありません")
    else:
        typer.echo("検証結果: エラーがあります")
    typer.echo("OK")
    for item in result.ok or ["なし"]:
        typer.echo(f"- {item}")
    typer.echo("警告")
    for item in result.warnings or ["なし"]:
        typer.echo(f"- {item}")
    typer.echo("エラー")
    for item in result.errors or ["なし"]:
        typer.echo(f"- {item}")


def _print_save_error(problem: str, fix: str = "JSONを修正してから、もう一度dry-runしてください。") -> None:
    typer.echo("保存できませんでした。", err=True)
    typer.echo("問題:", err=True)
    typer.echo(f"- {problem}", err=True)
    typer.echo("修正:", err=True)
    typer.echo(f"- {fix}", err=True)
    typer.echo("既存データは変更されていません。", err=True)


def _ensure_task_ids(plan: dict[str, Any]) -> bool:
    changed = False
    tasks = plan.get("tasks") or []
    used = {str(task.get("id")) for task in tasks if task.get("id")}
    next_index = 1
    for task in tasks:
        if task.get("id"):
            continue
        while f"task-{next_index}" in used:
            next_index += 1
        task_id = f"task-{next_index}"
        task["id"] = task_id
        used.add(task_id)
        changed = True
        next_index += 1
    return changed


def _task_result_label(status: str | None) -> str:
    if status is None:
        return "未記録"
    return TASK_STATUS_LABELS.get(status, status)


def _minimum_label(value: bool | None) -> str:
    if value is None:
        return "未記録"
    return "達成" if value else "未達"


def _result_map(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {result["task_id"]: result for result in entry.get("task_results") or []}


def _clean_proposal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    proposal = dict(payload)
    proposal.pop("status", None)
    proposal.pop("approved_at", None)
    return proposal


def _resolve_night_date(cli_date: str | None, payload: dict[str, Any]) -> str:
    json_date = payload.get("date")
    if cli_date and json_date and cli_date != json_date:
        raise typer.BadParameter(f"--date とJSON内のdateが一致しません（--date: {cli_date}, JSON: {json_date}）")
    day = cli_date or json_date or today_string()
    try:
        parse_date(day)
    except ValueError as exc:
        raise typer.BadParameter(f"dateはYYYY-MM-DD形式にしてください（現在: {day}）") from exc
    return day


def _build_pending_plan(payload: dict[str, Any], day: str) -> tuple[dict[str, Any], ValidationResult]:
    try:
        proposal_input = ProposalInput.model_validate(_clean_proposal_payload(payload))
    except ValidationError as exc:
        raise typer.BadParameter(_format_validation_error(exc)) from exc

    plan = Plan(
        **dump_model(proposal_input),
        status="pending_review",
    )
    plan_payload = dump_model(plan)
    _ensure_task_ids(plan_payload)
    result = validate_plan(plan_payload, day, final=False)
    if plan_payload.get("status") == "pending_review":
        result.ok.append("提案版: statusはpending_reviewです")
    else:
        result.errors.append("提案版: statusはpending_reviewにしてください")
    return plan_payload, result


def _print_plan_summary(title: str, plan: dict[str, Any], status_label: str) -> None:
    typer.echo(f"{title}｜{plan.get('target_date', '未保存')}")
    typer.echo(f"状態: {status_label}")
    typer.echo("Main")
    for index, item in enumerate(plan.get("main") or [], start=1):
        typer.echo(f"{index}. {item}")
    typer.echo("優先タスク")
    for index, task in enumerate(plan.get("tasks") or [], start=1):
        typer.echo(f"{index}. [{task.get('area')}] {task.get('task')}")
        typer.echo(f"   最低ライン: {task.get('minimum_line')}")
    typer.echo("明日変えること")
    typer.echo(plan.get("one_change_tomorrow", "未保存"))


def _print_task_results(target: str, entry: dict[str, Any], plan: dict[str, Any]) -> None:
    results = _result_map(entry)
    tasks = plan.get("tasks") or []
    completed = 0
    minimum_achieved = 0
    unrecorded = 0
    typer.echo(f"実行結果｜{target}")
    for index, task in enumerate(tasks, start=1):
        result = results.get(task.get("id"))
        if result:
            if result.get("status") == "completed":
                completed += 1
            if result.get("minimum_line_achieved"):
                minimum_achieved += 1
        else:
            unrecorded += 1
        typer.echo(f"{index}. [{task.get('area')}] {task.get('task')}")
        typer.echo(f"   結果: {_task_result_label(result.get('status') if result else None)}")
        typer.echo(f"   最低ライン: {_minimum_label(result.get('minimum_line_achieved') if result else None)}")
        note = (result or {}).get("note")
        if note:
            typer.echo(f"   メモ: {note}")
    total = len(tasks)
    typer.echo(f"通常タスク完了: {completed}/{total}")
    typer.echo(f"最低ライン達成: {minimum_achieved}/{total}")
    typer.echo(f"未記録: {unrecorded}件")


def _find_final_entry_by_target(root: Path, target_date: str) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    daily_dir = root / "data" / "daily"
    if not daily_dir.exists():
        return None, None, None
    for path in sorted(daily_dir.glob("*.json")):
        entry = read_json_file(path)
        final = entry.get("tomorrow_plan_final")
        if final and final.get("target_date") == target_date:
            return entry.get("date", path.stem), entry, final
    return None, None, None


def _has_pending_by_target(root: Path, target_date: str) -> bool:
    daily_dir = root / "data" / "daily"
    if not daily_dir.exists():
        return False
    for path in sorted(daily_dir.glob("*.json")):
        entry = read_json_file(path)
        proposal = entry.get("tomorrow_plan_proposal")
        if proposal and proposal.get("target_date") == target_date:
            return True
    return False


def _parse_task_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        parsed = TaskResultsInput.model_validate(payload)
    except ValidationError as exc:
        raise typer.BadParameter(_format_validation_error(exc)) from exc
    return [dump_model(result) for result in parsed.task_results]


def _validate_task_results_payload(results: list[dict[str, Any]], plan: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    task_ids = [str(task.get("id")) for task in plan.get("tasks") or [] if task.get("id")]
    task_id_set = set(task_ids)
    if len(task_ids) != len(task_id_set):
        errors.append("確定版のタスクIDが重複しています")

    input_ids = [result.get("task_id") for result in results]
    duplicated = sorted({task_id for task_id in input_ids if input_ids.count(task_id) > 1})
    if duplicated:
        errors.append(f"同じtask_idが複数あります（{', '.join(duplicated)}）")
    for result in results:
        task_id = result.get("task_id")
        if task_id not in task_id_set:
            errors.append(f"存在しないtask_idです: {task_id}")
        status = result.get("status")
        achieved = result.get("minimum_line_achieved")
        if status == "completed" and achieved is False:
            warnings.append(f"{task_id}: 完了ですが最低ライン未達になっています")
        if status == "minimum_only" and achieved is False:
            warnings.append(f"{task_id}: 最低ラインのみですが最低ライン未達になっています")
        if status == "not_started" and achieved is True:
            warnings.append(f"{task_id}: 未着手ですが最低ライン達成になっています")
    return errors, warnings


def _merge_task_results(existing: list[dict[str, Any]], updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {result["task_id"]: dict(result) for result in existing}
    timestamp = now_iso()
    for result in updates:
        updated = dict(result)
        updated["recorded_at"] = timestamp
        merged[updated["task_id"]] = updated
    return list(merged.values())


def _stamp_entry_for_write(day: str, entry: dict[str, Any]) -> dict[str, Any]:
    stamped = dict(entry)
    timestamp = now_iso()
    stamped["date"] = day
    stamped["updated_at"] = timestamp
    stamped.setdefault("created_at", timestamp)
    return stamped


def _validate_night_review_payload(payload: dict[str, Any]) -> tuple[str, ReviewInput]:
    raw_log = payload.get("raw_log")
    if not isinstance(raw_log, str) or not raw_log.strip():
        raise typer.BadParameter("raw_logは空でない文字列にしてください。")
    if payload.get("structured_review") is None:
        raise typer.BadParameter("structured_reviewがありません。")
    try:
        review_input = ReviewInput.normalize_payload(
            {
                "diary": payload.get("diary"),
                "structured_review": payload.get("structured_review"),
            }
        )
    except ValidationError as exc:
        raise typer.BadParameter(_format_validation_error(exc)) from exc
    if review_input.structured_review is None:
        raise typer.BadParameter("structured_reviewがありません。")
    return raw_log, review_input


def _prepare_close_day(
    root: Path,
    day: str,
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str], int, str | None]:
    warnings: list[str] = []
    raw_log, review_input = _validate_night_review_payload(payload)
    proposal_payload = payload.get("tomorrow_plan_proposal")
    if proposal_payload is None:
        raise typer.BadParameter("tomorrow_plan_proposalがありません。")
    if not isinstance(proposal_payload, dict):
        raise typer.BadParameter("tomorrow_plan_proposalはJSONオブジェクトにしてください。")
    plan_payload, plan_result = _build_pending_plan(proposal_payload, day)
    if plan_result.has_errors:
        raise typer.BadParameter(" / ".join(plan_result.errors))
    warnings.extend(plan_result.warnings)

    entries_by_day: dict[str, dict[str, Any]] = {}
    saved_result_count = 0
    result_source_day: str | None = None
    has_task_results = "task_results" in payload
    raw_task_results = payload.get("task_results")
    if has_task_results and raw_task_results:
        source_day, source_entry, source_plan = _find_final_entry_by_target(root, day)
        if not source_entry or not source_plan or not source_day:
            if _has_pending_by_target(root, day):
                raise typer.BadParameter(f"{day} は提案版のみです。承認後にclose-dayを実行してください。")
            raise typer.BadParameter(f"{day} を対象にした確定版指示書がありません。")
        _ensure_task_ids(source_plan)
        updates = _parse_task_results({"task_results": raw_task_results})
        errors, result_warnings = _validate_task_results_payload(updates, source_plan)
        if errors:
            raise typer.BadParameter(" / ".join(errors))
        warnings.extend(result_warnings)
        source_entry["tomorrow_plan_final"] = source_plan
        source_entry["task_results"] = _merge_task_results(source_entry.get("task_results") or [], updates)
        entries_by_day[source_day] = source_entry
        saved_result_count = len(updates)
        result_source_day = source_day
    elif has_task_results:
        warnings.append("当日のタスク結果は保存されませんでした（task_resultsが空です）。")
    else:
        warnings.append("当日のタスク結果は保存されていません。")

    current_entry = entries_by_day.get(day) or load_or_create_daily(root, day)
    current_entry["raw_log"] = raw_log
    if review_input.diary is not None:
        current_entry["diary"] = review_input.diary
    current_entry["structured_review"] = dump_model(review_input.structured_review)
    current_entry["tomorrow_plan_proposal"] = plan_payload
    entries_by_day[day] = current_entry
    return entries_by_day, warnings, saved_result_count, result_source_day


def _print_close_day_dry_run(
    root: Path,
    day: str,
    entries_by_day: dict[str, dict[str, Any]],
    result_count: int,
    warnings: list[str],
) -> None:
    typer.echo(f"保存前確認｜{day}")
    typer.echo("更新予定")
    for entry_day in sorted(entries_by_day):
        path = daily_path(root, entry_day).relative_to(root)
        typer.echo(f"- {path}")
        if entry_day != day:
            typer.echo(f"  タスク結果: {result_count}件")
        else:
            typer.echo("  生ログ・日記・整形ログ・翌日提案")
    target = (entries_by_day[day].get("tomorrow_plan_proposal") or {}).get("target_date", "-")
    typer.echo(f"翌日提案の対象日: {target}")
    typer.echo("エラー: 0件")
    typer.echo(f"警告: {len(warnings)}件")
    for warning in warnings:
        typer.echo(f"- {warning}")
    typer.echo("dry-runのため保存していません。")


def _print_close_day_summary(
    day: str,
    entry: dict[str, Any],
    result_count: int,
    carryover_count: int,
    warnings: list[str],
) -> None:
    final = entry.get("tomorrow_plan_final")
    typer.echo(f"夜の記録を保存しました｜{day}")
    typer.echo(f"当日のタスク結果   {result_count}件保存")
    typer.echo(f"生ログ             {_saved_label(entry.get('raw_log'))}")
    typer.echo(f"日記               {_saved_label(entry.get('diary'))}")
    typer.echo(f"整形ログ           {_saved_label(entry.get('structured_review'))}")
    typer.echo(f"翌日の提案版       {_saved_label(entry.get('tomorrow_plan_proposal'))}")
    typer.echo(f"翌日の確定版       {'承認済み' if final and final.get('status') == 'approved' else '未承認'}")
    proposal = entry.get("tomorrow_plan_proposal") or {}
    typer.echo(f"対象日             {proposal.get('target_date', '-')}")
    typer.echo("次:")
    typer.echo(f"daily-review show-proposal --date {day}")
    if warnings:
        typer.echo("警告")
        for warning in warnings:
            typer.echo(f"- {warning}")
    if carryover_count:
        typer.echo(f"引き継ぎ候補: {carryover_count}件")
        typer.echo(f"daily-review carryover --date {day}")


def _print_night_summary(day: str, entry: dict[str, Any], warnings: list[str]) -> None:
    final = entry.get("tomorrow_plan_final")
    typer.echo(f"夜の振り返りを保存しました｜{day}")
    typer.echo(f"生ログ       {_saved_label(entry.get('raw_log'))}")
    typer.echo(f"日記         {_saved_label(entry.get('diary'))}")
    typer.echo(f"整形ログ     {_saved_label(entry.get('structured_review'))}")
    typer.echo(f"提案版       {_saved_label(entry.get('tomorrow_plan_proposal'))}")
    typer.echo(f"確定版       {'承認済み' if final and final.get('status') == 'approved' else '未承認'}")
    typer.echo(f"対象日       {_target_date_for_entry(entry)}")
    typer.echo("次:")
    typer.echo(f"daily-review approve-plan --date {day}")
    if warnings:
        typer.echo("警告")
        for warning in warnings:
            typer.echo(f"- {warning}")


@app.command()
def init(root: Path | None = RootOption) -> None:
    """必要なディレクトリとテンプレートを作成します。"""
    base = _root(root)
    created, existing = init_workspace(base)
    typer.echo(f"初期化先: {base}")
    typer.echo("作成したもの")
    for path in created or ["なし"]:
        typer.echo(f"- {path}")
    typer.echo("既存だったもの")
    for path in existing or ["なし"]:
        typer.echo(f"- {path}")


@app.command()
def migrate(
    root: Path | None = RootOption,
    dry_run: bool = typer.Option(False, "--dry-run", help="変更予定だけを表示します。"),
    json_output: bool = typer.Option(False, "--json", help="結果をJSONで表示します。"),
    yes: bool = typer.Option(False, "--yes", help="確認なしで移行を実行します。"),
) -> None:
    """既存の保存先に不足しているv1.1/v1.2用ファイルだけを追加します。"""
    base = _root(root)
    try:
        already_migrated = is_migrated(base)
        plan = migration_plan(base)
    except (OSError, ValueError) as exc:
        typer.echo(f"ERROR: 移行状態を読み込めません: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    if dry_run:
        payload = {"root": str(base), "dry_run": True, "already_migrated": already_migrated, "plan": plan}
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        typer.echo("daily-review migrate｜既存環境 → v1.2基盤｜dry-run")
        typer.echo("確認結果:")
        for item in plan:
            label = "作成予定" if item["action"] == "create" else "既存"
            typer.echo(f"- {item['path']}: {label}")
        typer.echo("- 日次データ: 変更なし")
        typer.echo("- 週次データ: 変更なし")
        typer.echo("- 月次データ: 変更なし")
        typer.echo("保存は行いませんでした")
        return

    if already_migrated:
        payload = {"root": str(base), "already_migrated": True, "changes": []}
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            typer.echo("この環境はすでに必要な移行を完了しています")
            typer.echo("変更はありません")
        return
    if not yes and not typer.confirm("移行を実行しますか？", default=False):
        typer.echo("移行を中止しました")
        raise typer.Exit(code=2)
    try:
        result = apply_migration(base)
    except (OSError, ValueError) as exc:
        typer.echo(f"ERROR: 移行に失敗しました: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    payload = {"root": str(base), **result, "existing_data_changed": 0}
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo("必要な移行が完了しました")
    typer.echo(f"変更: {len(result['changes'])}件")
    typer.echo(f"スキップ: {len(result['skipped'])}件")
    typer.echo("既存データ変更: 0件")
    typer.echo("次の操作:")
    typer.echo("daily-review v11-check")


@app.command("v11-check")
def v11_check(
    root: Path | None = RootOption,
    verbose: bool = typer.Option(False, "--verbose", help="doctorの警告も表示します。"),
    json_output: bool = typer.Option(False, "--json", help="結果をJSONで表示します。"),
) -> None:
    """v1.1の実運用準備を読み取り専用で確認します。"""
    report = collect_v11_checks(_root(root))
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"保存先ルート: {report['root']}")
        for item in report["checks"]:
            if item["level"] == "OK" or verbose or item["level"] == "ERROR":
                prefix = item["level"]
                detail = "" if item["level"] == "OK" else f": {item['message']}"
                typer.echo(f"{prefix:<5}{item['name']}{detail}")
        if verbose:
            for warning in report["doctor_warnings"]:
                typer.echo(f"WARNING {warning['message']}")
        if report["errors"]:
            typer.echo("daily-review v11-check: ERROR")
        else:
            typer.echo("daily-review v11-check: OK")
            typer.echo("v1.1 workflow is compatible")
    if report["errors"]:
        raise typer.Exit(code=5)


@app.command("v12-check")
def v12_check(
    root: Path | None = RootOption,
    verbose: bool = typer.Option(False, "--verbose", help="doctorの警告も表示します。"),
    json_output: bool = typer.Option(False, "--json", help="JSON以外を出力しません。"),
) -> None:
    """v1.2のデータ安全性と運用準備を読み取り専用で確認します。"""
    report = collect_v12_checks(_root(root))
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"保存先ルート: {report['root']}")
        for item in report["checks"]:
            if item["level"] == "OK" or verbose:
                detail = "" if item["level"] == "OK" else f": {item['message']}"
                typer.echo(f"{item['level']:<5}{item['name']}{detail}")
        if verbose:
            for warning in report["doctor_warnings"]:
                typer.echo(f"WARN {warning['message']}")
        if report["errors"]:
            typer.echo("daily-review v12-check: ERROR")
        else:
            typer.echo("daily-review v12-check: OK")
            typer.echo(f"v{report['version']} is ready" if report["version"] == "1.2.0" else "v1.2.0rc1 is ready for final operational testing")
    if report["errors"]:
        raise typer.Exit(code=5)


@tasks_app.command("list")
def tasks_list(
    status: str | None = typer.Option(None, "--status", help="pending / completed / partial / minimum_only / not_started / skipped"),
    priority: str | None = typer.Option(None, "--priority", help="high / medium / low"),
    category: str | None = typer.Option(None, "--category", help="カテゴリの完全一致"),
    due: str | None = typer.Option(None, "--due", help="today / tomorrow / overdue"),
    main_only: bool = typer.Option(False, "--main", help="Main候補だけ表示"),
    minimum_only: bool = typer.Option(False, "--minimum", help="最低限ライン付きだけ表示"),
    all_items: bool = typer.Option(False, "--all", help="完了済みを含める"),
    output_format: str = typer.Option("text", "--format", help="text または json"),
    detail: bool = typer.Option(False, "--detail", help="作成日・更新日・参照元も表示"),
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """日次指示書と目標計画から、実行判断に必要なタスクを一覧表示します。"""
    if output_format not in {"text", "json"}:
        _goal_error("--format は text または json にしてください")
    day = _day(date)
    try:
        values = query_tasks(
            _root(root), today=day, status=status, priority=priority, category=category, due=due,
            main_only=main_only, minimum_only=minimum_only, include_all=all_items,
        )
    except (TaskQueryError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    if output_format == "json":
        typer.echo(json.dumps({"date": day, "count": len(values), "tasks": values}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"タスク一覧｜{day}｜{len(values)}件")
    if not values:
        typer.echo("条件に一致するタスクはありません。")
        return
    for index, item in enumerate(values, start=1):
        flags = [label for enabled, label in ((item["is_main"], "Main"), (item["is_minimum"], "最低限")) if enabled]
        typer.echo(f"{index}. [{item['status']}] {item['title']}")
        typer.echo(f"   ID: {item['short_id']} / 優先度: {item['priority']} / カテゴリ: {item['category'] or '未設定'} / 期限: {item['due_date'] or '未設定'}" + (f" / {', '.join(flags)}" if flags else ""))
        if detail:
            typer.echo(f"   参照元: {item['source']} {item['source_review_date']} / 作成: {item['created_at'] or '未記録'} / 更新: {item['updated_at'] or '未記録'}")


@export_app.command("csv")
def export_csv_command(
    kind: str = typer.Option("all", "--type", help="reviews / tasks / instructions / all"),
    output: Path | None = typer.Option(None, "--output", help="CSVファイルまたはall用ディレクトリ"),
    date: str | None = typer.Option(None, "--date", help="単日指定。--periodの基準日にも使用"),
    date_from: str | None = typer.Option(None, "--from", help="開始日 YYYY-MM-DD"),
    date_to: str | None = typer.Option(None, "--to", help="終了日 YYYY-MM-DD"),
    period: str | None = typer.Option(None, "--period", help="week（火曜開始）または month"),
    excel: bool = typer.Option(False, "--excel", help="UTF-8 BOM付きで出力"),
    force: bool = typer.Option(False, "--force", help="既存の出力CSVを上書き"),
    root: Path | None = RootOption,
) -> None:
    """レビュー、タスク、指示書を決定的なCSVで出力します。"""
    try:
        start, end = period_range(day=date, date_from=date_from, date_to=date_to, period=period)
        result = export_csv(_root(root), kind=kind, output=output, start=start, end=end, excel=excel, force=force)
    except FileExistsError as exc:
        typer.echo(f"ERROR: {exc}\n上書きする場合は --force を指定してください", err=True)
        raise typer.Exit(code=4) from exc
    except (ExportError, OSError, ValueError) as exc:
        typer.echo(f"ERROR: CSV出力に失敗しました: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    typer.echo(f"CSV出力｜{result['encoding']}")
    for item in result["files"]:
        typer.echo(f"{item['type']}: {item['path']} ({item['rows']}件)")


@notifications_app.command("check")
def notifications_check(
    date: str | None = DateOption,
    time_value: str | None = typer.Option(None, "--time", help="判定時刻 HH:MM"),
    dry_run: bool = typer.Option(False, "--dry-run", help="候補表示のみで送信・履歴保存を行わない"),
    root: Path | None = RootOption,
) -> None:
    """振り返り、指示書承認、期限、Main、最低限の通知条件を評価します。"""
    base, day = _root(root), _day(date)
    try:
        config = load_notification_config(base)
        current = parse_current(day, time_value)
        values = evaluate_notifications(base, day=day, current=current, config=config)
    except (NotificationError, TaskQueryError, OSError, ValueError) as exc:
        typer.echo(f"ERROR: 通知条件を評価できません: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    typer.echo(f"通知チェック｜{day} {current.strftime('%H:%M')}")
    if not values:
        typer.echo("送信すべき通知はありません。")
        return
    for item in values:
        typer.echo(f"- [{item.notification_type}] {item.message}")
    if dry_run:
        typer.echo(f"dry-run: {len(values)}件の候補を表示し、送信・履歴保存は行いませんでした。")
        return
    senders = []
    if config["console"]["enabled"]:
        senders.append(ConsoleSender(typer.echo))
    if config["file"]["enabled"]:
        senders.append(FileSender(base))
    try:
        result = dispatch_notifications(base, values, current=current, config=config, senders=senders)
    except (NotificationError, OSError, ValueError) as exc:
        typer.echo(f"ERROR: 通知履歴を保存できません: {exc}", err=True)
        raise typer.Exit(code=4) from exc
    typer.echo(f"送信: {result['sent']}件 / 重複スキップ: {result['skipped']}件 / 失敗: {result['failed']}件")
    if result["failed"]:
        typer.echo("WARN: 一部通知に失敗しました。日次データは変更していません。", err=True)


@notifications_app.command("history")
def notifications_history(
    json_output: bool = typer.Option(False, "--json", help="機械可読JSONで表示"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    root: Path | None = RootOption,
) -> None:
    """通知の送信・失敗履歴を新しい順で表示します。"""
    try:
        values = list(reversed(load_notification_history(_root(root))["records"]))[:limit]
    except (NotificationError, OSError, ValueError) as exc:
        typer.echo(f"ERROR: 通知履歴を読み込めません: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    if json_output:
        typer.echo(json.dumps({"records": values}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"通知履歴｜{len(values)}件")
    if not values:
        typer.echo("通知履歴はありません。")
        return
    for item in values:
        typer.echo(f"- {item.get('attempted_at', '未記録')} [{item.get('status', '不明')}] {item.get('notification_type', '不明')} -> {item.get('destination', '不明')}")


def _api_exit_code(response: CommandResponse) -> int:
    if not response.errors or response.status in {"partial_success"}:
        return 0
    codes = {item.code for item in response.errors}
    if codes & {"CONFIRMATION_REQUIRED", "TASK_AMBIGUOUS", "TASK_NOT_FOUND"}:
        return 3
    if codes & {
        "IDEMPOTENCY_CONFLICT",
        "CONFIRMATION_INVALID",
        "CONFIRMATION_EXPIRED",
        "PREVIEW_STALE",
        "DUPLICATE_REVIEW",
        "INSTRUCTION_ALREADY_APPROVED",
    }:
        return 4
    if "STORAGE_ERROR" in codes:
        return 5
    return 2


def _emit_api_response(response: CommandResponse, *, pretty: bool) -> None:
    typer.echo(
        json.dumps(
            response.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
    )
    code = _api_exit_code(response)
    if code:
        raise typer.Exit(code=code)


@api_app.command("execute")
def api_execute(
    input_path: Path | None = typer.Option(None, "--input", help="CommandRequest JSONファイル"),
    stdin: bool = typer.Option(False, "--stdin", help="標準入力からJSONを読む"),
    mode: str | None = typer.Option(None, "--mode", help="requestのmodeをpreviewまたはcommitで上書き"),
    confirmation_token: str | None = typer.Option(None, "--confirmation-token", help="previewで発行された確認トークン"),
    pretty: bool = typer.Option(False, "--pretty", help="JSONをインデントして表示"),
    root: Path | None = RootOption,
) -> None:
    """JSON CommandRequestを安全に実行します。

    previewは主要データを変更せず、commitに必要なconfirmation tokenを返します。
    書き込みcommitにはpreview済みtokenとidempotency keyが必要です。
    --inputまたは--stdinで受け取り、stdoutにはJSONだけを出力します。
    終了コード: 0 成功、2 入力、3 確認、4 競合、5 保存エラー。
    例: daily-review api execute --input request.json --pretty
    """
    raw: Any = {}
    try:
        if (input_path is None) == (not stdin):
            raise ValueError("--inputまたは--stdinのどちらか一方を指定してください")
        text = sys.stdin.read() if stdin else input_path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("入力JSONが空です")
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("入力はJSONオブジェクトにしてください")
        if mode is not None:
            if mode not in {"preview", "commit"}:
                raise ValueError("--modeはpreviewまたはcommitにしてください")
            raw["mode"] = mode
        if confirmation_token is not None:
            raw["confirmation_token"] = confirmation_token
        response = CommandExecutor(_root(root)).execute(raw)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        response = CommandResponse(
            request_id=str(raw.get("request_id", "unknown")) if isinstance(raw, dict) else "unknown",
            status="input_error",
            mode="preview",
            summary="入力JSONを読み込めません",
            errors=[{"code": "INVALID_REQUEST", "message": str(exc), "recoverable": True}],
            metadata={},
        )
    _emit_api_response(response, pretty=pretty)


@api_app.command("schema")
def api_schema(
    schema_type: str = typer.Option("all", "--type", help="request / response / all"),
    command: str | None = typer.Option(None, "--command", help="特定commandのschema"),
    pretty: bool = typer.Option(True, "--pretty/--compact", help="整形JSONまたはcompact JSON"),
) -> None:
    """実装と同じPydanticモデルからJSON Schemaを出力します。

    request、response、または個別commandの機械可読schemaを確認できます。
    例: daily-review api schema --type request
    """
    if command:
        model = COMMAND_MODELS.get(command)
        if model is None:
            typer.echo(json.dumps({"error": {"code": "UNKNOWN_COMMAND", "message": f"不明なcommandです: {command}"}}, ensure_ascii=False))
            raise typer.Exit(code=2)
        value: dict[str, Any] = {"version": "1", "command": command, "schema": model.model_json_schema()}
    elif schema_type == "request":
        value = CommandRequest.model_json_schema()
    elif schema_type == "response":
        value = CommandResponse.model_json_schema()
    elif schema_type == "all":
        value = {"version": "1", "request": CommandRequest.model_json_schema(), "response": CommandResponse.model_json_schema()}
    else:
        typer.echo(json.dumps({"error": {"code": "INVALID_REQUEST", "message": "--typeはrequest、response、allにしてください"}}, ensure_ascii=False))
        raise typer.Exit(code=2)
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2 if pretty else None, separators=None if pretty else (",", ":")))


@api_app.command("history")
def api_history(
    date: str | None = DateOption,
    request_id: str | None = typer.Option(None, "--request-id"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    pretty: bool = typer.Option(False, "--pretty"),
    root: Path | None = RootOption,
) -> None:
    """raw_inputを含まないCommand API監査履歴をJSONで表示します。

    日付、request ID、idempotency keyで絞り込めます。
    例: daily-review api history --date 2026-07-15 --pretty
    """
    try:
        values = load_audit_history(_root(root), date=date, request_id=request_id, idempotency_key=idempotency_key)
        output = {"count": len(values), "records": values}
    except (OSError, ValueError) as exc:
        output = {"count": 0, "records": [], "errors": [{"code": "STORAGE_ERROR", "message": str(exc)}]}
        typer.echo(json.dumps(output, ensure_ascii=False, indent=2 if pretty else None))
        raise typer.Exit(code=5) from exc
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2 if pretty else None, separators=None if pretty else (",", ":")))


@parse_app.command("review")
def parse_review_command(
    text: str | None = typer.Option(None, "--text", help="解析する自然文"),
    stdin: bool = typer.Option(False, "--stdin", help="標準入力から自然文を読む"),
    date: str | None = DateOption,
    preview: bool = typer.Option(False, "--preview", help="正規化結果をCommand APIでpreview"),
    commit: bool = typer.Option(False, "--commit", help="confirmation tokenを使って確定"),
    confirmation_token: str | None = typer.Option(None, "--confirmation-token"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    pretty: bool = typer.Option(False, "--pretty"),
    root: Path | None = RootOption,
) -> None:
    """日本語レビューを保持したままルールベースで正規化します。

    オプションなしはparse only、--previewは主要データ非更新の変更確認です。
    --commitには同じ入力のpreviewで発行されたconfirmation tokenが必要です。
    --textまたは--stdinのどちらか一方を使用します。
    例: daily-review parse review --stdin --date 2026-07-15 --preview
    """
    try:
        if (text is None) == (not stdin):
            raise NormalizationError("--textまたは--stdinのどちらか一方を指定してください")
        if preview and commit:
            raise NormalizationError("--previewと--commitは同時に指定できません")
        raw_text = sys.stdin.read() if stdin else text or ""
        day = _day(date)
        parsed = normalize_review(raw_text, effective_date=day)
    except (NormalizationError, ValueError) as exc:
        output = {"status": "input_error", "errors": [{"code": "INVALID_REQUEST", "message": str(exc), "recoverable": True}]}
        typer.echo(json.dumps(output, ensure_ascii=False, indent=2 if pretty else None))
        raise typer.Exit(code=2) from exc
    if not preview and not commit:
        typer.echo(json.dumps(parsed, ensure_ascii=False, indent=2 if pretty else None, separators=None if pretty else (",", ":")))
        return
    normalized = parsed["normalized"]
    stable_key = idempotency_key or f"parse-review-{normalized['date']}-{hashlib.sha256(raw_text.encode()).hexdigest()[:12]}"
    request = {
        "version": "1",
        "idempotency_key": stable_key,
        "mode": "commit" if commit else "preview",
        "timezone": "Asia/Tokyo",
        "effective_date": normalized["date"],
        "source": "parse_review",
        "raw_input": raw_text,
        "confirmation_token": confirmation_token,
        "commands": [{"type": "create_daily_review", "payload": normalized}],
    }
    response = CommandExecutor(_root(root)).execute(request)
    for warning in parsed["warnings"]:
        response.warnings.append(ApiIssue(code=warning["code"], message=warning["message"], details={key: value for key, value in warning.items() if key not in {"code", "message"}}))
    response.result["normalization"] = {"normalized": normalized, "confidence": parsed["confidence"]}
    _emit_api_response(response, pretty=pretty)


def _goal_error(message: str, *, code: int = 2) -> None:
    typer.echo(f"ERROR: {message}", err=True)
    raise typer.Exit(code=code)


def _goal_progress_label(goal: dict[str, Any]) -> str:
    progress, mode = goal_progress(goal)
    if progress is None:
        return "未設定"
    suffix = "（手動）" if mode == "manual" else ""
    return f"{progress:g}%{suffix}"


def _print_goal_warnings(goal: dict[str, Any], goals: list[dict[str, Any]]) -> None:
    due_date = goal.get("due_date")
    if isinstance(due_date, str) and parse_date(due_date) < parse_date(today_string()):
        typer.echo("WARN: 期限が過去です")
    parent_id = goal.get("parent_id")
    parent = next((item for item in goals if item.get("id") == parent_id), None)
    if parent and isinstance(due_date, str) and isinstance(parent.get("due_date"), str) and due_date > parent["due_date"]:
        typer.echo("WARN: 子目標の期限が親目標より後です")


def _print_goal(goal: dict[str, Any], goals: list[dict[str, Any]]) -> None:
    typer.echo(goal["title"])
    typer.echo(f"ID: {goal['id']}")
    typer.echo(f"レベル: {goal['level']}")
    typer.echo(f"カテゴリ: {goal.get('category') or '未設定'}")
    typer.echo(f"状態: {goal['status']}")
    typer.echo(f"期間: {goal.get('start_date') or '未設定'}〜{goal.get('due_date') or '未設定'}")
    typer.echo(f"進捗: {_goal_progress_label(goal)}")
    if goal.get("description"):
        typer.echo(f"説明: {goal['description']}")
    typer.echo("定性指標:")
    labels = {"not_met": "未達", "partially_met": "一部達成", "met": "達成"}
    for item in goal.get("qualitative_criteria") or []:
        typer.echo(f"- [{labels.get(item.get('status'), '不正')}] {item.get('description', '未設定')}")
    if not goal.get("qualitative_criteria"):
        typer.echo("なし")
    typer.echo("定量指標:")
    for item in goal.get("quantitative_metrics") or []:
        typer.echo(f"- {item.get('name', '未設定')}: {item.get('current')} / {item.get('target')}{item.get('unit', '')}")
    if not goal.get("quantitative_metrics"):
        typer.echo("なし")
    parent_id = goal.get("parent_id")
    parent = next((item for item in goals if item.get("id") == parent_id), None)
    typer.echo(f"親目標: {parent.get('title') if parent else 'なし'}")
    children = children_of(goals, goal["id"])
    typer.echo("子目標:")
    if children:
        for child in children:
            typer.echo(f"- {child['title']}")
    else:
        typer.echo("なし")


@design_app.callback()
def goal_design(
    ctx: typer.Context,
    text: str | None = typer.Option(None, "--text", help="曖昧な目標の原文"),
    root: Path | None = RootOption,
) -> None:
    """目標設計セッションを開始し、ChatGPT用プロンプトを表示します。"""
    if ctx.invoked_subcommand is not None:
        return
    if text is None:
        typer.echo(ctx.get_help())
        return
    try:
        value = create_design(_root(root), text)
    except (GoalDesignError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo(render_goal_design_prompt(value))
    typer.echo(f"\n保存先: data/goal-designs/{value['id']}.json")


@design_app.command("answer")
def goal_design_answer(
    design_id: str = typer.Argument(...),
    answer: str = typer.Option(..., "--answer"),
    root: Path | None = RootOption,
) -> None:
    """確認質問への回答を原文のまま追記します。"""
    try:
        value = save_answer(_root(root), design_id, answer)
    except (GoalDesignError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("回答を保存しました")
    typer.echo(f"回答数: {len(value['answers'])}件")
    typer.echo("次の操作: daily-review goal design prompt " + design_id)


@design_app.command("prompt")
def goal_design_prompt(design_id: str = typer.Argument(...), root: Path | None = RootOption) -> None:
    """回答を含むChatGPT用プロンプトを再表示します。"""
    try:
        typer.echo(render_goal_design_prompt(load_design(_root(root), design_id)))
    except (GoalDesignError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)


@design_app.command("receive")
def goal_design_receive(
    design_id: str = typer.Argument(...),
    json_text: str | None = typer.Option(None, "--json-text"),
    file: Path | None = typer.Option(None, "--file"),
    root: Path | None = RootOption,
) -> None:
    """ChatGPTが作成した目標候補JSONを未適用で保存します。"""
    if (json_text is None) == (file is None):
        _goal_error("--json-text または --file のどちらか1つを指定してください")
    try:
        raw = json_text if json_text is not None else file.read_text(encoding="utf-8")
        payload = json.loads(raw)
        receive_proposal(_root(root), design_id, payload)
    except json.JSONDecodeError as exc:
        _goal_error(f"JSONが不正です: {exc}", code=3)
    except (GoalDesignError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("目標設計proposalを保存しました（未適用）")
    typer.echo("次の操作: daily-review goal design review " + design_id)


@design_app.command("review")
def goal_design_review(
    design_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
    root: Path | None = RootOption,
) -> None:
    """適用前の目標設計候補を確認します。"""
    try:
        value = load_design(_root(root), design_id)
    except (GoalDesignError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    if json_output:
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))
        return
    typer.echo(f"目標設計｜{design_id}")
    typer.echo(f"状態: {value['status']}")
    typer.echo(f"原文: {value['raw_goal']}")
    proposal = value.get("proposal") or {}
    typer.echo(f"目標候補: {(proposal.get('goal') or {}).get('title', '未受信')}")
    typer.echo(f"マイルストーン: {len(proposal.get('milestones') or [])}件")


@design_app.command("apply")
def goal_design_apply(
    design_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes"),
    root: Path | None = RootOption,
) -> None:
    """確認済みの候補をgoalとroadmapへ1回だけ適用します。"""
    if not yes:
        _goal_error("適用には --yes が必要です")
    try:
        goal, _ = apply_design(_root(root), design_id)
    except (GoalDesignError, GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("目標設計を適用しました")
    typer.echo(f"目標ID: {goal['id']}")
    typer.echo(f"次の操作: daily-review goal roadmap {goal['id']}")


@goal_app.command("add")
def goal_add(
    title: str | None = typer.Option(None, "--title", help="目標のタイトル"),
    level: str | None = typer.Option(None, "--level", help="vision / long / medium / short"),
    category: str | None = typer.Option(None, "--category"),
    description: str | None = typer.Option(None, "--description"),
    start_date: str | None = typer.Option(None, "--start-date"),
    due_date: str | None = typer.Option(None, "--due-date"),
    parent: str | None = typer.Option(None, "--parent"),
    qualitative: list[str] = typer.Option([], "--qualitative", help="達成を判断できる定性指標。複数指定可。"),
    metric: list[str] = typer.Option([], "--metric", help="name|unit|baseline|target|direction。複数指定可。"),
    root: Path | None = RootOption,
) -> None:
    """目標を作成します。"""
    if title is None:
        title = typer.prompt("タイトル")
    if level is None:
        level = typer.prompt("レベル (vision/long/medium/short)", default="medium")
    base = _root(root)
    try:
        goal = new_goal(
            title=title, level=level, category=category, description=description, start_date=start_date,
            due_date=due_date, parent_id=parent, qualitative=qualitative, metrics=metric,
        )
        save_goal(base, goal, changed_fields=[], backup=False)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("目標を作成しました")
    typer.echo(f"ID: {goal['id']}")
    typer.echo(f"保存先: data/goals/items/{goal['id']}.json")
    _print_goal_warnings(goal, load_goals(base))


@goal_app.command("list")
def goal_list(
    level: str | None = typer.Option(None, "--level"),
    category: str | None = typer.Option(None, "--category"),
    status: str | None = typer.Option(None, "--status"),
    due_before: str | None = typer.Option(None, "--due-before"),
    all_items: bool = typer.Option(False, "--all", help="archivedを含めて表示する"),
    json_output: bool = typer.Option(False, "--json"),
    root: Path | None = RootOption,
) -> None:
    """目標を一覧表示します。"""
    try:
        if level is not None and level not in GOAL_LEVELS:
            raise GoalError("levelが不正です")
        if status is not None and status not in GOAL_STATUSES:
            raise GoalError("statusが不正です")
        if due_before is not None:
            parse_date(due_before)
        goals = load_goals(_root(root))
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    result = [goal for goal in goals if (all_items or goal.get("status") != "archived")]
    if level is not None:
        result = [goal for goal in result if goal.get("level") == level]
    if category is not None:
        result = [goal for goal in result if goal.get("category") == category]
    if status is not None:
        result = [goal for goal in result if goal.get("status") == status]
    if due_before is not None:
        result = [goal for goal in result if isinstance(goal.get("due_date"), str) and goal["due_date"] <= due_before]
    result.sort(key=lambda goal: (goal.get("due_date") is None, goal.get("due_date") or "", goal.get("title", "")))
    if json_output:
        typer.echo(json.dumps({"goals": result}, ensure_ascii=False, indent=2))
        return
    typer.echo("目標一覧")
    if not result:
        typer.echo("なし")
        return
    for goal in result:
        typer.echo(f"[{goal.get('status')}] {goal.get('title')}")
        typer.echo(f"ID: {goal.get('id')}")
        typer.echo(f"レベル: {goal.get('level')}")
        typer.echo(f"カテゴリ: {goal.get('category') or '未設定'}")
        typer.echo(f"期限: {goal.get('due_date') or '未設定'}")
        typer.echo(f"進捗: {_goal_progress_label(goal)}")


@goal_app.command("show")
def goal_show(
    goal_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
    root: Path | None = RootOption,
) -> None:
    """目標の詳細を表示します。"""
    try:
        base = _root(root)
        goal = load_goal(base, goal_id)
        goals = load_goals(base)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    if json_output:
        typer.echo(json.dumps(goal, ensure_ascii=False, indent=2))
        return
    _print_goal(goal, goals)


@goal_app.command("edit")
def goal_edit(
    goal_id: str = typer.Argument(...),
    title: str | None = typer.Option(None, "--title"),
    description: str | None = typer.Option(None, "--description"),
    category: str | None = typer.Option(None, "--category"),
    start_date: str | None = typer.Option(None, "--start-date"),
    due_date: str | None = typer.Option(None, "--due-date"),
    parent: str | None = typer.Option(None, "--parent"),
    clear_parent: bool = typer.Option(False, "--clear-parent"),
    manual_progress: float | None = typer.Option(None, "--manual-progress"),
    root: Path | None = RootOption,
) -> None:
    """許可済みの目標フィールドを編集します。"""
    if parent is not None and clear_parent:
        _goal_error("--parent と --clear-parent は同時に使用できません")
    base = _root(root)
    try:
        current = load_goal(base, goal_id)
        if all(value is None for value in (title, description, category, start_date, due_date, parent, manual_progress)) and not clear_parent:
            title = typer.prompt("タイトル", default=current["title"])
        changes = {key: value for key, value in {
            "title": title, "description": description, "category": category,
            "start_date": start_date, "due_date": due_date, "parent_id": parent,
            "manual_progress": manual_progress,
        }.items() if value is not None}
        if clear_parent:
            changes["parent_id"] = None
        goal = edit_goal(base, goal_id, changes)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("目標を更新しました")
    typer.echo(f"ID: {goal['id']}")
    typer.echo(f"revision: {goal['revision']}")
    _print_goal_warnings(goal, load_goals(base))


@goal_app.command("status")
def goal_status(
    goal_id: str = typer.Argument(...),
    status: str = typer.Argument(...),
    root: Path | None = RootOption,
) -> None:
    """目標の状態を変更します。"""
    try:
        goal = set_goal_status(_root(root), goal_id, status)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("目標の状態を更新しました")
    typer.echo(f"状態: {goal['status']}")


@goal_app.command("archive")
def goal_archive(
    goal_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", help="確認なしでアーカイブする"),
    root: Path | None = RootOption,
) -> None:
    """目標を物理削除せずアーカイブします。"""
    base = _root(root)
    try:
        goal = load_goal(base, goal_id)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    if not yes and not typer.confirm(f"目標「{goal['title']}」をアーカイブしますか？", default=False):
        typer.echo("アーカイブを中止しました")
        raise typer.Exit(code=2)
    try:
        archived = archive_goal(base, goal_id)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("目標をアーカイブしました")
    typer.echo(f"ID: {archived['id']}")


def _confirm_roadmap_warnings(warnings: list[str], *, allow_warning: bool) -> None:
    if not warnings:
        return
    for warning in warnings:
        typer.echo(f"WARNING: {warning}", err=True)
    if not allow_warning and not typer.confirm("警告を確認して保存しますか？", default=False):
        _goal_error("警告のため保存を中止しました。--allow-warningで保存できます")


def _milestone_progress_label(milestone: dict[str, Any]) -> str:
    progress, source = milestone_progress(milestone)
    if progress is None:
        return "未設定"
    if source == "steps":
        included = [step for step in milestone.get("steps") or [] if step.get("status") != "cancelled"]
        done = sum(step.get("status") == "done" for step in included)
        return f"{progress:g}%（ステップ{done}/{len(included)}）"
    labels = {"indicators": "指標", "manual": "手動"}
    return f"{progress:g}%（{labels.get(source, source)}）"


def _print_milestone(milestone: dict[str, Any]) -> None:
    typer.echo(milestone["title"])
    typer.echo(f"ID: {milestone['id']}")
    typer.echo(f"状態: {milestone['status']}")
    typer.echo(f"期間: {milestone.get('start_date') or '未設定'}〜{milestone.get('due_date') or '未設定'}")
    typer.echo(f"進捗: {_milestone_progress_label(milestone)}")
    typer.echo(f"順番: {milestone['order']}")
    typer.echo("定量指標:")
    for item in milestone.get("quantitative_metrics") or []:
        typer.echo(f"- {item['name']}: {item['current']} / {item['target']}{item['unit']}")
    if not milestone.get("quantitative_metrics"):
        typer.echo("なし")
    labels = {"not_met": "未達", "partially_met": "一部達成", "met": "達成"}
    typer.echo("定性指標:")
    for item in milestone.get("qualitative_criteria") or []:
        typer.echo(f"- [{labels[item['status']]}] {item['description']}")
    if not milestone.get("qualitative_criteria"):
        typer.echo("なし")
    typer.echo("依存関係:")
    typer.echo(", ".join(milestone.get("dependencies") or []) or "なし")
    typer.echo("実行ステップ:")
    for step in sorted(milestone.get("steps") or [], key=lambda item: item["order"]):
        typer.echo(f"{step['order']}. [{step['status']}] {step['title']}")
    if not milestone.get("steps"):
        typer.echo("なし")


@milestone_app.command("add")
def milestone_add(
    goal_id: str = typer.Argument(...),
    title: str | None = typer.Option(None, "--title"),
    description: str | None = typer.Option(None, "--description"),
    start_date: str | None = typer.Option(None, "--start-date"),
    due_date: str | None = typer.Option(None, "--due-date"),
    qualitative: list[str] = typer.Option([], "--qualitative"),
    metric_name: str | None = typer.Option(None, "--metric-name"),
    metric_unit: str = typer.Option("", "--metric-unit"),
    metric_baseline: float | None = typer.Option(None, "--metric-baseline"),
    metric_target: float | None = typer.Option(None, "--metric-target"),
    allow_warning: bool = typer.Option(False, "--allow-warning"),
    root: Path | None = RootOption,
) -> None:
    """目標へマイルストーンを追加します。"""
    if title is None:
        title = typer.prompt("マイルストーンのタイトル")
    base = _root(root)
    try:
        goal = load_goal(base, goal_id)
        milestone = new_milestone(goal, title=title, description=description, start_date=start_date, due_date=due_date, qualitative=qualitative, metric_name=metric_name, metric_unit=metric_unit, metric_baseline=metric_baseline, metric_target=metric_target)
        _confirm_roadmap_warnings(milestone_warnings(goal, milestone), allow_warning=allow_warning)
        add_milestone(base, goal_id, milestone)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("マイルストーンを追加しました")
    typer.echo(f"ID: {milestone['id']}")


@milestone_app.command("list")
def milestone_list(
    goal_id: str = typer.Argument(...),
    status: str | None = typer.Option(None, "--status"),
    due_before: str | None = typer.Option(None, "--due-before"),
    json_output: bool = typer.Option(False, "--json"),
    root: Path | None = RootOption,
) -> None:
    """目標のマイルストーンを一覧表示します。"""
    try:
        if status is not None and status not in MILESTONE_STATUSES:
            raise GoalError("マイルストーンstatusが不正です")
        if due_before:
            parse_date(due_before)
        goal = load_goal(_root(root), goal_id)
        milestones = sorted(milestones_of(goal), key=lambda item: item["order"])
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    if status:
        milestones = [item for item in milestones if item.get("status") == status]
    if due_before:
        milestones = [item for item in milestones if item.get("due_date") and item["due_date"] <= due_before]
    if json_output:
        typer.echo(json.dumps({"goal_id": goal_id, "milestones": milestones}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"マイルストーン｜{goal['title']}")
    if not milestones:
        typer.echo("なし")
    for milestone in milestones:
        typer.echo(f"{milestone['order']}. [{milestone['status']}] {milestone['title']}")
        typer.echo(f"   期限: {milestone.get('due_date') or '未設定'}")
        typer.echo(f"   進捗: {_milestone_progress_label(milestone)}")
        done = sum(step.get("status") == "done" for step in milestone.get("steps") or [])
        typer.echo(f"   ステップ: {done} / {len(milestone.get('steps') or [])}完了")


@milestone_app.command("show")
def milestone_show(
    goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption,
) -> None:
    """マイルストーンの詳細を表示します。"""
    try:
        milestone = find_milestone(load_goal(_root(root), goal_id), milestone_id)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    if json_output:
        typer.echo(json.dumps(milestone, ensure_ascii=False, indent=2)); return
    _print_milestone(milestone)


@milestone_app.command("edit")
def milestone_edit(
    goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...),
    title: str | None = typer.Option(None, "--title"), description: str | None = typer.Option(None, "--description"),
    start_date: str | None = typer.Option(None, "--start-date"), due_date: str | None = typer.Option(None, "--due-date"),
    status: str | None = typer.Option(None, "--status"), manual_progress: float | None = typer.Option(None, "--manual-progress"),
    qualitative: list[str] = typer.Option([], "--qualitative"), metric: list[str] = typer.Option([], "--metric"),
    clear_qualitative: bool = typer.Option(False, "--clear-qualitative"), clear_metrics: bool = typer.Option(False, "--clear-metrics"),
    depends_on: list[str] = typer.Option([], "--depends-on"), clear_dependencies: bool = typer.Option(False, "--clear-dependencies"),
    allow_warning: bool = typer.Option(False, "--allow-warning"), root: Path | None = RootOption,
) -> None:
    """マイルストーンを編集します。"""
    if depends_on and clear_dependencies:
        _goal_error("--depends-on と --clear-dependencies は同時に使用できません")
    if qualitative and clear_qualitative:
        _goal_error("--qualitative と --clear-qualitative は同時に使用できません")
    if metric and clear_metrics:
        _goal_error("--metric と --clear-metrics は同時に使用できません")
    base = _root(root)
    try:
        goal = load_goal(base, goal_id)
        milestone = find_milestone(goal, milestone_id)
        changes = {key: value for key, value in {"title": title, "description": description, "start_date": start_date, "due_date": due_date, "status": status}.items() if value is not None}
        if status is not None and status not in MILESTONE_STATUSES:
            raise GoalError("マイルストーンstatusが不正です")
        if manual_progress is not None:
            changes["progress"] = {"mode": "manual", "manual_value": manual_progress}
        if qualitative:
            changes["qualitative_criteria"] = [
                {"id": f"qual-{uuid.uuid4().hex[:8]}", "description": value, "status": "not_met"}
                for value in qualitative
            ]
        if clear_qualitative:
            changes["qualitative_criteria"] = []
        if metric:
            changes["quantitative_metrics"] = [parse_metric(value) for value in metric]
        if clear_metrics:
            changes["quantitative_metrics"] = []
        if depends_on:
            changes["dependencies"] = depends_on
        if clear_dependencies:
            changes["dependencies"] = []
        preview = dict(milestone); preview.update(changes)
        _confirm_roadmap_warnings(milestone_warnings(goal, preview), allow_warning=allow_warning)
        updated = update_milestone(base, goal_id, milestone_id, changes)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("マイルストーンを更新しました")
    typer.echo(f"revision: {updated['revision']}")


@milestone_app.command("status")
def milestone_status(
    goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...), status: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes"), root: Path | None = RootOption,
) -> None:
    """マイルストーンの状態を変更します。"""
    base = _root(root)
    try:
        goal = load_goal(base, goal_id); milestone = find_milestone(goal, milestone_id)
        if status not in MILESTONE_STATUSES:
            raise GoalError("マイルストーンstatusが不正です")
        pending = [step for step in milestone.get("steps") or [] if step.get("status") not in {"done", "cancelled"}]
        if status == "completed" and pending:
            typer.echo(f"WARNING: 未完了ステップが{len(pending)}件あります", err=True)
            if not yes and not typer.confirm("完了扱いにしますか？", default=False):
                _goal_error("状態変更を中止しました")
        changes: dict[str, Any] = {"status": status}
        if status == "completed": changes["completed_at"] = milestone.get("completed_at") or now_iso()
        elif milestone.get("completed_at") is not None: changes["completed_at"] = None
        updated = update_milestone(base, goal_id, milestone_id, changes)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("マイルストーンの状態を更新しました")
    typer.echo(f"状態: {updated['status']}")


@milestone_app.command("reorder")
def milestone_reorder(
    goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...),
    before: str | None = typer.Option(None, "--before"), position: int | None = typer.Option(None, "--position"), root: Path | None = RootOption,
) -> None:
    """マイルストーンの順番を連番へ正規化して変更します。"""
    try:
        reorder_milestone(_root(root), goal_id, milestone_id, before_id=before, position=position)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("マイルストーンを並べ替えました")


@step_app.command("add")
def step_add(
    goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...), title: str | None = typer.Option(None, "--title"),
    description: str | None = typer.Option(None, "--description"), start_date: str | None = typer.Option(None, "--start-date"), due_date: str | None = typer.Option(None, "--due-date"), minimum: str | None = typer.Option(None, "--minimum"),
    allow_warning: bool = typer.Option(False, "--allow-warning"), root: Path | None = RootOption,
) -> None:
    """マイルストーンへ実行ステップを追加します。"""
    if title is None: title = typer.prompt("ステップのタイトル")
    base = _root(root)
    try:
        goal = load_goal(base, goal_id); milestone = find_milestone(goal, milestone_id)
        step = new_step(title=title, description=description, start_date=start_date, due_date=due_date, minimum=minimum, order=len(milestone.get("steps") or []) + 1)
        _confirm_roadmap_warnings(step_warnings(milestone, step), allow_warning=allow_warning)
        add_step(base, goal_id, milestone_id, step)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("ステップを追加しました")
    typer.echo(f"ID: {step['id']}")


@step_app.command("list")
def step_list(goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...), json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption) -> None:
    """ステップを一覧表示します。"""
    try:
        milestone = find_milestone(load_goal(_root(root), goal_id), milestone_id)
        steps = sorted(milestone.get("steps") or [], key=lambda item: item["order"])
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    if json_output: typer.echo(json.dumps({"steps": steps}, ensure_ascii=False, indent=2)); return
    typer.echo(f"ステップ｜{milestone['title']}")
    for step in steps: typer.echo(f"{step['order']}. [{step['status']}] {step['title']}")
    if not steps: typer.echo("なし")


@step_app.command("edit")
def step_edit(
    goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...), step_id: str = typer.Argument(...),
    title: str | None = typer.Option(None, "--title"), description: str | None = typer.Option(None, "--description"), start_date: str | None = typer.Option(None, "--start-date"), due_date: str | None = typer.Option(None, "--due-date"), minimum: str | None = typer.Option(None, "--minimum"), depends_on: list[str] = typer.Option([], "--depends-on"), clear_dependencies: bool = typer.Option(False, "--clear-dependencies"), allow_warning: bool = typer.Option(False, "--allow-warning"), root: Path | None = RootOption,
) -> None:
    """ステップを編集します。"""
    if depends_on and clear_dependencies: _goal_error("--depends-on と --clear-dependencies は同時に使用できません")
    base = _root(root)
    try:
        goal = load_goal(base, goal_id); milestone, step = find_step(goal, milestone_id, step_id)
        changes = {key: value for key, value in {"title": title, "description": description, "start_date": start_date, "due_date": due_date, "minimum": minimum}.items() if value is not None}
        if depends_on: changes["dependencies"] = depends_on
        if clear_dependencies: changes["dependencies"] = []
        preview = dict(step); preview.update(changes)
        _confirm_roadmap_warnings(step_warnings(milestone, preview), allow_warning=allow_warning)
        updated = update_step(base, goal_id, milestone_id, step_id, changes)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("ステップを更新しました")
    typer.echo(f"revision: {updated['revision']}")


@step_app.command("status")
def step_status(goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...), step_id: str = typer.Argument(...), status: str = typer.Argument(...), root: Path | None = RootOption) -> None:
    """ステップの状態を変更します。"""
    if status not in STEP_STATUSES: _goal_error("ステップstatusが不正です")
    try:
        base = _root(root); _, step = find_step(load_goal(base, goal_id), milestone_id, step_id)
        changes: dict[str, Any] = {"status": status}
        if status == "done": changes["completed_at"] = step.get("completed_at") or now_iso()
        elif step.get("completed_at") is not None: changes["completed_at"] = None
        update_step(base, goal_id, milestone_id, step_id, changes)
    except (GoalError, OSError, ValueError) as exc:
        _goal_error(str(exc), code=3)
    typer.echo("ステップの状態を更新しました")


@step_app.command("reorder")
def step_reorder(goal_id: str = typer.Argument(...), milestone_id: str = typer.Argument(...), step_id: str = typer.Argument(...), before: str | None = typer.Option(None, "--before"), position: int | None = typer.Option(None, "--position"), root: Path | None = RootOption) -> None:
    """ステップを並べ替えます。"""
    try: reorder_step(_root(root), goal_id, milestone_id, step_id, before_id=before, position=position)
    except (GoalError, OSError, ValueError) as exc: _goal_error(str(exc), code=3)
    typer.echo("ステップを並べ替えました")


@goal_app.command("roadmap")
def goal_roadmap(goal_id: str = typer.Argument(...), compact: bool = typer.Option(False, "--compact"), json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption) -> None:
    """目標全体のマイルストーンとステップを時系列で表示します。"""
    try: goal = load_goal(_root(root), goal_id); milestones = sorted(milestones_of(goal), key=lambda item: item["order"])
    except (GoalError, OSError, ValueError) as exc: _goal_error(str(exc), code=3)
    if json_output: typer.echo(json.dumps({"goal": goal, "milestones": milestones}, ensure_ascii=False, indent=2)); return
    typer.echo(f"ロードマップ｜{goal['title']}")
    typer.echo(f"期間: {goal.get('start_date') or '未設定'}〜{goal.get('due_date') or '未設定'}")
    typer.echo(f"現在の進捗: {_goal_progress_label(goal)}")
    for milestone in milestones:
        due_label = milestone.get("due_date") or "期限未設定"
        if milestone.get("due_date") and milestone["due_date"] < today_string() and milestone.get("status") not in {"completed", "cancelled"}:
            due_label += "｜期限超過"
        typer.echo(due_label)
        typer.echo(f"├─ [{milestone['status']}] {milestone['title']}｜{_milestone_progress_label(milestone)}")
        if not compact:
            for step in sorted(milestone.get('steps') or [], key=lambda item: item['order']): typer.echo(f"│  ├─ [{step['status']}] {step['title']}")


@goal_app.command("next")
def goal_next(goal_id: str = typer.Argument(...), date: str | None = DateOption, root: Path | None = RootOption) -> None:
    """依存関係と期限を考慮して次に進める項目を1つ表示します。"""
    try:
        goal = load_goal(_root(root), goal_id); action = next_goal_action(goal, today=_day(date))
    except (GoalError, OSError, ValueError) as exc: _goal_error(str(exc), code=3)
    if not action:
        typer.echo("次に進められる項目はありません")
        typer.echo("理由: すべての候補が依存関係でブロックされています")
        return
    milestone, step = action["milestone"], action["step"]
    typer.echo(f"次に進める項目｜{goal['title']}")
    typer.echo(f"マイルストーン: {milestone['title']}")
    if step:
        typer.echo(f"実行ステップ: {step['title']}")
        typer.echo(f"期限: {step.get('due_date') or milestone.get('due_date') or '未設定'}")
        typer.echo(f"最低ライン: {step.get('minimum') or '未設定'}")
    else:
        typer.echo("実行ステップ: 未分解")


def _planning_error(message: str, *, code: int = 3) -> None:
    typer.echo(f"ERROR: {message}", err=True)
    raise typer.Exit(code=code)


def _print_week_plan(plan: dict[str, Any], *, saved: bool) -> None:
    typer.echo(f"週次計画候補｜{plan['week_start']}〜{plan['week_end']}")
    typer.echo("重点候補:")
    for index, item in enumerate(plan.get("focus_items") or [], start=1):
        typer.echo(f"{index}. {item['category']}｜{item['title']}")
        typer.echo(f"   期限: {item.get('due_date') or '未設定'}")
        typer.echo(f"   理由: {item.get('reason') or '目標の次アクション'}")
    if not plan.get("focus_items"):
        typer.echo("なし")
    if plan.get("carryovers"):
        typer.echo("前週からの繰越:")
        for item in plan["carryovers"]: typer.echo(f"- {item.get('title', item)}")
    typer.echo(f"保存状態: {'承認済み' if plan.get('status') == 'approved' else '保存済み' if saved else '未保存'}")
    if not saved:
        typer.echo(f"次の操作: daily-review plan week --date {plan['week_start']} --save")


def _print_daily_plan(plan: dict[str, Any], *, saved: bool) -> None:
    typer.echo(f"今日の計画候補｜{plan['date']}")
    typer.echo("Main候補:")
    for index, item in enumerate(plan.get("main_candidates") or [], start=1):
        typer.echo(f"{index}. {item['category']}｜{item['title']}")
        if item.get("minimum"): typer.echo(f"   最低ライン: {item['minimum']}")
        typer.echo(f"   理由: {item.get('reason') or '目標の次アクション'}")
    if not plan.get("main_candidates"):
        typer.echo("なし")
    if plan.get("other_tasks"):
        typer.echo("その他候補:")
        for item in plan["other_tasks"]: typer.echo(f"- {item['title']}")
    if len(plan.get("main_candidates") or []) >= MAX_MAIN_CANDIDATES and plan.get("other_tasks"):
        typer.echo("WARNING: 今日の候補が多すぎます。Mainは最大3件に絞ってください", err=True)
    if not saved:
        typer.echo(f"次の操作: daily-review plan today --date {plan['date']} --save")


@plan_app.command("week")
def plan_week(
    date: str | None = DateOption, dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"), save: bool = typer.Option(False, "--save"), root: Path | None = RootOption,
) -> None:
    """進行中の目標から、火曜始まりの週次重点候補を作ります。"""
    if save and dry_run: _planning_error("--save と --dry-run は同時に指定できません", code=2)
    day, base = _day(date), _root(root)
    try:
        plan = generate_weekly_plan(base, day, load_priorities(base))
        if save and not dry_run: save_weekly_plan(base, plan)
    except (PlanningError, GoalError, OSError, ValueError) as exc:
        _planning_error(str(exc))
    if json_output:
        typer.echo(json.dumps(plan, ensure_ascii=False, indent=2)); return
    _print_week_plan(plan, saved=save and not dry_run)
    if dry_run: typer.echo("dry-run: 保存は行いませんでした")


@plan_app.command("today")
def plan_today(
    date: str | None = DateOption, dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"), save: bool = typer.Option(False, "--save"), root: Path | None = RootOption,
) -> None:
    """承認済み週次重点と目標から、今日のMain候補を最大3件作ります。"""
    if save and dry_run: _planning_error("--save と --dry-run は同時に指定できません", code=2)
    day, base = _day(date), _root(root)
    try:
        plan = generate_daily_plan(base, day, load_priorities(base))
        if save and not dry_run: save_daily_plan(base, plan)
    except (PlanningError, GoalError, OSError, ValueError) as exc:
        _planning_error(str(exc))
    if json_output:
        typer.echo(json.dumps(plan, ensure_ascii=False, indent=2)); return
    _print_daily_plan(plan, saved=save and not dry_run)
    if dry_run: typer.echo("dry-run: 保存は行いませんでした")


@plan_app.command("review")
def plan_review(
    week: str | None = typer.Option(None, "--week"), date: str | None = DateOption,
    json_output: bool = typer.Option(False, "--json"), remove: str | None = typer.Option(None, "--remove"),
    add_step: tuple[str, str, str] | None = typer.Option(None, "--add-step"), move: str | None = typer.Option(None, "--move"),
    position: int | None = typer.Option(None, "--position"), set_minimum: str | None = typer.Option(None, "--set-minimum"), root: Path | None = RootOption,
) -> None:
    """保存済み週次または日次計画を表示・編集します。"""
    if (week is None) == (date is None): _planning_error("--week または --date のどちらか一方を指定してください", code=2)
    base = _root(root)
    try:
        if week:
            plan = load_weekly_plan(base, week)
            if not plan: raise PlanningError("週次計画が見つかりません")
            items = plan["focus_items"]
            changed = False
            if remove:
                filtered = [item for item in items if item["id"] != remove]
                if len(filtered) == len(items): raise PlanningError("削除対象の重点が見つかりません")
                plan["focus_items"] = filtered; items = filtered; changed = True
            if add_step:
                goal_id, milestone_id, step_id = add_step
                goal = load_goal(base, goal_id); milestone, step = find_step(goal, milestone_id, step_id)
                if len(items) >= 5: raise PlanningError("週次重点は最大5件です")
                from .planning import _candidate
                items.append(_candidate(goal, milestone, step, reason="手動追加")); changed = True
            if move:
                if position is None: raise PlanningError("--moveには--positionが必要です")
                item = next((value for value in items if value["id"] == move), None)
                if not item or not 1 <= position <= len(items): raise PlanningError("並べ替え対象またはpositionが不正です")
                items.remove(item); items.insert(position - 1, item); changed = True
            if changed: update_weekly_plan(base, plan)
            if json_output: typer.echo(json.dumps(plan, ensure_ascii=False, indent=2)); return
            _print_week_plan(plan, saved=True)
        else:
            day = _day(date); plan = load_daily_plan(base, day)
            if not plan: raise PlanningError("日次計画が見つかりません")
            changed = False
            if set_minimum:
                if "=" not in set_minimum: raise PlanningError("--set-minimumはdaily-plan-xxxxxxxx=最低ライン形式にしてください")
                item_id, minimum = set_minimum.split("=", 1)
                item = next((value for value in plan["main_candidates"] if value["id"] == item_id), None)
                if not item or not minimum.strip(): raise PlanningError("最低ラインの対象または内容が不正です")
                item["minimum"] = minimum; plan["minimum_candidates"] = [value["minimum"] for value in plan["main_candidates"] if value.get("minimum")]; changed = True
            if changed: update_daily_plan(base, plan)
            if json_output: typer.echo(json.dumps(plan, ensure_ascii=False, indent=2)); return
            _print_daily_plan(plan, saved=True)
    except (PlanningError, GoalError, OSError, ValueError) as exc:
        _planning_error(str(exc))


@plan_app.command("apply")
def plan_apply(
    week: str | None = typer.Option(None, "--week"), date: str | None = DateOption,
    yes: bool = typer.Option(False, "--yes"), root: Path | None = RootOption,
) -> None:
    """週次または日次のドラフト計画を明示承認します。"""
    if (week is None) == (date is None): _planning_error("--week または --date のどちらか一方を指定してください", code=2)
    if not yes and not typer.confirm("この計画を承認しますか？", default=False): _planning_error("承認を中止しました", code=2)
    try:
        plan = approve_goal_plan(_root(root), week=week, day=_day(date) if date else None)
    except (PlanningError, OSError, ValueError) as exc:
        _planning_error(str(exc))
    typer.echo("計画を承認しました")
    typer.echo(f"状態: {plan['status']}")


@goal_app.command("link")
def goal_link(
    date: str | None = DateOption, week: str | None = typer.Option(None, "--week"), main_index: int | None = typer.Option(None, "--main-index"),
    task_index: int | None = typer.Option(None, "--task-index"), focus_index: int | None = typer.Option(None, "--focus-index"),
    goal: str | None = typer.Option(None, "--goal"), milestone: str | None = typer.Option(None, "--milestone"), step: str | None = typer.Option(None, "--step"), root: Path | None = RootOption,
) -> None:
    """計画項目を目標・マイルストーン・ステップへ手動リンクします。"""
    if (week is None) == (date is None) or sum(value is not None for value in (main_index, task_index, focus_index)) != 1:
        _planning_error("日付/週とリンク対象を1つずつ指定してください", code=2)
    base = _root(root)
    try:
        if week:
            plan = load_weekly_plan(base, week)
            if not plan or focus_index is None or not 1 <= focus_index <= len(plan["focus_items"]): raise PlanningError("週次重点が見つかりません")
            item = plan["focus_items"][focus_index - 1]
            if goal: item["goal_id"] = goal
            if milestone: item["milestone_id"] = milestone
            if step: item["step_id"] = step
            from .planning import _validate_ref
            _validate_ref(base, item); update_weekly_plan(base, plan)
        else:
            day = _day(date); plan = load_daily_plan(base, day)
            if not plan: raise PlanningError("日次計画が見つかりません")
            record_type, record_index = ("main", main_index) if main_index is not None else ("task", task_index)
            source_items = plan["main_candidates"] if record_type == "main" else plan.get("other_tasks") or []
            if record_index is None or not 1 <= record_index <= len(source_items): raise PlanningError("日次計画項目が見つかりません")
            item = source_items[record_index - 1]
            if goal: item["goal_id"] = goal
            if milestone: item["milestone_id"] = milestone
            if step: item["step_id"] = step
            from .planning import _validate_ref
            _validate_ref(base, item)
            plan["goal_links"] = [link for link in plan.get("goal_links") or [] if (link.get("record_type"), link.get("record_index")) != (record_type, record_index)]
            plan["goal_links"].append({"record_type": record_type, "record_index": record_index, "goal_id": item["goal_id"], "milestone_id": item.get("milestone_id"), "step_id": item.get("step_id"), "linked_at": now_iso()})
            update_daily_plan(base, plan)
    except (PlanningError, GoalError, OSError, ValueError) as exc:
        _planning_error(str(exc))
    typer.echo("目標リンクを保存しました")


@goal_app.command("unlink")
def goal_unlink(date: str | None = DateOption, main_index: int = typer.Option(..., "--main-index"), root: Path | None = RootOption) -> None:
    """日次Mainからリンクだけを外します。文章や計画項目は削除しません。"""
    try:
        plan = load_daily_plan(_root(root), _day(date))
        if not plan: raise PlanningError("日次計画が見つかりません")
        old = len(plan.get("goal_links") or []); plan["goal_links"] = [link for link in plan.get("goal_links") or [] if link.get("record_index") != main_index]
        if old == len(plan["goal_links"]): raise PlanningError("解除対象のリンクが見つかりません")
        update_daily_plan(_root(root), plan)
    except (PlanningError, OSError, ValueError) as exc:
        _planning_error(str(exc))
    typer.echo("目標リンクを解除しました")


@goal_app.command("progress")
def goal_progress_command(date: str | None = DateOption, apply: bool = typer.Option(False, "--apply"), yes: bool = typer.Option(False, "--yes"), root: Path | None = RootOption) -> None:
    """日次のリンク済み項目について、ステップ状態の更新候補を表示します。"""
    day, base = _day(date), _root(root)
    try:
        plan = load_daily_plan(base, day)
        if not plan: raise PlanningError("日次計画が見つかりません")
        entry = load_daily(base, day) or {}
        status_by_area = {item.get("area"): item.get("status") for item in ((entry.get("structured_review") or {}).get("today_main") or []) if isinstance(item, dict)}
        candidates: list[tuple[dict[str, Any], str]] = []
        for link in plan.get("goal_links") or []:
            if link.get("record_type") != "main":
                continue
            item = plan["main_candidates"][link["record_index"] - 1]
            review_status = status_by_area.get(item.get("category"))
            proposed = {"完了": "done", "一部進んだ": "doing", "中止": "cancelled"}.get(review_status)
            if proposed and link.get("step_id"):
                candidates.append((link, proposed))
    except (PlanningError, OSError, ValueError, IndexError) as exc:
        _planning_error(str(exc))
    typer.echo(f"目標進捗候補｜{day}")
    if not candidates: typer.echo("反映候補はありません")
    for index, (link, status) in enumerate(candidates, start=1):
        item = plan["main_candidates"][link["record_index"] - 1]
        typer.echo(f"{index}. {item['title']} → step status: {status}")
    if apply:
        if not yes and not typer.confirm("表示した進捗候補を反映しますか？", default=False): _planning_error("進捗反映を中止しました", code=2)
        try:
            apply_step_updates(base, [(link["goal_id"], link["milestone_id"], link["step_id"], status) for link, status in candidates])
        except (PlanningError, GoalError, OSError, ValueError) as exc:
            _planning_error(str(exc))
        plan.setdefault("progress_applications", []).append({
            "applied_at": now_iso(),
            "updates": [
                {"goal_id": link["goal_id"], "milestone_id": link["milestone_id"], "step_id": link["step_id"], "status": status}
                for link, status in candidates
            ],
        })
        try:
            update_daily_plan(base, plan)
        except (PlanningError, OSError, ValueError) as exc:
            _planning_error(str(exc))
        typer.echo("目標進捗を反映しました")


def _evaluation_error(message: str, *, code: int = 3) -> None:
    typer.echo(f"ERROR: {message}", err=True)
    raise typer.Exit(code=code)


def _print_evaluation(value: dict[str, Any], *, period_type: str) -> None:
    if period_type == "week":
        typer.echo(f"週次目標評価｜{value['week_start']}〜{value['week_end']}")
        period_days = 7
    else:
        typer.echo(f"月次目標評価｜{value['month']}")
        period_days = month_range_for(f"{value['month']}-01")
        period_days = (parse_date(period_days[1]) - parse_date(period_days[0])).days + 1
    summary = value.get("summary") or {}
    planned, completed = summary.get("planned_main_count", 0), summary.get("completed_main_count", 0)
    completion = round(100 * completed / planned) if planned else 0
    typer.echo("全体:")
    typer.echo(f"記録日数: {summary.get('recorded_days', 0)} / {period_days}日")
    typer.echo(f"Main完了率: {completion}%")
    typer.echo(f"最低ライン達成: {summary.get('minimum_achieved_days', 0)}日")
    typer.echo(f"予定過多: {summary.get('overloaded_days', 0)}日")
    typer.echo("目標別:")
    labels = {"ahead": "前倒し", "on_track": "順調", "slightly_delayed": "やや遅れ", "delayed": "遅れ", "blocked": "停止", "inactive": "活動なし", "completed": "完了"}
    for index, item in enumerate(value.get("goal_evaluations") or [], start=1):
        typer.echo(f"{index}. {item.get('category') or '未設定'}｜{item['title']}")
        typer.echo(f"   判定: {labels.get(item['status'], item['status'])}")
        typer.echo(f"   進捗: {item.get('start_progress', 0):g}% → {item.get('end_progress', 0):g}%")
        typer.echo(f"   完了step: {item.get('completed_steps', 0)}件 / overdue: {item.get('overdue_steps', 0)}件")
    if not value.get("goal_evaluations"): typer.echo("なし")
    typer.echo("診断:")
    for item in value.get("diagnostics") or []: typer.echo(f"- {item['message']}")
    if not value.get("diagnostics"): typer.echo("なし")
    typer.echo(f"保存状態: {'承認済み' if value.get('status') == 'approved' else 'ドラフト'}")


@evaluate_app.command("week")
def goal_evaluate_week(
    date: str | None = DateOption, save: bool = typer.Option(False, "--save"), dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption,
) -> None:
    """火曜始まり・月曜終わりで目標と計画精度を評価します。"""
    if save and dry_run: _evaluation_error("--save と --dry-run は同時に指定できません", code=2)
    try:
        value = generate_weekly_evaluation(_root(root), _day(date))
        if save and not dry_run: save_evaluation(_root(root), value, period_type="week")
    except (EvaluationError, PlanningError, GoalError, OSError, ValueError) as exc:
        _evaluation_error(str(exc))
    if json_output: typer.echo(json.dumps(value, ensure_ascii=False, indent=2)); return
    _print_evaluation(value, period_type="week")
    if dry_run: typer.echo("dry-run: 保存は行いませんでした")


@evaluate_app.command("month")
def goal_evaluate_month(
    month: str | None = typer.Option(None, "--month"), date: str | None = DateOption,
    save: bool = typer.Option(False, "--save"), dry_run: bool = typer.Option(False, "--dry-run"), json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption,
) -> None:
    """暦月単位で目標の進捗傾向を評価します。"""
    if month and date: _evaluation_error("--month と --date は同時に指定できません", code=2)
    if save and dry_run: _evaluation_error("--save と --dry-run は同時に指定できません", code=2)
    target = month or _day(date)[:7]
    try:
        value = generate_monthly_evaluation(_root(root), target)
        if save and not dry_run: save_evaluation(_root(root), value, period_type="month")
    except (EvaluationError, PlanningError, GoalError, OSError, ValueError) as exc:
        _evaluation_error(str(exc))
    if json_output: typer.echo(json.dumps(value, ensure_ascii=False, indent=2)); return
    _print_evaluation(value, period_type="month")
    if dry_run: typer.echo("dry-run: 保存は行いませんでした")


@evaluate_app.command("review")
def goal_evaluate_review(
    week: str | None = typer.Option(None, "--week"), month: str | None = typer.Option(None, "--month"),
    json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption,
) -> None:
    """保存済みの週次または月次評価を確認します。"""
    if (week is None) == (month is None): _evaluation_error("--week または --month のどちらか一方を指定してください", code=2)
    try:
        value = load_weekly_evaluation(_root(root), week or "") if week else load_monthly_evaluation(_root(root), month or "")
        if not value: raise EvaluationError("評価が見つかりません")
    except (EvaluationError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    if json_output: typer.echo(json.dumps(value, ensure_ascii=False, indent=2)); return
    _print_evaluation(value, period_type="week" if week else "month")


@evaluate_app.command("apply")
def goal_evaluate_apply(
    week: str | None = typer.Option(None, "--week"), month: str | None = typer.Option(None, "--month"),
    yes: bool = typer.Option(False, "--yes"), root: Path | None = RootOption,
) -> None:
    """評価だけを承認し、目標や計画は変更しません。"""
    if (week is None) == (month is None): _evaluation_error("--week または --month のどちらか一方を指定してください", code=2)
    if not yes and not typer.confirm("評価を承認しますか？目標や計画は変更されません。", default=False): _evaluation_error("評価承認を中止しました", code=2)
    try: value = approve_evaluation(_root(root), week=week, month=month)
    except (EvaluationError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    typer.echo("評価を承認しました")
    typer.echo(f"状態: {value['status']}")


def _print_replan(value: dict[str, Any]) -> None:
    typer.echo(f"計画修正案｜{value['id']}")
    typer.echo(f"状態: {value['status']}")
    labels = {"reduce_daily_load": "日次負荷削減", "extend_deadline": "期限変更", "pause_goal": "目標一時停止", "remove_blocker": "blocker除去", "review_goal_definition": "目標定義見直し", "change_minimum": "最低ライン変更", "reduce_scope": "スコープ縮小"}
    for index, item in enumerate(value.get("proposals") or [], start=1):
        approved = " [承認対象]" if item["id"] in value.get("approved_proposal_ids", []) else ""
        typer.echo(f"{index}. {item['title']}{approved}")
        typer.echo(f"   ID: {item['id']}")
        typer.echo(f"   種類: {labels.get(item['type'], item['type'])}")
        typer.echo(f"   理由: {item['reason']}")
        typer.echo(f"   リスク: {item.get('risk') or '未設定'}")
        typer.echo(f"   信頼度: {item.get('confidence')}")


@replan_app.callback()
def goal_replan(
    ctx: typer.Context, week: str | None = typer.Option(None, "--week"), month: str | None = typer.Option(None, "--month"),
    goal_id: str | None = typer.Option(None, "--goal"), save: bool = typer.Option(False, "--save"), json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption,
) -> None:
    """評価または特定目標から、安全な修正案ドラフトを作ります。"""
    if ctx.invoked_subcommand is not None: return
    try:
        value = generate_replan(_root(root), week=week, month=month, goal_id=goal_id)
        if save: save_replan(_root(root), value)
    except (ReplanError, EvaluationError, GoalError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    if json_output: typer.echo(json.dumps(value, ensure_ascii=False, indent=2)); return
    _print_replan(value)
    if not save: typer.echo("保存状態: 未保存")


@replan_app.command("review")
def goal_replan_review(replan_id: str = typer.Argument(...), json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption) -> None:
    """保存済みreplanの提案と承認対象を表示します。"""
    try: value = load_replan(_root(root), replan_id)
    except (ReplanError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    if json_output: typer.echo(json.dumps(value, ensure_ascii=False, indent=2)); return
    _print_replan(value)


@replan_app.command("edit")
def goal_replan_edit(
    replan_id: str = typer.Argument(...), remove: str | None = typer.Option(None, "--remove"), approve: str | None = typer.Option(None, "--approve"),
    setting: str | None = typer.Option(None, "--set"), root: Path | None = RootOption,
) -> None:
    """proposalの削除・承認対象追加・安全なafter値の編集を行います。"""
    try: value = edit_replan(_root(root), replan_id, remove=remove, approve=approve, setting=setting)
    except (ReplanError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    typer.echo("replanを更新しました")
    typer.echo(f"revision: {value['revision']}")


@replan_app.command("apply")
def goal_replan_apply(replan_id: str = typer.Argument(...), yes: bool = typer.Option(False, "--yes"), root: Path | None = RootOption) -> None:
    """承認対象に選んだproposalだけをバックアップ後に適用します。"""
    if not yes and not typer.confirm("承認対象proposalを適用しますか？", default=False): _evaluation_error("replan適用を中止しました", code=2)
    try: value = apply_replan(_root(root), replan_id)
    except (ReplanError, GoalError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    typer.echo("replanを適用しました")
    typer.echo(f"状態: {value['status']}")


@replan_app.command("list")
def goal_replan_list(json_output: bool = typer.Option(False, "--json"), root: Path | None = RootOption) -> None:
    """保存済みreplanを一覧表示します。"""
    try: values = list_replans(_root(root))
    except (ReplanError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    if json_output: typer.echo(json.dumps({"replans": values}, ensure_ascii=False, indent=2)); return
    if not values: typer.echo("replanはありません")
    for value in values: typer.echo(f"[{value['status']}] {value['id']}｜{value['source_type']}:{value.get('source_id')}")


@replan_app.command("cancel")
def goal_replan_cancel(replan_id: str = typer.Argument(...), yes: bool = typer.Option(False, "--yes"), root: Path | None = RootOption) -> None:
    """未適用replanを取消します。"""
    if not yes and not typer.confirm("replanを取り消しますか？", default=False): _evaluation_error("取消を中止しました", code=2)
    try: value = cancel_replan(_root(root), replan_id)
    except (ReplanError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    typer.echo(f"replanを取消しました: {value['id']}")


@goal_app.command("coach")
def goal_coach(
    week: str | None = typer.Option(None, "--week"), month: str | None = typer.Option(None, "--month"),
    copy_prompt: bool = typer.Option(False, "--copy"), root: Path | None = RootOption,
) -> None:
    """保存済み評価をChatGPTへ渡すプロンプトを生成します。外部APIは使いません。"""
    try: prompt = build_coach_prompt(_root(root), week=week, month=month)
    except (GoalCoachError, EvaluationError, OSError, ValueError) as exc: _evaluation_error(str(exc))
    typer.echo(prompt)
    if copy_prompt:
        if not _copy_chat_prompt(prompt): _evaluation_error("クリップボードへコピーできません", code=4)
        typer.echo("ChatGPT用プロンプトをクリップボードへコピーしました", err=True)


@goal_app.command("coach-receive")
def goal_coach_receive(
    week: str | None = typer.Option(None, "--week"), month: str | None = typer.Option(None, "--month"),
    clipboard: bool = typer.Option(False, "--clipboard"), file: Path | None = typer.Option(None, "--file"), json_text: str | None = typer.Option(None, "--json-text"), root: Path | None = RootOption,
) -> None:
    """ChatGPT coach回答を評価の補助情報として保存します。自動適用しません。"""
    try:
        content, source = _read_chat_import_input(json_text=json_text, file=file, clipboard=clipboard)
        payload = _parse_json_text(content, source)
        receive_coach_payload(_root(root), payload, week=week, month=month)
    except (GoalCoachError, EvaluationError, OSError, ValueError, typer.BadParameter) as exc: _evaluation_error(str(exc), code=2 if isinstance(exc, (GoalCoachError, typer.BadParameter)) else 3)
    typer.echo("coach分析を保存しました")
    typer.echo("評価・目標・計画への自動適用は行っていません")


@app.command("input")
def input_text(
    date: str | None = DateOption,
    text: str | None = typer.Option(None, "--text", help="保存する自然文"),
    clipboard: bool = typer.Option(False, "--clipboard", help="macOSのクリップボードから読み込む"),
    dry_run: bool = typer.Option(False, "--dry-run", help="保存せずに内容だけを表示する"),
    root: Path | None = RootOption,
) -> None:
    """自然文の原文を日別inboxへ追記保存します。"""
    base = _root(root)
    day = _day(date)
    try:
        raw_text, source = _read_natural_input(text, clipboard)
    except typer.BadParameter as exc:
        _input_error(str(exc))
        return
    if not raw_text.strip():
        _input_error("入力内容が空です")
    if dry_run:
        typer.echo("daily-review input｜dry-run")
        typer.echo(f"日付: {day}")
        typer.echo(f"入力元: {source}")
        typer.echo(raw_text)
        typer.echo("保存は行いませんでした")
        return
    try:
        path, entry_id = _store_natural_input(base, day, raw_text, source)
    except ValueError as exc:
        typer.echo(f"ERROR: inboxを保存できません: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except OSError as exc:
        typer.echo(f"ERROR: inboxを保存できません: {exc}", err=True)
        raise typer.Exit(code=4) from exc
    typer.echo("入力を保存しました")
    typer.echo(f"日付: {day}")
    typer.echo(f"入力ID: {entry_id}")
    typer.echo(f"保存先: {path.relative_to(base)}")


def _store_natural_input(root: Path, day: str, raw_text: str, source: str) -> tuple[Path, str]:
    """Append one raw input with the same atomic semantics as ``input``."""
    path = inbox_path(root, day)
    try:
        payload = read_json_file(path) if path.exists() else {"date": day, "entries": []}
    except (OSError, ValueError) as exc:
        raise ValueError(f"inbox JSONを読み込めません: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("date") not in (None, day):
        raise ValueError("inbox JSONの日付が不正です")
    entries = payload.get("entries")
    if entries is None:
        entries = []
        payload["entries"] = entries
    if not isinstance(entries, list):
        raise ValueError("inbox JSONのentriesが不正です")
    known_ids = {str(entry.get("id")) for entry in entries if isinstance(entry, dict) and entry.get("id")}
    entry_id = f"{day.replace('-', '')}-{uuid.uuid4().hex[:6]}"
    while entry_id in known_ids:
        entry_id = f"{day.replace('-', '')}-{uuid.uuid4().hex[:6]}"
    payload["date"] = day
    entries.append({"id": entry_id, "created_at": now_iso(), "source": source, "raw_text": raw_text})
    atomic_write_json_data(path, payload)
    return path, entry_id


def _read_chat_import_input(
    *,
    json_text: str | None,
    file: Path | None,
    clipboard: bool,
) -> tuple[str, str]:
    """Select exactly one ChatGPT import source, including piped stdin."""
    explicit_count = sum((json_text is not None, file is not None, clipboard))
    piped = _stdin_is_piped()
    piped_text = sys.stdin.read() if explicit_count and piped else None
    if explicit_count > 1 or (explicit_count and piped_text):
        raise ValueError("--json-text、--file、--clipboard、標準入力は同時に使用できません")
    if json_text is not None:
        return json_text, "json_text"
    if file is not None:
        try:
            return file.read_text(encoding="utf-8"), "file"
        except OSError as exc:
            raise OSError(f"入力ファイルを読み込めません: {exc}") from exc
    if clipboard:
        return _read_clipboard_text(), "clipboard"
    if piped:
        stdin_text = sys.stdin.read()
        if stdin_text:
            return stdin_text, "stdin"
    raise ValueError("ChatGPT連携用JSONを指定してください")


def _chat_import_error(message: str, *, output_json: bool, code: int = 2) -> None:
    if output_json:
        typer.echo(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    else:
        typer.echo(f"ERROR: {message}", err=True)
    raise typer.Exit(code=code)


def _chat_auto_approval_reason(root: Path, day: str, draft: dict[str, Any], warnings: list[str]) -> str | None:
    if warnings:
        return "未知フィールドの警告があります"
    return _auto_approval_reason(root, day, draft)


def _chat_import_json_result(
    *,
    day: str,
    source: str,
    draft: dict[str, Any],
    input_id: str | None,
    input_saved: bool,
    approved: bool,
    dry_run: bool,
    warnings: list[str],
    backup_path: Path | None = None,
    root: Path | None = None,
) -> None:
    typer.echo(json.dumps({
        "ok": True,
        "date": day,
        "schema_version": "1.0",
        "source": source,
        "input_saved": input_saved,
        "input_id": input_id,
        "draft_saved": input_saved,
        "draft_path": f"data/drafts/{day}.json",
        "approved": approved,
        "dry_run": dry_run,
        "warnings": warnings,
        "backup_path": str(backup_path.relative_to(root)) if backup_path and root else None,
        "errors": [],
        "draft": draft,
    }, ensure_ascii=False, indent=2))


def _print_chat_import_result(
    *,
    base: Path,
    day: str,
    input_id: str | None,
    draft: dict[str, Any],
    source: str,
    warnings: list[str],
    dry_run: bool,
    backup_path: Path | None,
) -> None:
    typer.echo("daily-review chat-import｜dry-run" if dry_run else "ChatGPTの構造化入力を取り込みました")
    typer.echo(f"日付: {day}")
    typer.echo(f"入力元: {source}")
    if input_id:
        typer.echo(f"入力ID: {input_id}")
    typer.echo(f"保存先: {draft_path(base, day).relative_to(base)}")
    if backup_path:
        typer.echo(f"置換前ドラフトのバックアップ: {backup_path.relative_to(base)}")
    _print_draft_review(draft, day)
    for warning in warnings:
        typer.echo(f"WARN: {warning}")
    if dry_run:
        typer.echo("保存は行いませんでした")
    else:
        typer.echo(f"確認・承認: daily-review reflect --date {day} --resume")


def _copy_chat_prompt(prompt: str) -> bool:
    """Copy a generated prompt without making clipboard failure fatal to chat."""
    if platform.system() != "Darwin":
        return False
    try:
        subprocess.run(["pbcopy"], input=prompt, text=True, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


@app.command("chat-prompt")
def chat_prompt(
    date: str | None = DateOption,
    clipboard: bool = typer.Option(False, "--clipboard", help="プロンプトをmacOSのクリップボードへコピーする"),
    root: Path | None = RootOption,
) -> None:
    """ChatGPTへ渡す構造化インポート用プロンプトを表示します。"""
    base = _root(root)
    day = _day(date)
    path = base / "templates" / CHAT_IMPORT_PROMPT_NAME
    if not path.is_file():
        _input_error(f"テンプレートがありません: templates/{CHAT_IMPORT_PROMPT_NAME}\ndaily-review init を実行してください")
    try:
        prompt = path.read_text(encoding="utf-8").replace("YYYY-MM-DD", day)
    except OSError as exc:
        typer.echo(f"ERROR: テンプレートを読み込めません: {exc}", err=True)
        raise typer.Exit(code=4) from exc
    if not clipboard:
        typer.echo(prompt, nl=False)
        return
    if platform.system() != "Darwin":
        _input_error("--clipboard はmacOSでのみ利用できます")
    if not _copy_chat_prompt(prompt):
        typer.echo("ERROR: クリップボードへコピーできません", err=True)
        raise typer.Exit(code=4)
    typer.echo("ChatGPT用プロンプトをクリップボードへコピーしました")


@app.command("chat-import")
def chat_import(
    date: str | None = DateOption,
    clipboard: bool = typer.Option(False, "--clipboard", help="macOSのクリップボードから読み込む"),
    file: Path | None = typer.Option(None, "--file", help="ChatGPTの出力を保存したUTF-8ファイル"),
    json_text: str | None = typer.Option(None, "--json-text", help="ChatGPTのJSONまたはJSONを含む文章"),
    dry_run: bool = typer.Option(False, "--dry-run", help="検証・変換だけを行い保存しない"),
    approve: bool = typer.Option(False, "--approve", help="取込後に既存の確認・修正・承認フローを開始する"),
    yes: bool = typer.Option(False, "--yes", help="安全条件を満たす場合のみ確認なしで承認する"),
    output_json: bool = typer.Option(False, "--output-json", help="結果をJSONだけで標準出力へ出力する"),
    force: bool = typer.Option(False, "--force", help="未承認ドラフトをバックアップして置き換える"),
    root: Path | None = RootOption,
) -> None:
    """ChatGPTの構造化JSONを検証し、確認用ドラフトとして安全に取り込みます。"""
    if approve and yes:
        _chat_import_error("--approve と --yes は同時に使用できません", output_json=output_json)
    if approve and output_json:
        _chat_import_error("--approve と --output-json は同時に使用できません", output_json=output_json)
    base = _root(root)
    requested_day = _day(date)
    try:
        content, source = _read_chat_import_input(
            json_text=json_text, file=file, clipboard=clipboard,
        )
        payload, warnings = validate_chat_payload(extract_chat_json(content))
    except ChatSchemaError as exc:
        _chat_import_error(str(exc), output_json=output_json, code=3)
        return
    except ValueError as exc:
        _chat_import_error(str(exc), output_json=output_json)
        return
    except OSError as exc:
        _chat_import_error(str(exc), output_json=output_json, code=4)
        return
    day = payload["date"]
    if date is not None and requested_day != day:
        _chat_import_error(
            f"日付が一致しません\nCLI指定: {requested_day}\nJSON: {day}", output_json=output_json,
        )
    content_hash = import_hash(payload)
    try:
        existing_draft = load_draft(base, day)
    except (OSError, ValueError) as exc:
        _chat_import_error(f"既存ドラフトを読み込めません: {exc}", output_json=output_json, code=3)
        return
    if existing_draft:
        if existing_draft.get("status") == "approved":
            _chat_import_error(
                "この日はすでに承認済みです。安全のため置き換えません\n"
                f"既存の再編集・再承認フロー: daily-review reflect --date {day} --resume",
                output_json=output_json,
            )
        if existing_draft.get("import_hash") == content_hash and not force:
            _chat_import_error("同じ内容はすでに取り込み済みです", output_json=output_json)
        if not force:
            _chat_import_error(
                f"未承認ドラフトがあります。確認を再開してください: daily-review reflect --date {day} --resume\n"
                "置き換える場合は --force を指定してください",
                output_json=output_json,
            )
    if daily_path(base, day).exists():
        _chat_import_error("既存の日次データがあります。安全のため取り込みません", output_json=output_json)

    virtual_draft = build_chat_import_draft(
        payload,
        input_id=f"dry-run-{day.replace('-', '')}",
        content_hash=content_hash,
        warnings=warnings,
    )
    if dry_run:
        if output_json:
            _chat_import_json_result(
                day=day, source=source, draft=virtual_draft, input_id=None, input_saved=False,
                approved=False, dry_run=True, warnings=warnings, root=base,
            )
        else:
            _print_chat_import_result(
                base=base, day=day, input_id=None, draft=virtual_draft, source=source,
                warnings=warnings, dry_run=True, backup_path=None,
            )
        return

    backup_path: Path | None = None
    try:
        if existing_draft:
            backup_path = backup_unapproved_draft(base, day)
        _, input_id = _store_natural_input(base, day, payload["raw_text"], "chat_import")
        draft = build_chat_import_draft(payload, input_id=input_id, content_hash=content_hash, warnings=warnings)
        atomic_write_json_data(draft_path(base, day), draft)
    except (OSError, ValueError) as exc:
        _chat_import_error(f"構造化入力を保存できません: {exc}", output_json=output_json, code=4)
        return

    if yes:
        reason = _chat_auto_approval_reason(base, day, draft, warnings)
        if reason:
            _chat_import_error(
                f"自動承認できません\n理由: {reason}\ndaily-review reflect --date {day} --resume",
                output_json=output_json,
            )
        try:
            approval = approve_draft(base, day, draft)
        except (OSError, ValueError) as exc:
            _chat_import_error(f"承認内容を保存できません: {exc}", output_json=output_json, code=3)
            return
        if output_json:
            _chat_import_json_result(
                day=day, source=source, draft=draft, input_id=input_id, input_saved=True,
                approved=True, dry_run=False, warnings=warnings, backup_path=backup_path, root=base,
            )
        else:
            _print_chat_import_result(
                base=base, day=day, input_id=input_id, draft=draft, source=source,
                warnings=warnings, dry_run=False, backup_path=backup_path,
            )
            _reflect_approved_message(day, approval)
        return

    if output_json:
        _chat_import_json_result(
            day=day, source=source, draft=draft, input_id=input_id, input_saved=True,
            approved=False, dry_run=False, warnings=warnings, backup_path=backup_path, root=base,
        )
    else:
        _print_chat_import_result(
            base=base, day=day, input_id=input_id, draft=draft, source=source,
            warnings=warnings, dry_run=False, backup_path=backup_path,
        )
    if approve:
        reflect(date=day, text=None, clipboard=False, resume=True, yes=False, dry_run=False, json_output=False, root=base)


def _chat_prompt_for_day(base: Path, day: str, summary: dict[str, Any], *, create_priorities: bool) -> str:
    path = base / "templates" / CHAT_IMPORT_PROMPT_NAME
    if not path.is_file():
        raise FileNotFoundError(f"テンプレートがありません: templates/{CHAT_IMPORT_PROMPT_NAME}")
    template = path.read_text(encoding="utf-8")
    try:
        priorities = load_priorities(base, create=create_priorities)
    except FileNotFoundError:
        # --prompt-only must remain read-only even in an old workspace.
        priorities = list(DEFAULT_PRIORITIES["priorities"])
    return build_dynamic_prompt(base, day, summary, template, priorities)


def _save_chat_session(base: Path, day: str, status: str, **updates: Any) -> None:
    try:
        save_session(base, day, status, **updates)
    except (OSError, ValueError) as exc:
        typer.echo(f"WARN: chat sessionを保存できません: {exc}", err=True)


def _print_chat_completed(base: Path, day: str) -> None:
    entry = load_daily(base, day) or {}
    approval = entry.get("draft_approval") if isinstance(entry.get("draft_approval"), dict) else {}
    today_main = approval.get("today_main") if isinstance(approval.get("today_main"), list) else []
    proposal = entry.get("tomorrow_plan_proposal") or {}
    tomorrow_main = proposal.get("main") if isinstance(proposal.get("main"), list) else []
    minimums = [
        task.get("minimum_line") for task in proposal.get("tasks") or []
        if isinstance(task, dict) and isinstance(task.get("minimum_line"), str)
    ]
    typer.echo("今日の振り返りを保存しました")
    typer.echo(f"日付: {day}")
    typer.echo(f"今日のMain: {len(today_main)}件")
    typer.echo(f"明日のMain: {len(tomorrow_main)}件")
    typer.echo(f"保存先: {daily_path(base, day).relative_to(base)}")
    typer.echo("明日のMain:")
    for index, item in enumerate(tomorrow_main, start=1):
        typer.echo(f"{index}. {item}")
    if minimums:
        typer.echo("最低ライン:")
        for item in minimums:
            typer.echo(f"- {item}")


def _chat_resume(base: Path, day: str) -> None:
    """Delegate approval and editing to the existing reflect resume flow."""
    try:
        draft = load_draft(base, day)
    except (OSError, ValueError) as exc:
        _input_error(f"整理ドラフトを読み込めません: {exc}")
        return
    if not draft:
        _input_error(f"再開できるドラフトがありません\ndaily-review chat --date {day} を実行してください")
    if draft.get("status") == "approved":
        typer.echo(f"{day}の振り返りはすでに完了しています")
        return
    reflect(date=day, text=None, clipboard=False, resume=True, yes=False, dry_run=False, json_output=False, root=base)
    try:
        current = load_draft(base, day)
    except (OSError, ValueError):
        current = None
    if current and current.get("status") == "approved":
        _save_chat_session(base, day, "approved", completed_at=now_iso(), draft_path=str(draft_path(base, day).relative_to(base)))
        _print_chat_completed(base, day)
    elif current:
        _save_chat_session(base, day, "draft", draft_path=str(draft_path(base, day).relative_to(base)))
        typer.echo("未承認ドラフトとして保存しました")
        typer.echo(f"再開: daily-review chat --date {day} --resume")


def _chat_import_and_optionally_review(
    *,
    base: Path,
    day: str,
    clipboard: bool,
    file: Path | None,
    json_text: str | None,
    dry_run: bool,
    yes: bool,
    force: bool,
    review_after: bool,
) -> None:
    try:
        chat_import(
            date=day,
            clipboard=clipboard,
            file=file,
            json_text=json_text,
            dry_run=dry_run,
            approve=False,
            yes=yes,
            output_json=False,
            force=force,
            root=base,
        )
    except typer.Exit as exc:
        if exc.exit_code:
            typer.echo("次の操作:")
            typer.echo(f"daily-review chat --date {day} --import-only --clipboard")
        raise
    if dry_run:
        return
    try:
        draft = load_draft(base, day)
    except (OSError, ValueError):
        draft = None
    if draft and draft.get("status") == "approved":
        _save_chat_session(base, day, "approved", imported_at=now_iso(), draft_path=str(draft_path(base, day).relative_to(base)), completed_at=now_iso())
        _print_chat_completed(base, day)
        return
    _save_chat_session(base, day, "imported", imported_at=now_iso(), draft_path=str(draft_path(base, day).relative_to(base)))
    _save_chat_session(base, day, "draft", draft_path=str(draft_path(base, day).relative_to(base)))
    if review_after:
        _chat_resume(base, day)


def _read_chat_paste() -> str:
    typer.echo("JSONを貼り付けてください。最後の行に __END__、または空行を2回入力すると終了します。")
    lines: list[str] = []
    blank_lines = 0
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        if line.rstrip("\r\n") == "__END__":
            break
        if not line.strip():
            blank_lines += 1
            if blank_lines >= 2:
                break
        else:
            blank_lines = 0
        lines.append(line)
    value = "".join(lines)
    if not value.strip():
        _input_error("ChatGPT連携用JSONを指定してください")
    return value


@app.command("chat")
def chat(
    date: str | None = DateOption,
    resume: bool = typer.Option(False, "--resume", help="未承認ドラフトの確認・修正・承認を再開する"),
    prompt_only: bool = typer.Option(False, "--prompt-only", help="動的プロンプトだけを表示する"),
    copy_prompt: bool = typer.Option(False, "--copy-prompt", help="プロンプトをクリップボードへコピーする"),
    import_only: bool = typer.Option(False, "--import-only", help="プロンプトを省略してJSONの取り込みから開始する"),
    clipboard: bool = typer.Option(False, "--clipboard", help="macOSのクリップボードからJSONを読み込む"),
    file: Path | None = typer.Option(None, "--file", help="ChatGPT出力のUTF-8 JSONファイル"),
    json_text: str | None = typer.Option(None, "--json-text", help="ChatGPTのJSONまたはJSONを含む文章"),
    dry_run: bool = typer.Option(False, "--dry-run", help="JSONを検証・表示するだけで保存しない"),
    yes: bool = typer.Option(False, "--yes", help="安全条件を満たす場合のみ確認なしで承認する"),
    root: Path | None = RootOption,
) -> None:
    """ChatGPTとの日次往復を、既存の安全な取り込み・承認処理で進めます。"""
    if sum((resume, prompt_only, import_only)) > 1:
        _input_error("--resume、--prompt-only、--import-only は同時に使用できません")
    if (clipboard or file is not None or json_text is not None or yes or dry_run) and not import_only:
        _input_error("JSON入力オプション、--yes、--dry-run は --import-only と併用してください")
    base = _root(root)
    day = _day(date)
    try:
        draft = load_draft(base, day)
    except (OSError, ValueError) as exc:
        typer.echo(f"ERROR: 整理ドラフトを読み込めません: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    state = workflow_state(base, day, draft)
    if state == "approved":
        typer.echo(f"{day}の振り返りはすでに完了しています")
        typer.echo(f"daily-review summary --date {day}")
        return
    if state == "daily_only":
        typer.echo("ERROR: 日次データは存在しますが、承認状態を確認できません", err=True)
        typer.echo("daily-review doctor を実行してください", err=True)
        raise typer.Exit(code=3)
    if resume:
        _chat_resume(base, day)
        return
    if import_only:
        _chat_import_and_optionally_review(
            base=base, day=day, clipboard=clipboard, file=file, json_text=json_text,
            dry_run=dry_run, yes=yes, force=False, review_after=False,
        )
        return

    try:
        summary = build_daily_summary(base, day)
        prompt = _chat_prompt_for_day(base, day, summary, create_priorities=not prompt_only)
    except (OSError, ValueError) as exc:
        typer.echo(f"ERROR: ChatGPT用プロンプトを生成できません: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    typer.echo(f"daily-review chat｜{day}")
    if prompt_only:
        typer.echo(prompt, nl=False)
        if copy_prompt:
            if _copy_chat_prompt(prompt):
                typer.echo("ChatGPT用プロンプトをコピーしました")
            else:
                typer.echo("WARN: コピーできなかったため、上のプロンプトを手動でコピーしてください")
        return
    if state == "draft":
        typer.echo(f"{day}には未承認のドラフトがあります。")
        typer.echo("[r] 既存ドラフトを再開  [n] 新しいChatGPT入力を取り込む  [q] 終了")
        choice = typer.prompt("選択", default="r").strip().lower()
        if choice == "r":
            _chat_resume(base, day)
            return
        if choice == "q":
            _save_chat_session(base, day, "draft", draft_path=str(draft_path(base, day).relative_to(base)))
            typer.echo("未承認ドラフトとして保存しました")
            return
        if choice != "n":
            _input_error("r、n、qのいずれかを入力してください")
        force_import = True
    else:
        typer.echo("ChatGPTで今日の振り返りを整理します。")
        force_import = False
    typer.echo(prompt, nl=False)
    _save_chat_session(base, day, "waiting_for_chatgpt", **{
        "prompt_generated_at": now_iso(),
        "prompt_hash": prompt_hash(prompt),
        "imported_at": None,
        "draft_path": None,
        "completed_at": None,
    })
    should_copy = copy_prompt or typer.confirm("ChatGPT用プロンプトをクリップボードへコピーしますか？", default=True)
    if should_copy:
        if _copy_chat_prompt(prompt):
            typer.echo("ChatGPT用プロンプトをコピーしました")
            typer.echo("ChatGPTへ貼り付けて、今日の振り返りを書いてください")
        else:
            typer.echo("WARN: コピーできなかったため、上のプロンプトを手動でコピーしてください")
    typer.echo("ChatGPTから返されたJSONを取り込みます。")
    typer.echo("[c] クリップボードから読み込む  [p] この画面へ貼り付ける  [f] JSONファイルを指定する  [q] 終了")
    source = typer.prompt("選択", default="c").strip().lower()
    if source == "q":
        _save_chat_session(base, day, "cancelled")
        typer.echo("ChatGPT連携を終了しました")
        return
    if source == "c":
        values = {"clipboard": True, "file": None, "json_text": None}
    elif source == "p":
        values = {"clipboard": False, "file": None, "json_text": _read_chat_paste()}
    elif source == "f":
        values = {"clipboard": False, "file": Path(typer.prompt("JSONファイルのパス")), "json_text": None}
    else:
        _input_error("c、p、f、qのいずれかを入力してください")
        return
    _chat_import_and_optionally_review(
        base=base, day=day, dry_run=False, yes=False, force=force_import, review_after=True, **values,
    )


def _handoff_error(
    message: str,
    *,
    day: str | None = None,
    code: int = 2,
    next_command: str | None = None,
) -> None:
    typer.echo(f"ERROR: {message}", err=True)
    if next_command:
        typer.echo("次の操作:", err=True)
        typer.echo(next_command, err=True)
    elif day:
        typer.echo("次の操作:", err=True)
        typer.echo(f"daily-review handoff --date {day}", err=True)
    raise typer.Exit(code=code)


@app.command("handoff")
def handoff(
    date: str | None = DateOption,
    copy: bool = typer.Option(False, "--copy", help="完成したhandoffをmacOSのクリップボードへコピーする"),
    output: Path | None = typer.Option(None, "--output", help="handoffを書き出す新規テキストファイル"),
    root: Path | None = RootOption,
) -> None:
    """ChatGPTへ渡す、日付・ハッシュ付きの安全なhandoffを発行します。"""
    base = _root(root)
    day = _day(date)
    if daily_path(base, day).exists():
        _handoff_error("この日の日次データはすでに存在します", day=day)
    try:
        summary = build_daily_summary(base, day)
        prompt = _chat_prompt_for_day(base, day, summary, create_priorities=True)
        item = issue_handoff(base, day, prompt, prompt_hash(prompt))
    except (OSError, ValueError, HandoffError) as exc:
        _handoff_error(f"handoffを生成できません: {exc}", day=day, code=3)
        return
    package = render_handoff(day, prompt, item)
    if output:
        if output.exists():
            _handoff_error(f"出力先がすでに存在します: {output}", day=day)
        try:
            write_text(output.expanduser(), package)
        except OSError as exc:
            _handoff_error(f"出力ファイルを保存できません: {exc}", day=day, code=4)
    typer.echo(package, nl=False)
    _save_chat_session(
        base,
        day,
        "waiting_for_chatgpt",
        prompt_generated_at=now_iso(),
        prompt_hash=item["prompt_hash"],
        handoff_session_id=item["session_id"],
        imported_at=None,
        draft_path=None,
        completed_at=None,
    )
    if copy:
        if _copy_chat_prompt(package):
            typer.echo("ChatGPT用handoffをクリップボードへコピーしました")
            typer.echo(f"対象日: {day}")
            typer.echo(f"session_id: {item['session_id']}")
            typer.echo("ChatGPTへ貼り付けてください。")
        else:
            typer.echo("WARN: コピーできなかったため、上のhandoffを手動でコピーしてください")
    if output:
        typer.echo(f"出力先: {output.expanduser()}")


@app.command("handoff-list")
def handoff_list(
    date: str | None = DateOption,
    latest: bool = typer.Option(False, "--latest", help="最新の有効handoffだけを表示する"),
    json_output: bool = typer.Option(False, "--json", help="一覧をJSONだけで出力する"),
    root: Path | None = RootOption,
) -> None:
    """指定日のhandoff発行履歴を表示します。"""
    base = _root(root)
    day = _day(date)
    try:
        items = list_handoffs(base, day, latest=latest)
    except (OSError, HandoffError, ValueError) as exc:
        _handoff_error(f"handoff一覧を読み込めません: {exc}", day=day, code=3)
        return
    if json_output:
        typer.echo(json.dumps({"date": day, "handoffs": items}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"handoff一覧｜{day}")
    if not items:
        typer.echo("なし")
        return
    for index, item in enumerate(items, start=1):
        typer.echo(f"{index}. {item.get('session_id', '不明')}")
        typer.echo(f"   状態: {item.get('status', '不明')}")
        typer.echo(f"   作成: {item.get('created_at', '不明')}")
        typer.echo(f"   期限: {item.get('expires_at', '不明')}")


@app.command("handoff-cancel")
def handoff_cancel(
    date: str | None = DateOption,
    session_id: str | None = typer.Option(None, "--session-id", help="キャンセルするhandoffのsession_id"),
    latest: bool = typer.Option(False, "--latest", help="最新の未承認handoffをキャンセルする"),
    root: Path | None = RootOption,
) -> None:
    """未承認handoffをキャンセルし、以後の受信を拒否します。"""
    base = _root(root)
    day = _day(date)
    if session_id and latest:
        _handoff_error("--session-id と --latest は同時に使用できません", day=day)
    try:
        item = cancel_handoff(base, day, session_id=session_id, latest=latest)
    except (OSError, HandoffError, ValueError) as exc:
        _handoff_error(str(exc), day=day, code=3)
        return
    typer.echo("handoffをキャンセルしました")
    typer.echo(f"session_id: {item['session_id']}")


@app.command("receive")
def receive(
    date: str | None = DateOption,
    clipboard: bool = typer.Option(False, "--clipboard", help="macOSのクリップボードからChatGPT回答を読み込む"),
    file: Path | None = typer.Option(None, "--file", help="ChatGPT回答のUTF-8ファイル"),
    json_text: str | None = typer.Option(None, "--json-text", help="ChatGPT回答のJSONまたはJSONを含む文章"),
    dry_run: bool = typer.Option(False, "--dry-run", help="検証・表示だけを行い保存しない"),
    approve: bool = typer.Option(False, "--approve", help="受信後に既存の確認・承認フローを開始する"),
    yes: bool = typer.Option(False, "--yes", help="安全条件を満たす場合のみ確認なしで承認する"),
    force: bool = typer.Option(False, "--force", help="未承認ドラフトまたは受信済みhandoffを安全に再処理する"),
    allow_expired: bool = typer.Option(False, "--allow-expired", help="期限切れhandoffの受信を明示的に許可する"),
    root: Path | None = RootOption,
) -> None:
    """handoffに結び付いたChatGPT回答を検証して安全に取り込みます。"""
    if approve and yes:
        _handoff_error("--approve と --yes は同時に使用できません")
    base = _root(root)
    requested_day = _day(date) if date is not None else None
    try:
        content, _ = _read_chat_import_input(json_text=json_text, file=file, clipboard=clipboard)
        payload, _warnings, manifest, item, handoff_info, content_hash = prepare_receive(
            base,
            content,
            requested_day=requested_day,
            allow_expired=allow_expired,
            force=force,
        )
    except (ChatSchemaError, HandoffError, ValueError, typer.BadParameter) as exc:
        day_hint = requested_day
        _handoff_error(
            str(exc),
            day=day_hint,
            code=3,
            next_command="daily-review receive --file response.json" if clipboard else None,
        )
        return
    except OSError as exc:
        _handoff_error(f"回答を読み込めません: {exc}", day=requested_day, code=4)
        return
    day = payload["date"]
    if yes and (item.get("status") != "issued" or is_expired(item)):
        _handoff_error("自動承認できません。handoffが発行済みかつ有効期限内である必要があります", day=day)
    if daily_path(base, day).exists():
        _handoff_error("同日の日次データがすでに存在します", day=day)
    if dry_run:
        try:
            chat_import(
                date=day, clipboard=False, file=None, json_text=content, dry_run=True,
                approve=False, yes=False, output_json=False, force=force, root=base,
            )
        except typer.Exit:
            raise
        typer.echo(f"handoff session_id: {handoff_info['session_id']}")
        return
    try:
        chat_import(
            date=day, clipboard=False, file=None, json_text=content, dry_run=False,
            approve=False, yes=yes, output_json=False, force=force, root=base,
        )
    except typer.Exit as exc:
        if exc.exit_code:
            typer.echo("次の操作:", err=True)
            typer.echo(f"daily-review receive --date {day} --clipboard", err=True)
        raise
    try:
        draft = load_draft(base, day)
        approved = bool(draft and draft.get("status") == "approved")
        update_handoff(base, day, manifest, item, status="approved" if approved else "received", content_hash=content_hash)
    except (OSError, ValueError, HandoffError) as exc:
        _handoff_error(f"handoff状態を更新できません: {exc}", day=day, code=4)
        return
    _save_chat_session(
        base,
        day,
        "approved" if approved else "draft",
        handoff_session_id=handoff_info["session_id"],
        imported_at=now_iso(),
        draft_path=str(draft_path(base, day).relative_to(base)),
        completed_at=now_iso() if approved else None,
    )
    typer.echo("ChatGPTの回答を受信しました")
    typer.echo(f"対象日: {day}")
    typer.echo(f"session_id: {handoff_info['session_id']}")
    if approved:
        _print_chat_completed(base, day)
        return
    typer.echo("状態: 未承認")
    typer.echo("次の操作:")
    typer.echo(f"daily-review chat --date {day} --resume")
    if approve:
        _chat_resume(base, day)
        try:
            refreshed = load_draft(base, day)
        except (OSError, ValueError):
            refreshed = None
        if refreshed and refreshed.get("status") == "approved":
            update_handoff(base, day, manifest, item, status="approved", content_hash=content_hash)


def _print_organize_result(result: dict[str, Any], base: Path, day: str, *, dry_run: bool) -> None:
    draft = result["draft"]
    if dry_run:
        typer.echo("daily-review organize｜dry-run")
    elif result["changed"]:
        typer.echo("入力を整理しました")
    else:
        typer.echo("この日の入力はすでに整理済みです")
    typer.echo(f"日付: {day}")
    typer.echo(f"対象入力: {result['entry_count']}件")
    typer.echo(f"今回整理: {result['new_entry_count']}件")
    typer.echo(f"分類済み: {result['classified_count']}文")
    typer.echo(f"未分類: {result['unclassified_count']}文")
    typer.echo(f"保存先: {result['path'].relative_to(base)}")
    for title, candidates in (("今日のMain候補", draft["today"]["main_candidates"]),
                              ("明日のMain候補", draft["tomorrow"]["main_candidates"])):
        typer.echo(f"{title}:")
        if candidates:
            for index, candidate in enumerate(candidates, start=1):
                typer.echo(f"{index}. {candidate}")
        else:
            typer.echo("未記録")
    if dry_run:
        typer.echo("保存は行いませんでした")


@app.command("organize")
def organize(
    date: str | None = DateOption,
    dry_run: bool = typer.Option(False, "--dry-run", help="保存せずに整理結果だけを表示する"),
    force: bool = typer.Option(False, "--force", help="既存ドラフトを全入力から安全に作り直す"),
    json_output: bool = typer.Option(False, "--json", help="整理ドラフトをJSONで表示する"),
    root: Path | None = RootOption,
) -> None:
    """inboxの原文をルールベースで整理ドラフトへ保存します。"""
    base = _root(root)
    day = _day(date)
    try:
        result = organize_day(base, day, force=force)
    except LookupError:
        typer.echo(f"ERROR: {day}の入力がありません", err=True)
        typer.echo("先に daily-review input を実行してください", err=True)
        raise typer.Exit(code=2)
    except (OSError, ValueError) as exc:
        typer.echo(f"ERROR: 整理ドラフトを作成できません: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    if not dry_run and result["changed"]:
        try:
            atomic_write_json_data(result["path"], result["draft"])
        except OSError as exc:
            typer.echo(f"ERROR: 整理ドラフトを保存できません: {result['path']} ({exc})", err=True)
            raise typer.Exit(code=4) from exc
    if json_output:
        typer.echo(json.dumps(result["draft"], ensure_ascii=False, indent=2))
    else:
        _print_organize_result(result, base, day, dry_run=dry_run)


def _draft_or_error(base: Path, day: str) -> dict[str, Any]:
    try:
        draft = load_draft(base, day)
    except (OSError, ValueError) as exc:
        typer.echo(f"ERROR: 整理ドラフトを読み込めません: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    if not draft:
        typer.echo(f"ERROR: {day}の整理ドラフトがありません", err=True)
        typer.echo("先に daily-review organize を実行してください", err=True)
        raise typer.Exit(code=2)
    return draft


def _print_draft_list(title: str, values: list[str], *, numbered: bool = False) -> None:
    typer.echo(f"{title}:")
    if not values:
        typer.echo("なし")
        return
    for index, value in enumerate(values, start=1):
        typer.echo(f"{index}. {value}" if numbered else f"- {value}")


def _print_draft_review(draft: dict[str, Any], day: str) -> None:
    today = draft["today"]
    reflection = draft["reflection"]
    tomorrow = draft["tomorrow"]
    typer.echo(f"daily-review review｜{day}")
    _print_draft_list("今日のMain候補", today["main_candidates"], numbered=True)
    _print_draft_list("完了", today["completed"])
    _print_draft_list("一部進行", today["partial"])
    _print_draft_list("未完了", today["not_completed"])
    _print_draft_list("良かったこと", reflection["good"])
    _print_draft_list("問題", reflection["problems"])
    _print_draft_list("原因", reflection["causes"])
    _print_draft_list("明日変えること", reflection["change_next"])
    _print_draft_list("明日のMain候補", tomorrow["main_candidates"], numbered=True)
    _print_draft_list("明日のその他タスク", tomorrow["other_tasks"])
    _print_draft_list("最低ライン候補", tomorrow["minimum_candidates"])
    _print_draft_list("日記", draft["journal"])
    _print_draft_list("未分類", draft["unclassified"])
    approved = draft.get("status") == "approved"
    typer.echo(f"状態: {'承認済み' if approved else '未承認'}")
    if approved:
        typer.echo(f"確定先: {draft.get('approved_daily_path') or '未記録'}")
    else:
        typer.echo("次の操作:")
        typer.echo(f"daily-review approve --date {day}")


@app.command("review")
def review(
    mode: str | None = typer.Argument(None, help="quick を指定すると日次レビューを簡単入力"),
    date: str | None = DateOption,
    json_output: bool = typer.Option(False, "--json", help="ドラフトJSONをそのまま表示する"),
    dry_run: bool = typer.Option(False, "--dry-run", help="表示のみでファイルを変更しない"),
    done: list[str] = typer.Option([], "--done", help="今日できたこと。複数指定可"),
    not_done: list[str] = typer.Option([], "--not-done", help="できなかったこと。複数指定可"),
    cause: list[str] = typer.Option([], "--cause", help="崩れた原因。複数指定可"),
    tomorrow: list[str] = typer.Option([], "--tomorrow", help="明日やること。入力順の上位3件をMain候補にする"),
    minimum: list[str] = typer.Option([], "--minimum", help="明日の最低限。複数指定可"),
    journal: str | None = typer.Option(None, "--journal", help="任意の日記（改行可）"),
    stdin: bool = typer.Option(False, "--stdin", help="標準入力からクイックレビューJSONを読み込む"),
    force: bool = typer.Option(False, "--force", help="同日レビューをバックアップ後に更新"),
    root: Path | None = RootOption,
) -> None:
    """整理ドラフトを表示します。`review quick`で簡単入力できます。"""
    base = _root(root)
    day = _day(date)
    if mode is not None:
        if mode != "quick":
            typer.echo("ERROR: reviewのモードは quick のみ指定できます", err=True)
            raise typer.Exit(code=2)
        option_used = any((done, not_done, cause, tomorrow, minimum, journal is not None))
        if stdin and option_used:
            typer.echo("ERROR: --stdinと個別入力オプションは同時に指定できません", err=True)
            raise typer.Exit(code=2)
        raw_input = ""
        try:
            if stdin:
                raw_input = sys.stdin.read()
                if not raw_input.strip():
                    raise QuickReviewError("標準入力が空です")
                payload = json.loads(raw_input)
            elif option_used:
                payload = {"date": day, "done": done, "not_done": not_done, "causes": cause, "tomorrow": tomorrow, "minimum": minimum, "journal": journal or ""}
                raw_input = json.dumps(payload, ensure_ascii=False, indent=2)
            else:
                payload = {
                    "date": day,
                    "done": [value] if (value := typer.prompt("今日できたこと", default="", show_default=False)) else [],
                    "not_done": [value] if (value := typer.prompt("できなかったこと", default="", show_default=False)) else [],
                    "causes": [value] if (value := typer.prompt("崩れた原因", default="", show_default=False)) else [],
                    "tomorrow": [value] if (value := typer.prompt("明日やること", default="", show_default=False)) else [],
                    "minimum": [value] if (value := typer.prompt("明日の最低限", default="", show_default=False)) else [],
                    "journal": typer.prompt("日記（任意）", default="", show_default=False),
                }
                raw_input = json.dumps(payload, ensure_ascii=False, indent=2)
            normalized = normalize_quick_payload(payload, day=day)
            planned = build_quick_entry(day, normalized, load_daily(base, day))
        except json.JSONDecodeError as exc:
            typer.echo(f"ERROR: 標準入力のJSONが不正です: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        except (QuickReviewError, OSError, ValueError) as exc:
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        if json_output and dry_run:
            typer.echo(json.dumps({"date": day, "dry_run": True, "entry": planned}, ensure_ascii=False, indent=2))
            return
        elif dry_run:
            typer.echo(f"daily-review review quick｜dry-run｜{day}")
            typer.echo(f"完了: {len(normalized['done'])}件 / 未完了: {len(normalized['not_done'])}件")
            typer.echo(f"明日のMain候補: {len(planned['tomorrow_plan_proposal']['main'])}件 / 保留: {len(planned['quick_review']['backlog_candidates'])}件")
            typer.echo("保存は行いませんでした")
            return
        try:
            result = save_quick_review(base, day, normalized, raw_input=raw_input, force=force)
        except QuickReviewError as exc:
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(code=4) from exc
        except OSError as exc:
            raw_saved = inbox_path(base, day).exists()
            detail = "原入力はinboxに保存済みです。" if raw_saved else "原入力の保存にも失敗しました。"
            typer.echo(f"ERROR: クイックレビューを完了できません: {exc}\n{detail}", err=True)
            raise typer.Exit(code=4) from exc
        if json_output:
            typer.echo(json.dumps({"date": day, "dry_run": False, "input_id": result["input_id"], "entry": result["entry"]}, ensure_ascii=False, indent=2))
        else:
            typer.echo("クイックレビューを保存しました")
            typer.echo(f"日付: {day}")
            typer.echo(f"入力ID: {result['input_id']}")
            typer.echo(f"明日のMain候補: {len(result['entry']['tomorrow_plan_proposal']['main'])}件")
            typer.echo(f"次の操作: daily-review approve-plan --date {day}")
        return
    draft = _draft_or_error(base, day)
    if json_output:
        typer.echo(json.dumps(draft, ensure_ascii=False, indent=2))
        return
    _print_draft_review(draft, day)
    if dry_run:
        typer.echo("dry-runのためファイルを変更していません。")


def _parse_draft_set_values(values: list[str]) -> dict[str, list[str]]:
    replacements: dict[str, list[str]] = {}
    for item in values:
        if "=" not in item:
            raise ValueError("--set は field=value の形式で指定してください")
        field, value = item.split("=", 1)
        if field not in EDITABLE_DRAFT_FIELDS:
            raise ValueError(f"編集できないフィールドです: {field}")
        replacements.setdefault(field, []).append(value)
    return replacements


def _interactive_draft_replacements(draft: dict[str, Any]) -> dict[str, list[str]]:
    replacements: dict[str, list[str]] = {}
    for field in EDITABLE_DRAFT_FIELDS:
        group, key = field.split(".", 1) if "." in field else (None, field)
        values = draft[group][key] if group else draft[key]
        typer.echo(f"\n{field} の現在の内容:")
        for index, value in enumerate(values, start=1):
            typer.echo(f"{index}. {value}")
        if not typer.confirm("変更しますか？", default=False):
            continue
        typer.echo("新しい値を1行ずつ入力してください。空行で終了（最初の空行で削除）:")
        new_values: list[str] = []
        while True:
            value = typer.prompt("", default="", show_default=False)
            if not value:
                break
            new_values.append(value)
        replacements[field] = new_values
    return replacements


@app.command("edit-draft")
def edit_draft(
    date: str | None = DateOption,
    set_values: list[str] = typer.Option([], "--set", help="field=value。複数指定時はその配列で置換する"),
    force: bool = typer.Option(False, "--force", help="承認済みドラフトを編集可能に戻す"),
    root: Path | None = RootOption,
) -> None:
    """整理ドラフトの許可済み配列フィールドを置換編集します。"""
    base = _root(root)
    day = _day(date)
    draft = _draft_or_error(base, day)
    try:
        replacements = _parse_draft_set_values(set_values) if set_values else _interactive_draft_replacements(draft)
        if not replacements:
            typer.echo("変更はありません。")
            return
        changed = replace_draft_fields(draft, replacements, force=force)
    except PermissionError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not changed:
        typer.echo("変更はありません。")
        return
    if not set_values and not typer.confirm("この変更を保存しますか？", default=False):
        typer.echo("編集をキャンセルしました")
        return
    try:
        atomic_write_json_data(draft_path(base, day), draft)
    except OSError as exc:
        typer.echo(f"ERROR: 整理ドラフトを保存できません: {exc}", err=True)
        raise typer.Exit(code=4) from exc
    typer.echo("整理ドラフトを更新しました")
    typer.echo("変更項目: " + ", ".join(changed))
    typer.echo(f"revision: {draft['revision']}")


def _print_approval_preview(draft: dict[str, Any]) -> None:
    typer.echo("以下の内容を確定します。")
    _print_draft_list("今日のMain", draft["today"]["main_candidates"], numbered=True)
    _print_draft_list("明日のMain", draft["tomorrow"]["main_candidates"], numbered=True)


@app.command("approve")
def approve(
    date: str | None = DateOption,
    yes: bool = typer.Option(False, "--yes", help="確認を省略して承認する"),
    force: bool = typer.Option(False, "--force", help="承認済みドラフトをバックアップ後に再承認する"),
    root: Path | None = RootOption,
) -> None:
    """確認済みの整理ドラフトを日次記録と翌日提案へ保存します。"""
    base = _root(root)
    day = _day(date)
    draft = _draft_or_error(base, day)
    if draft.get("status") not in {"draft", "approved"}:
        typer.echo("ERROR: 整理ドラフトのstatusが不正です", err=True)
        raise typer.Exit(code=3)
    if draft.get("status") == "approved" and not force:
        typer.echo("このドラフトはすでに承認済みです")
        typer.echo(f"確定先: {draft.get('approved_daily_path') or '未記録'}")
        return
    if not yes:
        if _stdin_is_piped():
            typer.echo("ERROR: 非対話環境では --yes なしで承認できません", err=True)
            raise typer.Exit(code=2)
        _print_approval_preview(draft)
        if not typer.confirm("確定しますか？", default=False):
            typer.echo("承認をキャンセルしました")
            return
    try:
        result = approve_draft(base, day, draft, force=force)
    except ValueError as exc:
        typer.echo(f"ERROR: ドラフトを日次データへ変換できません: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except OSError as exc:
        typer.echo(f"ERROR: 承認内容を保存できません: {exc}", err=True)
        raise typer.Exit(code=4) from exc
    typer.echo(f"ドラフトを承認しました｜{day}")
    typer.echo(f"確定先: {draft['approved_daily_path']}")
    typer.echo(f"Markdownを更新しました: {result['markdown_path']}")
    if result["backup_path"]:
        typer.echo(f"再承認前バックアップ: {result['backup_path'].relative_to(base)}")


def _reflect_json(
    *,
    day: str,
    draft: dict[str, Any] | None,
    input_saved: bool,
    input_id: str | None,
    approved: bool,
    daily_file: Path | None,
    dry_run: bool,
) -> None:
    typer.echo(json.dumps({
        "date": day,
        "status": (draft or {}).get("status", "draft"),
        "input_saved": input_saved,
        "input_id": input_id,
        "draft_path": f"data/drafts/{day}.json" if draft else None,
        "approved": approved,
        "daily_path": str(daily_file.relative_to(daily_file.parents[2])) if daily_file else None,
        "dry_run": dry_run,
        "draft": draft,
    }, ensure_ascii=False, indent=2))


def _reflect_error(message: str, *, json_output: bool, code: int = 2) -> None:
    if json_output:
        typer.echo(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    else:
        typer.echo(f"ERROR: {message}", err=True)
    raise typer.Exit(code=code)


def _recent_duplicate_input(root: Path, day: str, raw_text: str) -> bool:
    path = inbox_path(root, day)
    if not path.exists():
        return False
    try:
        payload = read_json_file(path)
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return False
        now = datetime.fromisoformat(now_iso())
        for entry in reversed(entries):
            if not isinstance(entry, dict) or entry.get("raw_text") != raw_text:
                continue
            created_at = entry.get("created_at")
            if not isinstance(created_at, str):
                continue
            if now - datetime.fromisoformat(created_at) <= timedelta(minutes=5):
                return True
    except (OSError, ValueError):
        return False
    return False


def _auto_approval_reason(root: Path, day: str, draft: dict[str, Any]) -> str | None:
    if draft.get("status") != "draft":
        return "承認済みまたは不正なドラフトです"
    if draft.get("unclassified"):
        return f"未分類の文章が{len(draft['unclassified'])}件あります"
    for field in ("today.main_candidates", "tomorrow.main_candidates"):
        group, key = field.split(".", 1)
        values = (draft.get(group) or {}).get(key)
        if not isinstance(values, list):
            return f"{field}が不正です"
        if len(values) > 3:
            return f"{field}が最大3件を超えています"
        if not values:
            label = "今日のMain候補" if group == "today" else "明日のMain候補"
            return f"{label}がありません"
    if daily_path(root, day).exists():
        return "既存の日次データがあります"
    try:
        build_daily_from_draft({}, day, draft)
    except ValueError as exc:
        return str(exc)
    return None


def _reflect_approved_message(day: str, result: dict[str, Path | None]) -> None:
    typer.echo("振り返りを確定しました")
    typer.echo(f"日付: {day}")
    typer.echo(f"保存先: {result['daily_path'].relative_to(result['daily_path'].parents[2])}")


def _reflect_interactive(base: Path, day: str, draft: dict[str, Any]) -> bool:
    """Review/edit/approval loop.  Returns whether daily data was saved."""
    for _ in range(20):
        typer.echo("この内容を確定しますか？")
        typer.echo("[y] 承認して確定  [e] ドラフトを編集  [n] 保存せず終了")
        choice = typer.prompt("選択", default="n").strip().lower()
        if choice == "y":
            try:
                result = approve_draft(base, day, draft)
            except (OSError, ValueError) as exc:
                typer.echo(f"ERROR: 承認内容を保存できません: {exc}", err=True)
                return False
            _reflect_approved_message(day, result)
            return True
        if choice == "n":
            typer.echo("確定せず終了しました")
            typer.echo("ドラフトは保存されています")
            typer.echo(f"再開: daily-review reflect --date {day} --resume")
            return False
        if choice == "e":
            edit_draft(date=day, set_values=[], force=False, root=base)
            draft = _draft_or_error(base, day)
            typer.echo("編集後の内容を表示します")
            _print_draft_review(draft, day)
            continue
        typer.echo("y、e、nのいずれかを入力してください")
    typer.echo("ERROR: 編集・確認の回数が上限に達しました。--resumeで再開してください", err=True)
    return False


@app.command("reflect")
def reflect(
    date: str | None = DateOption,
    text: str | None = typer.Option(None, "--text", help="保存する自然文"),
    clipboard: bool = typer.Option(False, "--clipboard", help="macOSのクリップボードから読み込む"),
    resume: bool = typer.Option(False, "--resume", help="既存の未承認ドラフトから再開する"),
    yes: bool = typer.Option(False, "--yes", help="安全条件を満たす場合のみ確認なしで承認する"),
    dry_run: bool = typer.Option(False, "--dry-run", help="保存せずに入力と整理結果だけを確認する"),
    json_output: bool = typer.Option(False, "--json", help="結果をJSONだけで出力する"),
    root: Path | None = RootOption,
) -> None:
    """自然文入力から整理・確認・承認までを一つの流れで進めます。"""
    base = _root(root)
    day = _day(date)
    if resume:
        if text is not None or clipboard:
            _reflect_error("--resume と --text/--clipboard は同時に使用できません", json_output=json_output)
        try:
            draft = load_draft(base, day)
        except (OSError, ValueError) as exc:
            _reflect_error(f"整理ドラフトを読み込めません: {exc}", json_output=json_output, code=3)
            return
        if not draft:
            _reflect_error("再開できるドラフトがありません\n先に daily-review reflect または daily-review organize を実行してください", json_output=json_output)
            return
        if draft.get("status") == "approved":
            if json_output:
                _reflect_json(day=day, draft=draft, input_saved=False, input_id=None, approved=True, daily_file=daily_path(base, day), dry_run=dry_run)
            else:
                typer.echo("この日の振り返りはすでに確定済みです")
                typer.echo(f"daily-review summary --date {day}")
            return
        if dry_run:
            if json_output:
                _reflect_json(day=day, draft=draft, input_saved=False, input_id=None, approved=False, daily_file=None, dry_run=True)
            else:
                typer.echo(f"daily-review reflect｜dry-run｜{day}")
                _print_draft_review(draft, day)
                typer.echo("保存は行いませんでした")
            return
        if yes:
            reason = _auto_approval_reason(base, day, draft)
            if reason:
                _reflect_error(f"自動承認できません\n理由: {reason}\ndaily-review reflect --date {day} --resume", json_output=json_output)
            try:
                result = approve_draft(base, day, draft)
            except (OSError, ValueError) as exc:
                _reflect_error(f"承認内容を保存できません: {exc}", json_output=json_output, code=3)
            if json_output:
                _reflect_json(day=day, draft=draft, input_saved=False, input_id=None, approved=True, daily_file=result["daily_path"], dry_run=False)
            else:
                _reflect_approved_message(day, result)
            return
        if json_output:
            _reflect_json(day=day, draft=draft, input_saved=False, input_id=None, approved=False, daily_file=None, dry_run=False)
            return
        _print_draft_review(draft, day)
        _reflect_interactive(base, day, draft)
        return

    try:
        existing_draft = load_draft(base, day)
    except (OSError, ValueError) as exc:
        _reflect_error(f"整理ドラフトを読み込めません: {exc}", json_output=json_output, code=3)
        return
    if existing_draft:
        if existing_draft.get("status") == "approved":
            _reflect_error("この日はすでに確定済みです。修正する場合は既存の編集・再承認フローを使用してください", json_output=json_output)
        _reflect_error(f"既存ドラフトがあります\ndaily-review reflect --date {day} --resume", json_output=json_output)
    if daily_path(base, day).exists():
        _reflect_error("既存の日次データがあります。安全のため自動上書きしません", json_output=json_output)

    try:
        raw_text, source = _read_natural_input(text, clipboard)
    except typer.BadParameter as exc:
        _reflect_error(str(exc), json_output=json_output)
        return
    if not raw_text.strip():
        _reflect_error("入力内容が空です", json_output=json_output)
    virtual_entry = {"id": f"dry-run-{day.replace('-', '')}", "created_at": now_iso(), "source": source, "raw_text": raw_text}
    if dry_run:
        try:
            result = organize_entries(day, [virtual_entry])
        except ValueError as exc:
            _reflect_error(f"整理できません: {exc}", json_output=json_output, code=3)
            return
        draft = result["draft"]
        if json_output:
            _reflect_json(day=day, draft=draft, input_saved=False, input_id=None, approved=False, daily_file=None, dry_run=True)
        else:
            typer.echo("daily-review reflect｜dry-run")
            typer.echo(f"日付: {day}")
            _print_draft_review(draft, day)
            typer.echo("保存は行いませんでした")
        return

    if _recent_duplicate_input(base, day, raw_text):
        if yes or json_output or _stdin_is_piped():
            _reflect_error("同じ内容が直前に保存されています", json_output=json_output)
        if not typer.confirm("同じ内容が直前に保存されています。もう一度保存しますか？", default=False):
            typer.echo("確定せず終了しました")
            return
    try:
        _, input_id = _store_natural_input(base, day, raw_text, source)
        result = organize_day(base, day)
        atomic_write_json_data(result["path"], result["draft"])
    except (OSError, ValueError, LookupError) as exc:
        _reflect_error(
            f"入力は保存されていますが整理できません: {exc}\ndaily-review reflect --date {day} --resume",
            json_output=json_output,
            code=3,
        )
        return
    draft = result["draft"]
    if yes:
        reason = _auto_approval_reason(base, day, draft)
        if reason:
            _reflect_error(f"自動承認できません\n理由: {reason}\ndaily-review reflect --date {day} --resume", json_output=json_output)
        try:
            approval = approve_draft(base, day, draft)
        except (OSError, ValueError) as exc:
            _reflect_error(f"承認内容を保存できません: {exc}", json_output=json_output, code=3)
        if json_output:
            _reflect_json(day=day, draft=draft, input_saved=True, input_id=input_id, approved=True, daily_file=approval["daily_path"], dry_run=False)
        else:
            typer.echo(f"daily-review reflect｜{day}")
            typer.echo("入力を保存しました")
            typer.echo(f"入力ID: {input_id}")
            _reflect_approved_message(day, approval)
        return
    if json_output:
        _reflect_json(day=day, draft=draft, input_saved=True, input_id=input_id, approved=False, daily_file=None, dry_run=False)
        return
    typer.echo(f"daily-review reflect｜{day}")
    typer.echo("入力を保存しました")
    typer.echo(f"入力ID: {input_id}")
    typer.echo("内容を整理しました")
    _print_draft_review(draft, day)
    if _stdin_is_piped():
        typer.echo("確定せず終了しました")
        typer.echo("ドラフトは保存されています")
        typer.echo(f"再開: daily-review reflect --date {day} --resume")
        return
    _reflect_interactive(base, day, draft)


@app.command("save-raw")
def save_raw(
    date: str | None = DateOption,
    file: Path | None = typer.Option(None, "--file", help="生ログのテキストファイル"),
    root: Path | None = RootOption,
) -> None:
    """指定日の生ログを保存します。"""
    base = _root(root)
    day = _day(date)
    raw_log = _read_text_from_file_or_stdin(file)
    if not raw_log.strip():
        raise typer.BadParameter("空入力は保存できません。")
    entry = load_or_create_daily(base, day)
    entry["raw_log"] = raw_log
    json_path = save_daily(base, day, entry)
    markdown_path = _regenerate_daily_markdown(base, day, entry)
    typer.echo(f"生ログを保存しました: {json_path}")
    typer.echo(f"Markdownを更新しました: {markdown_path}")
    typer.echo("次: save-review で整形済み振り返りを保存します。")


@app.command("save-review")
def save_review(
    date: str | None = DateOption,
    file: Path | None = typer.Option(None, "--file", help="整形済み振り返りJSON"),
    root: Path | None = RootOption,
) -> None:
    """ChatGPTが整形した振り返りJSONを保存します。"""
    base = _root(root)
    day = _day(date)
    payload = _read_json_from_file_or_stdin(file)
    try:
        review_input = ReviewInput.normalize_payload(payload)
    except ValidationError as exc:
        raise typer.BadParameter(_format_validation_error(exc)) from exc

    entry = load_or_create_daily(base, day)
    if review_input.structured_review is not None:
        entry["structured_review"] = dump_model(review_input.structured_review)
    if review_input.diary is not None:
        entry["diary"] = review_input.diary
    json_path = save_daily(base, day, entry)
    markdown_path = _regenerate_daily_markdown(base, day, entry)
    typer.echo(f"整形ログを保存しました: {json_path}")
    typer.echo(f"Markdownを更新しました: {markdown_path}")
    typer.echo("次: save-proposal で明日の指示書・提案版を保存します。")


@app.command("save-proposal")
def save_proposal(
    date: str | None = DateOption,
    file: Path | None = typer.Option(None, "--file", help="明日の指示書・提案版JSON"),
    root: Path | None = RootOption,
) -> None:
    """明日の指示書の提案版を保存します。"""
    base = _root(root)
    day = _day(date)
    payload = _clean_proposal_payload(_read_json_from_file_or_stdin(file))
    try:
        proposal_input = ProposalInput.model_validate(payload)
    except ValidationError as exc:
        raise typer.BadParameter(_format_validation_error(exc)) from exc

    plan = Plan(
        **dump_model(proposal_input),
        status="pending_review",
    )
    plan_payload = dump_model(plan)
    _ensure_task_ids(plan_payload)
    result = validate_plan(plan_payload, day, final=False)
    if result.has_errors:
        _print_validation(result)
        raise typer.Exit(code=1)

    entry = load_or_create_daily(base, day)
    entry["tomorrow_plan_proposal"] = plan_payload
    json_path = save_daily(base, day, entry)
    markdown_path = _regenerate_daily_markdown(base, day, entry)
    typer.echo(f"提案版を保存しました: {json_path}")
    typer.echo(f"Markdownを更新しました: {markdown_path}")
    _print_validation(result)
    typer.echo("次: 内容がよければ approve-plan で確定版にします。")


@app.command("save-night")
def save_night(
    date: str | None = DateOption,
    file: Path | None = typer.Option(None, "--file", help="夜の振り返り一括JSON"),
    root: Path | None = RootOption,
) -> None:
    """生ログ、整形済み振り返り、明日の指示書・提案版を一括保存します。"""
    base = _root(root)
    payload = _read_json_from_file_or_stdin(file)
    day = _resolve_night_date(date, payload)
    if payload.get("task_results"):
        raise typer.BadParameter(
            "task_resultsはrecord-resultsで保存してください。save-nightでは部分保存防止のため同時保存しません。"
        )

    raw_log = payload.get("raw_log")
    if not isinstance(raw_log, str) or not raw_log.strip():
        raise typer.BadParameter("raw_logは空でない文字列にしてください。")
    if payload.get("structured_review") is None:
        raise typer.BadParameter("structured_reviewがありません。")
    if payload.get("tomorrow_plan_proposal") is None:
        raise typer.BadParameter("tomorrow_plan_proposalがありません。")

    try:
        review_input = ReviewInput.normalize_payload(
            {
                "diary": payload.get("diary"),
                "structured_review": payload.get("structured_review"),
            }
        )
    except ValidationError as exc:
        raise typer.BadParameter(_format_validation_error(exc)) from exc
    if review_input.structured_review is None:
        raise typer.BadParameter("structured_reviewがありません。")

    proposal_payload = payload.get("tomorrow_plan_proposal")
    if not isinstance(proposal_payload, dict):
        raise typer.BadParameter("tomorrow_plan_proposalはJSONオブジェクトにしてください。")
    plan_payload, result = _build_pending_plan(proposal_payload, day)
    if result.has_errors:
        _print_validation(result)
        raise typer.Exit(code=1)

    entry = load_or_create_daily(base, day)
    entry["raw_log"] = raw_log
    if review_input.diary is not None:
        entry["diary"] = review_input.diary
    entry["structured_review"] = dump_model(review_input.structured_review)
    entry["tomorrow_plan_proposal"] = plan_payload
    save_daily(base, day, entry)
    _regenerate_daily_markdown(base, day, entry)
    _print_night_summary(day, entry, result.warnings)


@app.command("close-day")
def close_day(
    date: str | None = DateOption,
    file: Path | None = typer.Option(None, "--file", help="当日の結果・振り返り・翌日提案の一括JSON"),
    clipboard: bool = typer.Option(False, "--clipboard", help="macOSのクリップボードからJSONを読み込む"),
    dry_run: bool = typer.Option(False, "--dry-run", help="検証と更新予定の表示だけ行い、保存しない"),
    root: Path | None = RootOption,
) -> None:
    """当日の結果、夜の振り返り、翌日の提案版を安全に一括保存します。"""
    base = _root(root)
    try:
        payload = _read_json_for_command(file, clipboard)
        day = _resolve_night_date(date, payload)
        entries_by_day, warnings, result_count, result_source_day = _prepare_close_day(base, day, payload)
    except typer.BadParameter as exc:
        _print_save_error(str(exc))
        raise typer.Exit(code=1) from exc

    if dry_run:
        _print_close_day_dry_run(base, day, entries_by_day, result_count, warnings)
        return

    stamped_entries = {
        entry_day: _stamp_entry_for_write(entry_day, entry)
        for entry_day, entry in entries_by_day.items()
    }
    writes = [
        (daily_path(base, entry_day), stamped_entries[entry_day])
        for entry_day in sorted(stamped_entries)
    ]
    atomic_write_json_many(writes)
    for entry_day, entry in stamped_entries.items():
        _regenerate_daily_markdown(base, entry_day, entry)

    carryover_count = 0
    if result_source_day and result_source_day in stamped_entries:
        carryover_count = sum(
            1
            for result in stamped_entries[result_source_day].get("task_results", [])
            if result.get("status") in CARRYOVER_STATUSES
        )
    _print_close_day_summary(day, stamped_entries[day], result_count, carryover_count, warnings)


@app.command("approve-plan")
def approve_plan(
    date: str | None = DateOption,
    force: bool = typer.Option(False, "--force", help="既存の確定版を確認なしで上書きする"),
    root: Path | None = RootOption,
) -> None:
    """提案版を確定版へコピーし、承認状態にします。"""
    base = _root(root)
    day = _day(date)
    entry = load_daily(base, day)
    if not entry or not entry.get("tomorrow_plan_proposal"):
        typer.echo("提案版がないため承認できません。", err=True)
        raise typer.Exit(code=1)
    if entry.get("tomorrow_plan_final") and not force:
        confirmed = typer.confirm("既存の確定版を上書きしますか？")
        if not confirmed:
            typer.echo("承認を中止しました。")
            raise typer.Exit(code=1)

    proposal = dict(entry["tomorrow_plan_proposal"])
    _ensure_task_ids(proposal)
    result = validate_plan(proposal, day, final=False)
    if result.has_errors:
        _print_validation(result)
        typer.echo("致命的なエラーがあるため承認しません。", err=True)
        raise typer.Exit(code=1)

    final = dict(proposal)
    final["status"] = "approved"
    final["approved_at"] = now_iso()
    final_result = validate_plan(final, day, final=True)
    if final_result.has_errors:
        _print_validation(final_result)
        raise typer.Exit(code=1)

    entry["tomorrow_plan_proposal"] = proposal
    entry["tomorrow_plan_final"] = final
    json_path = save_daily(base, day, entry)
    markdown_path = _regenerate_daily_markdown(base, day, entry)
    typer.echo(f"指示書を承認しました｜対象日 {final['target_date']}")
    typer.echo("翌朝:")
    typer.echo(f"daily-review today --date {final['target_date']}")
    typer.echo(f"確定版を保存しました: {json_path}")
    typer.echo(f"Markdownを更新しました: {markdown_path}")
    _print_validation(final_result)


def _find_plan_by_target(root: Path, target_date: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    daily_dir = root / "data" / "daily"
    if not daily_dir.exists():
        return None, None, None
    pending: dict[str, Any] | None = None
    pending_source: str | None = None
    for path in sorted(daily_dir.glob("*.json")):
        entry = read_json_file(path)
        final = entry.get("tomorrow_plan_final")
        if final and final.get("target_date") == target_date:
            return entry, final, None
        proposal = entry.get("tomorrow_plan_proposal")
        if proposal and proposal.get("target_date") == target_date:
            pending = proposal
            pending_source = entry.get("date")
    return None, pending, pending_source


def _target_date_for_entry(entry: dict[str, Any]) -> str:
    final = entry.get("tomorrow_plan_final") or {}
    proposal = entry.get("tomorrow_plan_proposal") or {}
    return final.get("target_date") or proposal.get("target_date") or "-"


def _saved_label(value: Any) -> str:
    return "保存済み" if value else "未保存"


@app.command()
def today(
    date: str | None = DateOption,
    show_ids: bool = typer.Option(False, "--show-ids", help="夜の結果記録用にタスクIDを表示する"),
    root: Path | None = RootOption,
) -> None:
    """指定日をtarget_dateに持つ確定済み指示書を表示します。"""
    base = _root(root)
    target = _day(date)
    entry, plan, pending_source = _find_plan_by_target(base, target)
    if not plan:
        typer.echo(f"{target} を対象にした確定版指示書はありません。")
        return
    if pending_source:
        typer.echo(f"{target} の指示書は提案版のみです。まだ未承認です（保存元: {pending_source}）。")
        return
    if entry and _ensure_task_ids(plan):
        source_day = entry.get("date")
        if source_day:
            save_daily(base, source_day, entry)
            _regenerate_daily_markdown(base, source_day, entry)

    typer.echo(f"今日の指示書｜{target}")
    typer.echo("Main")
    for index, item in enumerate(plan.get("main") or [], start=1):
        typer.echo(f"{index}. {item}")
    typer.echo("優先タスク")
    for index, task in enumerate(plan.get("tasks") or [], start=1):
        id_part = f"[{task.get('id')}] " if show_ids else ""
        typer.echo(f"{index}. {id_part}[{task.get('area')}] {task.get('task')}")
        typer.echo(f"   最低ライン: {task.get('minimum_line')}")
    typer.echo("今日変えること")
    typer.echo(plan.get("one_change_tomorrow", "未保存"))


@app.command("record-results")
def record_results(
    date: str | None = DateOption,
    file: Path | None = typer.Option(None, "--file", help="タスク実行結果JSON"),
    root: Path | None = RootOption,
) -> None:
    """target_dateで確定版を探し、タスクごとの実行結果を保存します。"""
    base = _root(root)
    target = _day(date)
    payload = _read_json_from_file_or_stdin(file)
    source_day, entry, plan = _find_final_entry_by_target(base, target)
    if not entry or not plan or not source_day:
        if _has_pending_by_target(base, target):
            typer.echo(f"{target} は提案版のみです。承認後に結果を保存してください。", err=True)
        else:
            typer.echo(f"{target} を対象にした確定版指示書がありません。", err=True)
        raise typer.Exit(code=1)

    _ensure_task_ids(plan)
    updates = _parse_task_results(payload)
    errors, warnings = _validate_task_results_payload(updates, plan)
    if errors:
        for error in errors:
            typer.echo(f"エラー: {error}", err=True)
        raise typer.Exit(code=1)

    entry["tomorrow_plan_final"] = plan
    entry["task_results"] = _merge_task_results(entry.get("task_results") or [], updates)
    json_path = save_daily(base, source_day, entry)
    markdown_path = _regenerate_daily_markdown(base, source_day, entry)
    typer.echo(f"実行結果を保存しました｜{target}")
    typer.echo(f"保存先: {json_path}")
    typer.echo(f"Markdownを更新しました: {markdown_path}")
    if warnings:
        typer.echo("警告")
        for warning in warnings:
            typer.echo(f"- {warning}")


@app.command("results")
def results(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """指定日をtarget_dateに持つ確定版タスクの実行結果を表示します。"""
    base = _root(root)
    target = _day(date)
    source_day, entry, plan = _find_final_entry_by_target(base, target)
    if not entry or not plan or not source_day:
        typer.echo(f"{target} を対象にした確定版指示書がありません。", err=True)
        raise typer.Exit(code=1)
    if _ensure_task_ids(plan):
        entry["tomorrow_plan_final"] = plan
        save_daily(base, source_day, entry)
        _regenerate_daily_markdown(base, source_day, entry)
    _print_task_results(target, entry, plan)


@app.command("carryover")
def carryover(
    date: str | None = DateOption,
    include_skipped: bool = typer.Option(False, "--include-skipped", help="意図的に見送ったタスクも表示する"),
    root: Path | None = RootOption,
) -> None:
    """未完了タスクを翌日の引き継ぎ候補として表示します。"""
    base = _root(root)
    target = _day(date)
    source_day, entry, plan = _find_final_entry_by_target(base, target)
    if not entry or not plan or not source_day:
        typer.echo(f"{target} を対象にした確定版指示書がありません。", err=True)
        raise typer.Exit(code=1)
    if _ensure_task_ids(plan):
        entry["tomorrow_plan_final"] = plan
        save_daily(base, source_day, entry)
        _regenerate_daily_markdown(base, source_day, entry)

    result_by_id = _result_map(entry)
    typer.echo(f"引き継ぎ候補｜{target}")
    count = 0
    for task in plan.get("tasks") or []:
        result = result_by_id.get(task.get("id"))
        status = result.get("status") if result else None
        if status == "completed":
            continue
        if status == "skipped" and not include_skipped:
            continue
        if status not in CARRYOVER_STATUSES and status is not None and not (include_skipped and status == "skipped"):
            continue
        count += 1
        typer.echo(f"{count}. [{task.get('area')}] {task.get('task')}")
        typer.echo(f"   結果: {_task_result_label(status)}")
        typer.echo(f"   次の候補: {task.get('task')}")
    if count == 0:
        typer.echo("なし")
    typer.echo("注意:")
    typer.echo("これは翌日の提案候補です。")
    typer.echo("確定版へは自動追加されません。")


@app.command("show-proposal")
def show_proposal(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """指定日の明日の指示書・提案版を短く表示します。"""
    base = _root(root)
    day = _day(date)
    entry = load_daily(base, day)
    if not entry or not entry.get("tomorrow_plan_proposal"):
        typer.echo(f"{day} の提案版はまだありません。", err=True)
        raise typer.Exit(code=1)
    _print_plan_summary("明日の指示書・提案版", entry["tomorrow_plan_proposal"], "未承認")
    typer.echo("承認する場合:")
    typer.echo(f"daily-review approve-plan --date {day}")


def _print_next_action(base: Path, day: str, *, include_date: bool = False) -> None:
    summary = build_daily_summary(base, day)
    entry = summary["entry"]
    if summary["errors"]:
        for error in summary["errors"]:
            typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(code=3)
    action = next_action_kind(summary)
    if action == "organize":
        typer.echo("自然文入力が未整理です。")
        typer.echo(f"daily-review organize --date {day}")
        return
    if action == "draft_review":
        typer.echo("整理ドラフトが未承認です。")
        typer.echo(f"daily-review review --date {day}")
        return
    if action == "proposal":
        typer.echo("明日の指示書が未承認です。")
        typer.echo(f"daily-review show-proposal --date {day}")
        typer.echo(f"daily-review approve-plan --date {day}")
        return

    if action == "complete":
        target = entry["tomorrow_plan_final"].get("target_date", tomorrow_of(day))
        typer.echo("今日の夜の処理は完了しています。")
        typer.echo("翌朝:")
        typer.echo(f"daily-review today --date {target}")
        return

    if action == "today":
        typer.echo("今日の指示書があります。")
        typer.echo(f"daily-review today --date {day}")
        return

    typer.echo("夜の振り返りが未保存です。")
    typer.echo("1. ChatGPTへ今日の結果を送る")
    typer.echo("2. JSONをコピーする")
    typer.echo("3. 以下を実行する")
    date_option = f" --date {day}" if include_date else ""
    typer.echo(f"daily-review close-day{date_option} --clipboard --dry-run")


def _print_summary(summary: dict[str, Any], *, title: str = "状況", next_override: str | None = None) -> None:
    today_final = summary["today_final"]
    results = summary["task_results"]
    proposal = summary["tomorrow_proposal"]
    final = summary["tomorrow_final"]
    typer.echo(f"{title}｜{summary['date']}")
    typer.echo(f"今日の確定版: {'記録済み' if today_final else '未記録'}")
    typer.echo("今日のMain:")
    if summary["today_main"]:
        for index, item in enumerate(summary["today_main"], start=1):
            typer.echo(f"{index}. {item}")
    else:
        typer.echo("未記録")
    result_label = f"{results['recorded']}/{results['total']}件" if today_final or results["total"] else "未記録"
    typer.echo(f"タスク結果: {result_label}")
    typer.echo(f"夜の振り返り: {'記録済み' if summary['night_review_exists'] else '未記録'}")
    typer.echo(f"明日の提案版: {'記録済み' if proposal else '未記録'}")
    typer.echo(f"明日の確定版: {'記録済み' if final else '未記録'}")
    typer.echo(f"今週の記録日数: {summary['week_recorded_days']}日")
    typer.echo(f"今月の記録日数: {summary['month_recorded_days']}日")
    typer.echo(f"自然文入力: {summary['inbox_entry_count']}件")
    if not summary["draft"]:
        draft_label = "未作成"
    elif summary.get("draft_status") == "approved":
        draft_label = "承認済み"
    else:
        draft_label = "未承認"
    typer.echo(f"整理ドラフト: {draft_label}")
    typer.echo(f"次の操作: {next_override or next_command(summary)}")


@app.command()
def summary(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """指定日の計画・記録・次の操作を短く表示します。"""
    report = build_daily_summary(_root(root), _day(date))
    _print_summary(report, title="日次サマリー")
    try:
        plan = load_daily_plan(_root(root), report["date"])
    except (PlanningError, OSError, ValueError):
        plan = None
    if plan:
        typer.echo(f"目標リンク: {len(plan.get('goal_links') or [])}件")
        typer.echo(f"目標進捗反映: {'未実行' if plan.get('goal_links') else '対象なし'}")
    week_start, _ = week_range_for(report["date"])
    try: weekly_evaluation = load_weekly_evaluation(_root(root), week_start)
    except (EvaluationError, OSError, ValueError): weekly_evaluation = None
    typer.echo(f"週次目標評価: {'承認済み' if weekly_evaluation and weekly_evaluation.get('status') == 'approved' else 'ドラフト' if weekly_evaluation else '未作成'}")
    if report["errors"]:
        for error in report["errors"]:
            typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(code=3)


@app.command()
def home(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """毎日最初に見る、計画・未完了・次の操作の統合画面です。"""
    base = _root(root)
    report = build_daily_summary(base, _day(date))
    resume_next = ""
    try:
        designs = []
        for path in sorted((base / "data" / "goal-designs").glob("design-*.json")) if (base / "data" / "goal-designs").is_dir() else []:
            value = read_json_file(path)
            if isinstance(value, dict) and value.get("status") in {"questioning", "proposed"}:
                designs.append(value)
        if designs:
            latest = max(designs, key=lambda item: (item.get("updated_at", ""), item.get("id", "")))
            action = "review" if latest["status"] == "proposed" else "prompt"
            resume_next = f"daily-review goal design {action} {latest['id']}"
        if not resume_next:
            draft_replans = [item for item in list_replans(base) if item.get("status") == "draft"]
            if draft_replans:
                latest_replan = max(draft_replans, key=lambda item: (item.get("updated_at", ""), item.get("id", "")))
                action = "apply" if latest_replan.get("approved_proposal_ids") else "review"
                resume_next = f"daily-review goal replan {action} {latest_replan['id']}"
    except (OSError, ValueError, GoalDesignError, ReplanError):
        resume_next = ""
    chat_next = chat_home_next_command(base, report)
    handoff_state = "none"
    handoff_eligible = not report.get("draft") and not report.get("entry") and not report.get("inbox_entry_count")
    if handoff_eligible:
        try:
            handoff_state = current_handoff_state(base, report["date"])
        except (OSError, ValueError, HandoffError):
            handoff_state = "none"
    if "--import-only --clipboard" in chat_next and handoff_state == "none":
        handoff_next = chat_next
    elif handoff_state == "issued":
        handoff_next = f"daily-review receive --date {report['date']} --clipboard"
    else:
        handoff_next = f"daily-review handoff --date {report['date']} --copy" if handoff_state in {"none", "expired"} else ""
    planning_next = ""
    try:
        active_goals = [goal for goal in load_goals(base) if goal.get("status") == "active"]
        week_start, _ = week_range_for(report["date"])
        weekly_goal_plan = load_weekly_plan(base, week_start)
        daily_goal_plan = load_daily_plan(base, report["date"])
        # Do not hide an existing daily review workflow.  Planning is the
        # first action only for an otherwise empty day with active goals.
        if active_goals and not report.get("entry") and not report.get("draft") and not report.get("inbox_entry_count"):
            if report["date"] == week_start and not weekly_goal_plan:
                planning_next = f"daily-review plan week --date {report['date']}"
            elif weekly_goal_plan and weekly_goal_plan.get("status") != "approved":
                planning_next = f"daily-review plan review --week {week_start}"
            elif not daily_goal_plan:
                planning_next = f"daily-review plan today --date {report['date']}"
            elif daily_goal_plan.get("status") != "approved":
                planning_next = f"daily-review plan review --date {report['date']}"
    except (PlanningError, GoalError, OSError, ValueError):
        planning_next = ""
    _print_summary(report, title="daily-review home", next_override=resume_next or planning_next or (handoff_next if handoff_eligible else chat_next or home_next_command(report)))
    if handoff_state == "issued":
        typer.echo("状態: ChatGPTの回答待ち")
    elif handoff_state == "expired":
        typer.echo("状態: handoff期限切れ")
    if "--import-only --clipboard" in chat_next:
        typer.echo("状態: ChatGPTからのJSON待ち")
    typer.echo("未完了タスク:")
    if report["incomplete_tasks"]:
        for index, item in enumerate(report["incomplete_tasks"], start=1):
            task = item["task"]
            typer.echo(f"{index}. [{task.get('area', '未設定')}] {task.get('task', '未設定')}（{_task_result_label(item['status'])}）")
    else:
        typer.echo("なし")
    if report["tomorrow_final"]:
        typer.echo("明日の確定版: 記録済み")
    elif report["tomorrow_proposal"]:
        typer.echo("明日の提案版: 未承認")
    draft = report["draft"] or {}
    if draft:
        today_candidates = (draft.get("today") or {}).get("main_candidates") or []
        tomorrow_candidates = (draft.get("tomorrow") or {}).get("main_candidates") or []
        typer.echo(f"今日のMain候補: {len(today_candidates)}件")
        typer.echo(f"明日のMain候補: {len(tomorrow_candidates)}件")
        if report.get("draft_status") == "approved":
            typer.echo("確定日次: 作成済み")
            typer.echo("今日の振り返り: 完了")
    try:
        goals = build_goal_summary(base, today=report["date"])
    except (GoalError, OSError, ValueError):
        typer.echo("目標: 読み込めません")
    else:
        if not goals["active_count"]:
            typer.echo("目標: 未設定")
            if not (handoff_eligible or chat_next or home_next_command(report)):
                typer.echo("次の操作: daily-review goal add")
        else:
            typer.echo(f"進行中の目標: {goals['active_count']}件")
            if goals["near_due"]:
                typer.echo("期限が近い目標:")
                for goal in goals["near_due"]:
                    remaining = (parse_date(goal["due_date"]) - parse_date(report["date"])).days
                    typer.echo(f"- {goal['title']}｜残り{remaining}日")
            next_actions: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for goal in load_goals(base):
                if goal.get("status") != "active":
                    continue
                action = next_goal_action(goal, today=report["date"])
                if action:
                    next_actions.append((goal, action))
            if next_actions:
                typer.echo("目標の次アクション:")
                for goal, action in next_actions[:2]:
                    item = action["step"] or action["milestone"]
                    typer.echo(f"- {goal.get('category') or goal['title']}｜{item['title']}")
    week_start, week_end = week_range_for(report["date"])
    month_start, month_end = month_range_for(report["date"])
    if report["night_review_exists"] and report["date"] == week_end:
        try: weekly_evaluation = load_weekly_evaluation(base, week_start)
        except (EvaluationError, OSError, ValueError): weekly_evaluation = None
        if not weekly_evaluation:
            typer.echo(f"目標評価の次操作: daily-review goal evaluate week --date {report['date']} --save")
        elif weekly_evaluation.get("status") != "approved":
            typer.echo(f"目標評価の次操作: daily-review goal evaluate review --week {week_start}")
    if report["night_review_exists"] and report["date"] == month_end:
        try: monthly_evaluation = load_monthly_evaluation(base, month_start[:7])
        except (EvaluationError, OSError, ValueError): monthly_evaluation = None
        if not monthly_evaluation:
            typer.echo(f"月次評価の次操作: daily-review goal evaluate month --month {month_start[:7]} --save")
    doctor_report = run_doctor(base)
    errors = [item for item in doctor_report["issues"] if item["level"] == "ERROR"]
    if errors:
        typer.echo(f"WARN: doctorで重大エラーが{len(errors)}件あります。daily-review doctor を実行してください。")
    if report["errors"]:
        for error in report["errors"]:
            typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(code=3)


@app.command("start")
def start(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """指定日の状態から、今行うべき次の操作を案内します。"""
    base = _root(root)
    day = _day(date)
    typer.echo(f"開始案内｜{day}")
    _print_next_action(base, day, include_date=True)


@app.command("next")
def next_action(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """保存状態から次に実行するコマンドをルールベースで表示します。"""
    _print_next_action(_root(root), _day(date))


@app.command()
def status(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """指定日の保存状況を短く表示します。"""
    base = _root(root)
    day = _day(date)
    entry = load_daily(base, day)
    if not entry:
        typer.echo(f"{day} の日次データはまだありません。")
        raise typer.Exit(code=1)

    final = entry.get("tomorrow_plan_final")
    proposal = entry.get("tomorrow_plan_proposal")
    typer.echo(day)
    typer.echo(f"生ログ        {_saved_label(entry.get('raw_log'))}")
    typer.echo(f"整形ログ      {_saved_label(entry.get('structured_review') or entry.get('diary'))}")
    typer.echo(f"提案版        {_saved_label(proposal)}")
    typer.echo(f"確定版        {'承認済み' if final and final.get('status') == 'approved' else '未承認'}")
    typer.echo(f"対象日        {_target_date_for_entry(entry)}")


@app.command("list")
def list_entries(
    limit: int = typer.Option(30, "--limit", min=1, help="表示件数"),
    root: Path | None = RootOption,
) -> None:
    """保存済みの日次記録を一覧表示します。"""
    base = _root(root)
    daily_dir = base / "data" / "daily"
    if not daily_dir.exists():
        typer.echo("日次記録はまだありません。")
        return
    paths = sorted(daily_dir.glob("*.json"), reverse=True)[:limit]
    typer.echo("振り返り日 | 生ログ | 整形 | 提案 | 確定 | 対象日")
    for path in paths:
        entry = read_json_file(path)
        typer.echo(
            " | ".join(
                [
                    entry.get("date", path.stem),
                    "保存済み" if entry.get("raw_log") else "未保存",
                    "保存済み" if entry.get("structured_review") or entry.get("diary") else "未保存",
                    "保存済み" if entry.get("tomorrow_plan_proposal") else "未保存",
                    "承認済み" if (entry.get("tomorrow_plan_final") or {}).get("status") == "approved" else "未承認",
                    _target_date_for_entry(entry),
                ]
            )
        )


@app.command()
def validate(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """指定日の日次データを検証します。"""
    base = _root(root)
    day = _day(date)
    entry = load_daily(base, day)
    if not entry:
        typer.echo(f"{day} の日次データがありません。", err=True)
        raise typer.Exit(code=1)
    result = validate_daily(entry)
    _print_validation(result)
    if result.has_errors:
        raise typer.Exit(code=1)


@app.command()
def weekly(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """火曜始まり・月曜終わりの週次集計を作成します。"""
    base = _root(root)
    day = _day(date)
    start, end = week_range_for(day)
    summary = build_weekly_summary(base, day)
    json_path = weekly_path(base, start, end)
    write_text(json_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    markdown_path = write_text(weekly_log_path(base, start, end), render_weekly(summary))
    typer.echo(f"週次集計を保存しました: {json_path}")
    typer.echo(f"Markdownを保存しました: {markdown_path}")
    typer.echo(f"対象期間: {start}〜{end}")
    typer.echo(f"記録日数: {summary['recorded_days']}")
    for warning in summary["warnings"]:
        typer.echo(f"警告: {warning}")
    main = summary["main_summary"]
    typer.echo(f"Main: 完了 {main['completed']}/{main['recorded']}（{'算出不可' if main['percent'] is None else str(main['percent']) + '%' }）、結果未記録 {main['unrecorded']}件")
    minimum = summary["minimum_line_summary"]
    typer.echo(f"最低ライン達成率: {'算出不可' if minimum['percent'] is None else str(minimum['percent']) + '%'}")
    continuity = summary["continuity"]
    typer.echo(f"継続状況: 振り返り {continuity['review_recorded']['count']}/{continuity['review_recorded']['total']}日、結果 {continuity['task_results_recorded']['count']}/{continuity['task_results_recorded']['total']}日、確定版 {continuity['approved_plan']['count']}/{continuity['approved_plan']['total']}日")
    typer.echo(f"崩れた原因: {summary['failure_reasons'][0]['cause'] if summary['failure_reasons'] else 'なし'}")
    typer.echo(f"引き継ぎが多いタスク: {summary['carryover_analysis'][0]['task'] if summary['carryover_analysis'] else 'なし'}")
    typer.echo(f"来週変えること1つ: {summary['improvement_suggestion']['text']}")
    # Keep the established operational lines for scripts and existing users.
    task_summary = summary["task_execution"]
    typer.echo("タスク実行状況")
    if task_summary["total"]:
        completion = task_summary["completion_rate"]
        task_minimum = task_summary["task_minimum_line_rate"]
        typer.echo(f"通常タスク完了率: {completion['percent']}%（{completion['completed']}/{completion['total']}）")
        typer.echo(f"最低ライン達成率: {task_minimum['percent']}%（{task_minimum['achieved']}/{task_minimum['total']}）")
    else:
        typer.echo("集計対象なし")


def _legacy_doctor(root: Path | None) -> None:
    """Keep the established human-readable v1 doctor output."""
    report = run_doctor(_root(root))
    errors = [item for item in report["issues"] if item["level"] == "ERROR"]
    warnings = [item for item in report["issues"] if item["level"] == "WARN"]
    typer.echo(f"保存先ルート: {report['root']}")
    typer.echo("OK   ディレクトリ構造・テンプレート・JSONを点検しました")
    typer.echo(f"OK   日次JSON {report['daily_count']}件")
    for check in report["checks"]:
        typer.echo(f"OK   {check}")
    for item in warnings + errors:
        typer.echo(f"{item['level']} {item['message']}")
    if errors:
        typer.echo("daily-review doctor: ERROR")
        typer.echo(f"WARN {len(warnings)}件 / ERROR {len(errors)}件")
    else:
        if warnings:
            typer.echo("daily-review doctor: WARNING")
            typer.echo(f"WARN {len(warnings)}件 / ERROR 0件")
        else:
            typer.echo("daily-review doctor: OK")
    if errors:
        raise typer.Exit(code=1)


def _backup_create_output(
    base: Path,
    output: Path | None,
    *,
    dry_run: bool,
    output_format: str,
    idempotency_key: str | None = None,
) -> None:
    plan = plan_backup(base, output)
    if dry_run:
        result = {
            "status": "dry_run",
            "output": str(plan["output"]),
            "file_count": plan["file_count"],
            "estimated_size": plan["estimated_size"],
            "files": [name for name, _ in plan["members"]],
            "excluded": plan["excluded"],
        }
    else:
        path, manifest = create_backup(base, output, idempotency_key=idempotency_key)
        result = {
            "status": "created",
            "path": str(path),
            "backup_id": manifest["backup_id"],
            "file_count": manifest["file_count"],
            "total_size": manifest["total_size"],
            "verified": True,
        }
    if output_format == "json":
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if dry_run:
        typer.echo("バックアップ作成｜dry-run")
        typer.echo(f"出力予定: {result['output']}")
        typer.echo(f"対象: {result['file_count']}件 / {result['estimated_size']} bytes")
        for name in result["files"]:
            typer.echo(f"INCLUDE {name}")
        for item in result["excluded"]:
            typer.echo(f"EXCLUDE {item['path']} ({item['reason']})")
        typer.echo("バックアップファイルは作成していません")
    else:
        typer.echo(f"バックアップを作成しました: {result['path']}")
        typer.echo(f"backup_id: {result['backup_id']}")
        typer.echo(f"ファイル数: {result['file_count']}")
        typer.echo("検証: OK")


@backup_app.callback()
def backup_legacy(
    ctx: typer.Context,
    root: Path | None = RootOption,
    output: Path | None = typer.Option(
        None, "--output", help="旧形式互換: 出力ZIPまたはディレクトリ"
    ),
) -> None:
    """サブコマンドなしでは従来どおりフルバックアップを作成します。"""
    if ctx.invoked_subcommand is not None:
        return
    try:
        _backup_create_output(_root(root), output, dry_run=False, output_format="text")
    except (OSError, ValueError, OperationLockedError) as exc:
        typer.echo(f"バックアップできませんでした: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@backup_app.command("create")
def backup_create_command(
    root: Path | None = RootOption,
    output: Path | None = typer.Option(
        None, "--output", help="出力ZIPまたはディレクトリ"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="対象と除外だけを表示し、ZIPを作成しません"
    ),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    output_format: str = typer.Option("text", "--format", help="text または json"),
) -> None:
    """data、logs、templates、秘密情報を除いたconfigをZIPへ保存します。"""
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--formatはtextまたはjsonにしてください")
    try:
        _backup_create_output(
            _root(root),
            output,
            dry_run=dry_run,
            output_format=output_format,
            idempotency_key=idempotency_key,
        )
    except (OSError, ValueError, OperationLockedError) as exc:
        if output_format == "json":
            typer.echo(
                json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
            )
        else:
            typer.echo(f"バックアップできませんでした: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@backup_app.command("list")
def backup_list_command(
    root: Path | None = RootOption,
    directory: Path | None = typer.Option(None, "--directory"),
    output_format: str = typer.Option("text", "--format", help="text または json"),
) -> None:
    """バックアップを検証状態・件数・versionとともに一覧表示します。"""
    values = list_backups(_root(root), directory)
    if output_format == "json":
        typer.echo(json.dumps({"backups": values}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"バックアップ一覧｜{len(values)}件")
    for item in values:
        typer.echo(
            f"- {item['backup_id']} {item.get('created_at', '未記録')} {'OK' if item['verified'] else 'ERROR'} {item['path']}"
        )


@backup_app.command("inspect")
def backup_inspect_command(
    backup_file: Path = typer.Argument(...),
    output_format: str = typer.Option("text", "--format", help="text または json"),
) -> None:
    """ZIPを展開せずmanifestを表示します。"""
    try:
        manifest, _ = inspect_backup(backup_file)
    except (OSError, ValueError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    if output_format == "json":
        typer.echo(json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    typer.echo(f"backup_id: {manifest.get('backup_id', backup_file.stem)}")
    typer.echo(f"created_at: {manifest.get('created_at', '未記録')}")
    typer.echo(f"app_version: {manifest.get('app_version', '未記録')}")
    typer.echo(f"file_count: {manifest.get('file_count', 0)}")


@backup_app.command("verify")
def backup_verify_command(
    backup_file: Path = typer.Argument(...),
    output_format: str = typer.Option("text", "--format", help="text または json"),
) -> None:
    """manifest、SHA-256、不正パス、symlink、展開サイズを検証します。"""
    try:
        result = verify_backup(backup_file)
    except (OSError, ValueError) as exc:
        if output_format == "json":
            typer.echo(
                json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
            )
        else:
            typer.echo(f"バックアップ検証: ERROR ({exc})", err=True)
        raise typer.Exit(code=3) from exc
    if output_format == "json":
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    typer.echo("バックアップ検証: OK")
    typer.echo(f"backup_id: {result['backup_id']}")
    typer.echo(f"ファイル数: {result['file_count']}")


@backup_app.command("delete")
def backup_delete_command(
    backup_file: Path | None = typer.Argument(None),
    retention: bool = typer.Option(
        False, "--retention", help="設定された世代管理の削除候補を対象にする"
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply", help="既定では削除候補だけを表示"
    ),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    root: Path | None = RootOption,
) -> None:
    """手動バックアップを世代管理から除外し、確認後だけ削除します。"""
    base = _root(root)
    if retention:
        candidates = prune_backups(base, dry_run=True)
    elif backup_file is not None:
        if dry_run or not idempotency_key:
            verify_backup(backup_file)
        candidates = [{"path": str(backup_file)}]
    else:
        raise typer.BadParameter("BACKUP_FILEまたは--retentionを指定してください")
    result = None
    if not dry_run:
        result = delete_backup_files(
            base,
            [Path(item["path"]) for item in candidates],
            idempotency_key=idempotency_key,
        )
    typer.echo("削除候補" if dry_run else "削除しました")
    for item in candidates:
        typer.echo(f"- {item['path']}")
    if result and result["status"] == "idempotent_replay":
        typer.echo("同じ削除結果を返しました（idempotent replay）")


@app.command("restore")
def restore_router(
    operation_or_backup: str = typer.Argument(
        ..., help="preview / apply / status、または旧形式のbackup ZIP"
    ),
    backup_file: Path | None = typer.Argument(
        None, help="preview/apply対象のbackup ZIP"
    ),
    mode: str = typer.Option("merge", "--mode", help="merge / replace / missing-only"),
    confirmation_token: str | None = typer.Option(None, "--confirmation-token"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    output_format: str = typer.Option("text", "--format", help="text または json"),
    root: Path | None = RootOption,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="旧形式restoreの書込みを行わない"
    ),
    force: bool = typer.Option(
        False, "--force", help="旧形式restoreで安全バックアップ後に上書き"
    ),
) -> None:
    """復元preview/apply/statusと従来のrestore BACKUP_FILEを提供します。

    previewで発行されたconfirmation tokenをapplyに渡してください。
    apply前には現在状態を自動バックアップし、JSON出力にも対応します。
    """
    base = _root(root)
    try:
        if operation_or_backup == "status":
            records = restore_history(base)
            if output_format == "json":
                typer.echo(
                    json.dumps({"records": records}, ensure_ascii=False, indent=2)
                )
            else:
                typer.echo(f"復元履歴｜{len(records)}件")
                for item in records:
                    typer.echo(
                        f"- {item.get('restore_id')} {item.get('status')} {item.get('backup_id')}"
                    )
            return
        if operation_or_backup == "preview":
            if backup_file is None:
                raise ValueError("restore previewにはBACKUP_FILEが必要です")
            result = preview_restore(base, backup_file, mode=mode)
            if output_format == "json":
                typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
                return
            typer.echo(f"復元preview｜{mode}")
            for key in (
                "added",
                "updated",
                "unchanged",
                "skipped",
                "conflicts",
                "deleted",
            ):
                typer.echo(f"{key}: {result['counts'][key]}件")
            typer.echo(f"confirmation token: {result['confirmation_token']}")
            return
        if operation_or_backup == "apply":
            if backup_file is None:
                raise ValueError("restore applyにはBACKUP_FILEが必要です")
            if not confirmation_token:
                raise ValueError("restore applyには--confirmation-tokenが必要です")
            result = apply_restore(
                base,
                backup_file,
                mode=mode,
                confirmation_token=confirmation_token,
                idempotency_key=idempotency_key,
            )
            typer.echo(
                json.dumps(result, ensure_ascii=False, indent=2)
                if output_format == "json"
                else f"復元: {result['status']} / {result.get('restore_id', '再送')}"
            )
            return
        legacy_file = Path(operation_or_backup)
        result = restore_backup(base, legacy_file, dry_run=dry_run, force=force)
    except (OSError, ValueError, OperationLockedError) as exc:
        if output_format == "json":
            typer.echo(
                json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
            )
        else:
            typer.echo(f"復元できませんでした: {exc}", err=True)
        raise typer.Exit(
            code=1 if operation_or_backup not in {"preview", "apply"} else 4
        ) from exc
    typer.echo("復元前確認" if dry_run else "復元しました")
    typer.echo(f"復元予定ファイル: {len(result['files'])}件")
    typer.echo(f"新規作成: {len(result['new_files'])}件")
    typer.echo(f"競合: {len(result['conflicts'])}件")
    typer.echo(f"スキップ: {len(result['skipped'])}件")
    if dry_run:
        typer.echo("dry-runのため書き込んでいません。")
    elif result.get("safety_backup"):
        typer.echo(f"上書き前バックアップ: {result['safety_backup']}")


@rollover_app.command("preview")
def rollover_preview_command(
    date: str | None = DateOption,
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    output_format: str = typer.Option("text", "--format", help="text または json"),
    root: Path | None = RootOption,
) -> None:
    """未完了候補、長期未完了警告、Main最大3件を保存せず提示します。"""
    try:
        result = preview_rollover(
            _root(root), _day(date), idempotency_key=idempotency_key
        )
    except (OSError, ValueError, OperationLockedError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    if output_format == "json":
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    typer.echo(f"繰越preview｜{result['target_date']}")
    for item in result["candidates"]:
        typer.echo(
            f"- [{item['decision']}] {item['title']} ({item['rollover_count_after']}回目)"
        )
    typer.echo(f"Main候補: {len(result['main_task_ids'])}件")
    typer.echo(f"confirmation token: {result['confirmation_token']}")


@rollover_app.command("apply")
def rollover_apply_command(
    date: str | None = DateOption,
    confirmation_token: str = typer.Option(..., "--confirmation-token"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    output_format: str = typer.Option("text", "--format", help="text または json"),
    root: Path | None = RootOption,
) -> None:
    """既存タスクへ計画日・元期限・繰越回数を追記し、複製しません。"""
    try:
        result = apply_rollover(
            _root(root),
            _day(date),
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )
    except (OSError, ValueError, OperationLockedError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=4) from exc
    typer.echo(
        json.dumps(result, ensure_ascii=False, indent=2)
        if output_format == "json"
        else f"繰越: {result['status']} / {result.get('applied_count', 0)}件"
    )


@rollover_app.command("history")
def rollover_history_command(
    root: Path | None = RootOption,
    output_format: str = typer.Option("text", "--format", help="text または json"),
) -> None:
    """タスク単位の繰越前後状態と理由を表示します。"""
    records = rollover_history(_root(root))
    if output_format == "json":
        typer.echo(json.dumps({"records": records}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"繰越履歴｜{len(records)}件")
    for item in records:
        typer.echo(
            f"- {item['target_date']} {item['task_id']} {item['rollover_count_before']} -> {item['rollover_count_after']}"
        )


@doctor_app.callback()
def doctor_legacy(ctx: typer.Context, root: Path | None = RootOption) -> None:
    """サブコマンドなしでは従来の読み取り専用doctorを実行します。"""
    if ctx.invoked_subcommand is None:
        _legacy_doctor(root)


@doctor_app.command("check")
def doctor_check_command(
    root: Path | None = RootOption,
    output_format: str = typer.Option("text", "--format", help="text または json"),
) -> None:
    """JSON、task、指示書、API、通知、backup、lockを変更せず検査します。"""
    report = run_integrity_check(_root(root))
    if output_format == "json":
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"データ整合性｜{report['status'].upper()}")
        for item in report["issues"]:
            typer.echo(
                f"{item['severity'].upper():8} {item['code']} {item['path']}: {item['message']}"
            )
        typer.echo(
            " / ".join(
                f"{key.upper()} {value}件" for key, value in report["counts"].items()
            )
        )
    if report["status"] in {"error", "critical"}:
        raise typer.Exit(code=3)


@doctor_app.command("repair")
def doctor_repair_command(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="修復候補だけを表示し、変更しません"
    ),
    output_format: str = typer.Option("text", "--format", help="text または json"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    root: Path | None = RootOption,
) -> None:
    """内容を捏造・削除せず、安全な既定値・Main超過だけを修復します。"""
    try:
        result = (
            preview_integrity_repair(_root(root))
            if dry_run
            else apply_integrity_repair(_root(root), idempotency_key=idempotency_key)
        )
    except (OSError, ValueError, OperationLockedError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=4) from exc
    if output_format == "json":
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    typer.echo("整合性修復｜dry-run" if dry_run else f"整合性修復｜{result['status']}")
    if dry_run:
        typer.echo(f"修復可能: {result['fix_count']}件")
        typer.echo(f"手動確認: {result['manual_count']}件")
        typer.echo("変更は行っていません")
    else:
        typer.echo(f"修復: {result.get('fixed_count', 0)}件")
        if result.get("backup_path"):
            typer.echo(f"修復前バックアップ: {result['backup_path']}")


@doctor_app.command("report")
def doctor_report_command(
    root: Path | None = RootOption,
    output_format: str = typer.Option("text", "--format", help="text または json"),
) -> None:
    """安全修復の履歴を表示します。"""
    records = repair_history(_root(root))
    if output_format == "json":
        typer.echo(json.dumps({"records": records}, ensure_ascii=False, indent=2))
        return
    typer.echo(f"修復履歴｜{len(records)}件")
    for item in records:
        typer.echo(
            f"- {item['repair_id']} {item['status']} fixed={item['fixed_count']}"
        )


@app.command("release-check")
def release_check(root: Path | None = RootOption) -> None:
    """v1.2.0 正式リリースに必要な静的条件を読み取り専用で確認します。"""
    del root  # A release check validates the package, not user-owned runtime data.
    source_root = repository_root()
    errors: list[str] = []
    installed_version = _metadata_version()
    if __version__ != "1.2.0":
        errors.append(f"アプリのバージョンが1.2.0ではありません: {__version__}")
    if installed_version != __version__:
        errors.append("package metadataのバージョンを取得できないか一致しません")
    command_names = {
        command.name or command.callback.__name__.replace("_", "-")
        for command in app.registered_commands
    }
    command_names.update(group.name for group in app.registered_groups if group.name)
    required_commands = {
        "home", "summary", "start", "next", "doctor", "weekly", "monthly", "backup", "restore",
        "chat", "chat-prompt", "chat-import", "handoff", "receive", "handoff-list", "handoff-cancel",
        "input", "organize", "review", "edit-draft", "approve", "reflect", "migrate", "v11-check", "v12-check",
        "goal", "plan", "tasks", "export", "notifications", "api", "parse", "rollover",
    }
    missing_commands = sorted(required_commands - command_names)
    if missing_commands:
        errors.append("主要コマンドが登録されていません: " + ", ".join(missing_commands))
    from .chat_schema import SCHEMA_VERSION
    from .handoff import HANDOFF_VERSION
    from .migration import EVALUATION_MIGRATION_ID, FINAL_MIGRATION_ID, MIGRATION_ID, PLANNING_MIGRATION_ID, RECOVERY_MIGRATION_ID, ROADMAP_MIGRATION_ID
    from .storage import REQUIRED_TEMPLATE_NAMES

    for name in REQUIRED_TEMPLATE_NAMES + (CHAT_IMPORT_PROMPT_NAME,):
        if not (source_root / "templates" / name).is_file():
            errors.append(f"必要なテンプレートがありません: templates/{name}")
    if SCHEMA_VERSION != "1.0":
        errors.append("chat import schemaのバージョンが不正です")
    if HANDOFF_VERSION != "1.0":
        errors.append("handoff schemaのバージョンが不正です")
    if MIGRATION_ID != "v1.1-base":
        errors.append("v1.1 migration定義が不正です")
    if ROADMAP_MIGRATION_ID != "v1.2-goal-roadmap":
        errors.append("goal roadmap migration定義が不正です")
    if PLANNING_MIGRATION_ID != "v1.2-goal-planning":
        errors.append("goal planning migration定義が不正です")
    if EVALUATION_MIGRATION_ID != "v1.2-goal-evaluation-rc1":
        errors.append("goal evaluation migration定義が不正です")
    if FINAL_MIGRATION_ID != "v1.2-final":
        errors.append("v1.2 final migration定義が不正です")
    if RECOVERY_MIGRATION_ID != "v1.3-recovery-base":
        errors.append("v1.3 recovery migration定義が不正です")
    for name in ("evaluation.py", "replan.py", "goal_coach.py", "goal_design.py", "v12_check.py"):
        if not (source_root / "src" / "daily_review" / name).is_file():
            errors.append(f"v1.2 rc1モジュールがありません: {name}")
    if not (source_root / "src" / "daily_review" / "goals.py").is_file():
        errors.append("goal schemaまたはstorageがありません")
    if not (source_root / "config" / "priorities.example.json").is_file():
        errors.append("config/priorities.example.json がありません")
    if not (source_root / "config" / "notifications.example.json").is_file():
        errors.append("config/notifications.example.json がありません")
    if not (source_root / "config" / "api.example.json").is_file():
        errors.append("config/api.example.json がありません")
    if not (source_root / "config" / "recovery.example.json").is_file():
        errors.append("config/recovery.example.json がありません")
    for name in ("command_models.py", "command_api.py", "review_normalizer.py"):
        if not (source_root / "src" / "daily_review" / name).is_file():
            errors.append(f"v1.3 Command APIモジュールがありません: {name}")
    for name in ("recovery.py", "rollover.py", "integrity.py", "operation_lock.py"):
        if not (source_root / "src" / "daily_review" / name).is_file():
            errors.append(f"v1.3 recoveryモジュールがありません: {name}")
    for name in ("README.md", "CHANGELOG.md", "RELEASE_CHECKLIST.md", "RELEASE_CHECKLIST_V1.2.md", "tests/test_v11_e2e.py"):
        if not (source_root / name).is_file():
            errors.append(f"リリース必須ファイルがありません: {name}")
    try:
        ignored = (source_root / ".gitignore").read_text(encoding="utf-8")
        ignored_runtime_paths = (
            "data/",
            "logs/",
            "config/priorities.json",
            "config/notifications.json",
            "config/api.json",
            "config/recovery.json",
            "exports/",
        )
        if not all(value in ignored for value in ignored_runtime_paths):
            errors.append("実行時データまたは設定のGit除外が不足しています")
    except OSError:
        errors.append(".gitignoreを読み込めません")
    typer.echo(f"パッケージルート: {source_root}")
    typer.echo(f"version: {__version__}")
    if errors:
        for error in errors:
            typer.echo(f"ERROR: {error}")
        typer.echo("daily-review release-check: ERROR")
        raise typer.Exit(code=1)
    typer.echo("daily-review release-check: OK")
    typer.echo("OK   chat workflow")
    typer.echo("OK   chat prompt template")
    typer.echo("OK   chat import schema")
    typer.echo("OK   priorities config")
    typer.echo("OK   handoff workflow")
    typer.echo("OK   receive validation")
    typer.echo("OK   duplicate protection")
    typer.echo("OK   clipboard workflow")
    typer.echo("OK   migration definition")
    typer.echo("OK   goal commands")
    typer.echo("OK   goal schema")
    typer.echo("OK   goal storage")
    typer.echo("OK   goal backup")
    typer.echo("OK   milestone commands")
    typer.echo("OK   roadmap command")
    typer.echo("OK   dependency validation")
    typer.echo("OK   next action selection")
    typer.echo("OK   weekly planning commands")
    typer.echo("OK   daily planning commands")
    typer.echo("OK   goal linking commands")
    typer.echo("OK   progress application workflow")
    typer.echo("OK   weekly and monthly evaluations")
    typer.echo("OK   replan review and apply")
    typer.echo("OK   goal coach workflow")
    typer.echo("OK   goal design workflow")
    typer.echo("OK   v12 readiness check")
    typer.echo("OK   v1.2 final migration")
    typer.echo("OK   runtime data ignored by git")
    typer.echo("OK   v1.3 task, export, and notification foundations")
    typer.echo("OK   v1.3 command api and normalization foundations")
    typer.echo("OK   v1.3 backup, restore, rollover, and integrity foundations")
    typer.echo("v1.2.0 is ready")


@app.command()
def monthly(
    date: str | None = DateOption,
    root: Path | None = RootOption,
) -> None:
    """指定日を含む暦月の振り返りを作成します。"""
    base = _root(root)
    day = _day(date)
    start, end = month_range_for(day)
    summary = build_report(base, start, end, period_type="monthly")
    summary["weekly_trends"] = weekly_trends(base, start, end)
    month = start[:7]
    json_path = monthly_path(base, month)
    write_text(json_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    markdown_path = write_text(monthly_log_path(base, month), render_monthly(summary))
    typer.echo(f"月次集計を保存しました: {json_path}")
    typer.echo(f"Markdownを保存しました: {markdown_path}")
    typer.echo(f"対象月: {start}〜{end}")
    typer.echo(f"Main達成率: {'算出不可' if summary['main_summary']['percent'] is None else str(summary['main_summary']['percent']) + '%'}")
    minimum = summary["minimum_line_summary"]
    typer.echo(f"最低ライン達成率: {'算出不可' if minimum['percent'] is None else str(minimum['percent']) + '%'}")
    continuity = summary["continuity"]
    typer.echo(f"継続状況: 振り返り {continuity['review_recorded']['count']}/{continuity['review_recorded']['total']}日、結果 {continuity['task_results_recorded']['count']}/{continuity['task_results_recorded']['total']}日、確定版 {continuity['approved_plan']['count']}/{continuity['approved_plan']['total']}日")
    typer.echo(f"崩れた原因: {summary['failure_reasons'][0]['cause'] if summary['failure_reasons'] else 'なし'}")
    typer.echo(f"引き継ぎが多いタスク: {summary['carryover_analysis'][0]['task'] if summary['carryover_analysis'] else 'なし'}")
    typer.echo(f"翌月に変えること1つ: {summary['improvement_suggestion']['text']}")


if __name__ == "__main__":
    app()
