from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from .date_utils import today_string, tomorrow_of, week_range_for
from .markdown import render_daily, render_weekly
from .models import Plan, ProposalInput, ReviewInput, dump_model, now_iso
from .storage import (
    daily_log_path,
    init_workspace,
    load_daily,
    load_or_create_daily,
    read_json_file,
    resolve_root,
    save_daily,
    weekly_log_path,
    weekly_path,
    write_text,
)
from .validation import ValidationResult, validate_daily, validate_plan
from .weekly import build_weekly_summary


app = typer.Typer(help="毎日の振り返りと明日の指示書をローカル保存するCLIです。", no_args_is_help=True)


RootOption = typer.Option(None, "--root", help="保存先ルート。未指定ならカレントディレクトリです。")
DateOption = typer.Option(None, "--date", help="対象日（YYYY-MM-DD）。未指定なら今日です。")


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
    try:
        if file:
            return json.loads(file.read_text(encoding="utf-8"))
        typer.echo("JSONを貼り付けてください。終わったら Ctrl-D で保存します。", err=True)
        return json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        source = str(file) if file else "標準入力"
        raise typer.BadParameter(
            f"JSONの形式が不正です: {source} の {exc.lineno}行{exc.colno}列付近（{exc.msg}）"
        ) from exc


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
    payload = _read_json_from_file_or_stdin(file)
    payload.pop("status", None)
    try:
        proposal_input = ProposalInput.model_validate(payload)
    except ValidationError as exc:
        raise typer.BadParameter(_format_validation_error(exc)) from exc

    plan = Plan(
        **dump_model(proposal_input),
        status="pending_review",
    )
    plan_payload = dump_model(plan)
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

    entry["tomorrow_plan_final"] = final
    json_path = save_daily(base, day, entry)
    markdown_path = _regenerate_daily_markdown(base, day, entry)
    typer.echo(f"確定版を保存しました: {json_path}")
    typer.echo(f"Markdownを更新しました: {markdown_path}")
    _print_validation(final_result)
    typer.echo(f"明日の朝: daily-review today --date {final['target_date']}")


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

    typer.echo(f"今日の指示書｜{target}")
    typer.echo("Main")
    for index, item in enumerate(plan.get("main") or [], start=1):
        typer.echo(f"{index}. {item}")
    typer.echo("優先タスク")
    for index, task in enumerate(plan.get("tasks") or [], start=1):
        typer.echo(f"{index}. [{task.get('area')}] {task.get('task')}")
        typer.echo(f"   最低ライン: {task.get('minimum_line')}")
    typer.echo("今日変えること")
    typer.echo(plan.get("one_change_tomorrow", "未保存"))


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
    if summary["warnings"]:
        for warning in summary["warnings"]:
            typer.echo(f"警告: {warning}")
    rate = summary["minimum_line_rate"]
    typer.echo(f"最低ライン達成率: {rate['achieved']}/{rate['total']}（{rate['percent']}%）")
    typer.echo(f"確定版指示書を作れた日数: {summary['approved_plan_days']}")


if __name__ == "__main__":
    app()
