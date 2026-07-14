from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from . import __version__
from .archive import create_backup, restore_backup
from .date_utils import month_range_for, parse_date, today_string, tomorrow_of, week_range_for
from .markdown import render_daily, render_monthly, render_weekly
from .models import Plan, ProposalInput, ReviewInput, TaskResultsInput, dump_model, now_iso
from .storage import (
    atomic_write_json_many,
    daily_path,
    daily_log_path,
    init_workspace,
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
from .doctor import run_doctor


app = typer.Typer(
    help="毎日の振り返りと明日の指示書をローカル保存するCLIです。",
    no_args_is_help=True,
    invoke_without_command=True,
)


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


def _day(value: str | None) -> str:
    return value or today_string()


def _read_text_from_file_or_stdin(file: Path | None) -> str:
    if file:
        return file.read_text(encoding="utf-8")
    typer.echo("入力を貼り付けてください。終わったら Ctrl-D で保存します。", err=True)
    return sys.stdin.read()


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
    entry = load_daily(base, day)

    if entry and entry.get("tomorrow_plan_proposal") and not entry.get("tomorrow_plan_final"):
        typer.echo("明日の指示書が未承認です。")
        typer.echo(f"daily-review show-proposal --date {day}")
        typer.echo(f"daily-review approve-plan --date {day}")
        return

    if entry and entry.get("tomorrow_plan_final"):
        target = entry["tomorrow_plan_final"].get("target_date", tomorrow_of(day))
        typer.echo("今日の夜の処理は完了しています。")
        typer.echo("翌朝:")
        typer.echo(f"daily-review today --date {target}")
        return

    _, today_plan, pending_source = _find_plan_by_target(base, day)
    if today_plan and not pending_source:
        typer.echo("今日の指示書があります。")
        typer.echo(f"daily-review today --date {day}")
        return

    typer.echo("夜の振り返りが未保存です。")
    typer.echo("1. ChatGPTへ今日の結果を送る")
    typer.echo("2. JSONをコピーする")
    typer.echo("3. 以下を実行する")
    date_option = f" --date {day}" if include_date else ""
    typer.echo(f"daily-review close-day{date_option} --clipboard --dry-run")


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


@app.command()
def backup(
    root: Path | None = RootOption,
    output: Path | None = typer.Option(None, "--output", help="出力ZIP、または出力先ディレクトリ"),
) -> None:
    """data、logs、templates をZIPバックアップします。"""
    base = _root(root)
    try:
        path, manifest = create_backup(base, output)
    except (OSError, ValueError) as exc:
        typer.echo(f"バックアップできませんでした: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"バックアップを作成しました: {path}")
    typer.echo(f"ファイル数: {manifest['file_count']}")


@app.command()
def restore(
    backup_file: Path = typer.Argument(..., help="backup コマンドで作成したZIPファイル"),
    root: Path | None = RootOption,
    dry_run: bool = typer.Option(False, "--dry-run", help="復元内容だけを表示し、書き込みません。"),
    force: bool = typer.Option(False, "--force", help="競合前に安全バックアップを作成してから上書きします。"),
) -> None:
    """検証済みバックアップを安全に復元します。"""
    base = _root(root)
    try:
        result = restore_backup(base, backup_file, dry_run=dry_run, force=force)
    except (OSError, ValueError) as exc:
        typer.echo(f"復元できませんでした: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("復元前確認" if dry_run else "復元しました")
    typer.echo(f"復元予定ファイル: {len(result['files'])}件")
    typer.echo(f"新規作成: {len(result['new_files'])}件")
    typer.echo(f"競合: {len(result['conflicts'])}件")
    typer.echo(f"スキップ: {len(result['skipped'])}件")
    if result["conflicts"]:
        for name in result["conflicts"]:
            typer.echo(f"- {name}")
    if dry_run:
        typer.echo("dry-runのため書き込んでいません。")
    elif result.get("safety_backup"):
        typer.echo(f"上書き前バックアップ: {result['safety_backup']}")


@app.command()
def doctor(root: Path | None = RootOption) -> None:
    """保存構造とJSONを読み取り専用で点検します。"""
    report = run_doctor(_root(root))
    errors = [item for item in report["issues"] if item["level"] == "ERROR"]
    warnings = [item for item in report["issues"] if item["level"] == "WARN"]
    typer.echo("OK   ディレクトリ構造・テンプレート・JSONを点検しました")
    typer.echo(f"OK   日次JSON {report['daily_count']}件")
    for item in warnings + errors:
        typer.echo(f"{item['level']} {item['message']}")
    typer.echo(f"結果：WARN {len(warnings)}件、ERROR {len(errors)}件")
    if errors:
        raise typer.Exit(code=1)


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
