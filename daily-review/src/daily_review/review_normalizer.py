"""Conservative rule-based normalization for Japanese daily reviews."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from .command_models import MAX_RAW_INPUT
from .date_utils import parse_date


class NormalizationError(ValueError):
    pass


HEADINGS = {
    "done": ("今日できたこと", "できたこと", "今日やったこと", "完了"),
    "not_done": ("できなかったこと", "未完了", "未着手"),
    "causes": ("崩れた原因", "原因", "できなかった理由"),
    "tomorrow": ("明日やること", "明日のmain", "明日のタスク"),
    "minimum": ("最低限", "最低ライン", "最小行動"),
    "journal": ("日記", "メモ", "感想"),
}
HEADING_LOOKUP = {
    re.sub(r"\s+", "", label.lower()): key
    for key, labels in HEADINGS.items()
    for label in labels
}
BULLET = re.compile(r"^\s*(?:[-*・]|\d+[.)．])\s*(.*)$")
NEGATIVE = re.compile(r"(?:できなかった|未着手|やっていない|できず|未完了)")
MINIMUM = re.compile(r"^(?:最低限|最低ライン|最小行動)\s*(?:は|:)?\s*(.+)$", re.I)
WORST_CASE = re.compile(r"^最悪でも\s*(.+)$")
TOMORROW = re.compile(r"^明日(?:は|のタスクは|やることは)\s*(.+)$")
CAUSE = re.compile(r"^(?:原因|理由)(?:は|:)?\s*(.+)$")
EXPLICIT_DATE = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\s*の?振り返り)?$")


def _heading(line: str) -> str | None:
    normalized = line.strip().replace("：", ":")
    normalized = re.sub(r"[:：]\s*$", "", normalized)
    normalized = re.sub(r"\s+", "", normalized.lower())
    return HEADING_LOOKUP.get(normalized)


def _clean_item(line: str) -> str:
    match = BULLET.match(line)
    return (match.group(1) if match else line).strip()


def normalize_review(raw_input: str, *, effective_date: str) -> dict[str, Any]:
    parse_date(effective_date)
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise NormalizationError("入力内容が空です")
    if len(raw_input) > MAX_RAW_INPUT:
        raise NormalizationError(f"入力は{MAX_RAW_INPUT}文字以内にしてください")

    normalized: dict[str, Any] = {
        "date": effective_date,
        "done": [],
        "not_done": [],
        "causes": [],
        "tomorrow": [],
        "minimum": [],
        "journal": None,
        "unclassified": [],
    }
    warnings: list[dict[str, Any]] = []
    section: str | None = None
    journal_lines: list[str] = []
    saw_heading = False

    lines = raw_input.splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            if section == "journal" and journal_lines:
                journal_lines.append("")
            continue
        heading = _heading(stripped)
        if heading:
            section = heading
            saw_heading = True
            continue
        if stripped in {"今日の振り返り", "振り返り"}:
            continue
        explicit = EXPLICIT_DATE.fullmatch(stripped)
        if explicit:
            try:
                normalized["date"] = parse_date(
                    f"{int(explicit.group(1)):04d}-{int(explicit.group(2)):02d}-{int(explicit.group(3)):02d}"
                ).isoformat()
            except ValueError as exc:
                raise NormalizationError("日付が不正です") from exc
            continue
        if stripped == "昨日の振り返り":
            normalized["date"] = (
                parse_date(effective_date) - timedelta(days=1)
            ).isoformat()
            continue
        if stripped == "明日の振り返り":
            normalized["date"] = (
                parse_date(effective_date) + timedelta(days=1)
            ).isoformat()
            warnings.append(
                {
                    "code": "RELATIVE_FUTURE_DATE",
                    "message": "明日を対象日として解釈しました",
                    "line": line_number,
                }
            )
            continue

        item = _clean_item(stripped)
        if section == "journal":
            journal_lines.append(item)
            continue
        if section in {"done", "not_done", "causes", "tomorrow", "minimum"}:
            normalized[section].append(item)
            if section == "minimum" and item.startswith("最悪でも"):
                warnings.append(
                    {
                        "code": "AMBIGUOUS_MINIMUM",
                        "message": "「最悪でも」を最低限候補として解釈しました",
                        "line": line_number,
                    }
                )
            continue

        match = MINIMUM.match(item)
        if match:
            normalized["minimum"].append(match.group(1).strip())
        elif match := WORST_CASE.match(item):
            normalized["minimum"].append(match.group(1).strip())
            warnings.append(
                {
                    "code": "AMBIGUOUS_MINIMUM",
                    "message": "「最悪でも」を最低限候補として解釈しました",
                    "line": line_number,
                }
            )
        elif match := TOMORROW.match(item):
            normalized["tomorrow"].append(match.group(1).strip())
        elif match := CAUSE.match(item):
            normalized["causes"].append(match.group(1).strip())
        elif NEGATIVE.search(item):
            normalized["not_done"].append(item)
            warnings.append(
                {
                    "code": "RULE_BASED_NEGATION",
                    "message": "否定表現から未完了候補として解釈しました",
                    "line": line_number,
                }
            )
        else:
            normalized["unclassified"].append(item)

    if journal_lines:
        normalized["journal"] = "\n".join(journal_lines).rstrip()
    if normalized["unclassified"]:
        warnings.append(
            {
                "code": "UNCLASSIFIED_TEXT",
                "message": "分類できない文章をunclassifiedに保持しました",
                "count": len(normalized["unclassified"]),
            }
        )
    classified_count = sum(
        len(normalized[key])
        for key in ("done", "not_done", "causes", "tomorrow", "minimum")
    ) + bool(normalized["journal"])
    confidence = (
        "high"
        if saw_heading and classified_count and not normalized["unclassified"]
        else "medium"
        if classified_count
        else "low"
    )
    if confidence == "low":
        warnings.append(
            {
                "code": "LOW_CONFIDENCE",
                "message": "明確な見出しを認識できませんでした。保存前に確認してください",
            }
        )
    return {
        "raw_input": raw_input,
        "normalized": normalized,
        "warnings": warnings,
        "confidence": {"overall": confidence},
    }
