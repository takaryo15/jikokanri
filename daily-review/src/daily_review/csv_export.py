"""Deterministic and spreadsheet-safe CSV exports."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .date_utils import month_range_for, parse_date, week_range_for
from .storage import read_json_file
from .task_service import collect_tasks


EXPORT_TYPES = {"reviews", "tasks", "instructions", "all"}
REVIEW_COLUMNS = (
    "date",
    "completed_items",
    "incomplete_items",
    "causes",
    "tomorrow_items",
    "minimum_items",
    "journal",
    "satisfaction",
    "created_at",
    "updated_at",
    "raw_log_path",
)
TASK_COLUMNS = (
    "id",
    "title",
    "description",
    "status",
    "priority",
    "category",
    "due_date",
    "is_main",
    "is_minimum",
    "source_review_date",
    "created_at",
    "updated_at",
    "completed_at",
)
INSTRUCTION_COLUMNS = (
    "target_date",
    "status",
    "main_1",
    "main_2",
    "main_3",
    "minimum",
    "optional_tasks",
    "generated_from_review_date",
    "approved_at",
    "created_at",
    "updated_at",
)


class ExportError(ValueError):
    pass


def _json_array(values: Any) -> str:
    return json.dumps(
        values if isinstance(values, list) else [],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _safe_cell(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _daily_entries(root: Path) -> list[dict[str, Any]]:
    directory = root / "data" / "daily"
    values = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        value = read_json_file(path)
        if isinstance(value, dict):
            values.append(value)
    return values


def review_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    for entry in _daily_entries(root):
        day = str(entry.get("date", ""))
        quick = (
            entry.get("quick_review")
            if isinstance(entry.get("quick_review"), dict)
            else {}
        )
        review = (
            entry.get("structured_review")
            if isinstance(entry.get("structured_review"), dict)
            else {}
        )
        plan = (
            entry.get("tomorrow_plan_final")
            or entry.get("tomorrow_plan_proposal")
            or {}
        )
        completed = quick.get("done") if quick else review.get("what_went_well")
        incomplete = (
            quick.get("not_done")
            if quick
            else [
                item.get("area", "")
                for item in review.get("today_main") or []
                if isinstance(item, dict)
                and item.get("status") not in {"完了", "completed"}
            ]
        )
        causes = quick.get("causes") if quick else review.get("breakdown_causes")
        tomorrow = (
            quick.get("tomorrow")
            if quick
            else [
                item.get("task", "")
                for item in plan.get("tasks") or []
                if isinstance(item, dict)
            ]
        )
        minimum = (
            quick.get("minimum")
            if quick
            else [
                item.get("minimum_line", "")
                for item in plan.get("tasks") or []
                if isinstance(item, dict) and item.get("minimum_line")
            ]
        )
        rows.append(
            {
                "date": day,
                "completed_items": _json_array(completed),
                "incomplete_items": _json_array(incomplete),
                "causes": _json_array(causes),
                "tomorrow_items": _json_array(tomorrow),
                "minimum_items": _json_array(minimum),
                "journal": entry.get("diary", ""),
                "satisfaction": entry.get("satisfaction", ""),
                "created_at": entry.get("created_at", ""),
                "updated_at": entry.get("updated_at", ""),
                "raw_log_path": f"logs/{day}.md" if entry.get("raw_log") else "",
            }
        )
    return rows


def task_rows(root: Path) -> list[dict[str, Any]]:
    return [
        {key: item.get(key, "") for key in TASK_COLUMNS} for item in collect_tasks(root)
    ]


def instruction_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    for entry in _daily_entries(root):
        for key in ("tomorrow_plan_proposal", "tomorrow_plan_final"):
            plan = entry.get(key)
            if not isinstance(plan, dict):
                continue
            main = list(plan.get("main") or [])[:3]
            tasks = [item for item in plan.get("tasks") or [] if isinstance(item, dict)]
            minimum = [
                item.get("minimum_line", "")
                for item in tasks
                if item.get("minimum_line")
            ]
            optional = [
                item.get("task", "")
                for item in tasks
                if item.get("task") not in set(main)
                and item.get("area") not in set(main)
            ]
            rows.append(
                {
                    "target_date": plan.get("target_date", ""),
                    "status": plan.get("status", ""),
                    "main_1": main[0] if len(main) > 0 else "",
                    "main_2": main[1] if len(main) > 1 else "",
                    "main_3": main[2] if len(main) > 2 else "",
                    "minimum": _json_array(minimum),
                    "optional_tasks": _json_array(optional),
                    "generated_from_review_date": entry.get("date", ""),
                    "approved_at": plan.get("approved_at", ""),
                    "created_at": plan.get("created_at", entry.get("created_at", "")),
                    "updated_at": plan.get("updated_at", entry.get("updated_at", "")),
                }
            )
    rows.sort(
        key=lambda item: (
            item["target_date"],
            item["status"],
            item["generated_from_review_date"],
        )
    )
    return rows


def period_range(
    *, day: str | None, date_from: str | None, date_to: str | None, period: str | None
) -> tuple[str | None, str | None]:
    for value in (day, date_from, date_to):
        if value is not None:
            parse_date(value)
    if period not in {None, "week", "month"}:
        raise ExportError("periodはweekまたはmonthにしてください")
    if day and (date_from or date_to):
        raise ExportError("--dateと--from/--toは同時に指定できません")
    if period and (date_from or date_to):
        raise ExportError("--periodと--from/--toは同時に指定できません")
    if period:
        if not day:
            raise ExportError("--periodには基準となる--dateが必要です")
        return week_range_for(day) if period == "week" else month_range_for(day)
    if day:
        return day, day
    if date_from and date_to and date_from > date_to:
        raise ExportError("--fromは--to以前にしてください")
    return date_from, date_to


def _row_date(kind: str, row: dict[str, Any]) -> str:
    return (
        row["date"]
        if kind == "reviews"
        else row["due_date"]
        if kind == "tasks"
        else row["target_date"]
    )


def _filter(
    rows: list[dict[str, Any]], kind: str, start: str | None, end: str | None
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if (not start or _row_date(kind, row) >= start)
        and (not end or _row_date(kind, row) <= end)
    ]


def _write_csv(
    path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]], *, excel: bool
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(name)
    try:
        with os.fdopen(
            fd, "w", encoding="utf-8-sig" if excel else "utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {column: _safe_cell(row.get(column, "")) for column in columns}
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def export_csv(
    root: Path,
    *,
    kind: str,
    output: Path | None,
    start: str | None,
    end: str | None,
    excel: bool,
    force: bool,
) -> dict[str, Any]:
    if kind not in EXPORT_TYPES:
        raise ExportError(
            "typeはreviews、tasks、instructions、allのいずれかにしてください"
        )
    builders = {
        "reviews": (REVIEW_COLUMNS, review_rows),
        "tasks": (TASK_COLUMNS, task_rows),
        "instructions": (INSTRUCTION_COLUMNS, instruction_rows),
    }
    kinds = tuple(builders) if kind == "all" else (kind,)
    base = output or (root / "exports" / ("csv" if kind == "all" else f"{kind}.csv"))
    targets: dict[str, Path] = {}
    for item in kinds:
        if kind == "all":
            target = base / f"{item}.csv"
        elif base.suffix.lower() == ".csv":
            target = base
        else:
            target = base / f"{item}.csv"
        targets[item] = target
    conflicts = [path for path in targets.values() if path.exists()]
    if conflicts and not force:
        raise FileExistsError(
            "出力先がすでに存在します: " + ", ".join(str(path) for path in conflicts)
        )
    result = []
    for item in kinds:
        columns, builder = builders[item]
        rows = _filter(builder(root), item, start, end)
        rows.sort(key=lambda row: tuple(str(row.get(column, "")) for column in columns))
        _write_csv(targets[item], columns, rows, excel=excel)
        result.append({"type": item, "path": targets[item], "rows": len(rows)})
    return {"files": result, "encoding": "utf-8-sig" if excel else "utf-8"}
