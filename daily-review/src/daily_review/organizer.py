"""Rule-based organization of raw natural-language inbox entries.

This module deliberately does not modify daily records.  Its output is only a
draft that a later command (and ultimately the user) can review.
"""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import draft_path, inbox_path, read_json_file


PARSER_VERSION = "rule-v1"

EDITABLE_DRAFT_FIELDS = (
    "today.main_candidates",
    "today.completed",
    "today.partial",
    "today.not_completed",
    "reflection.good",
    "reflection.problems",
    "reflection.causes",
    "reflection.change_next",
    "tomorrow.main_candidates",
    "tomorrow.other_tasks",
    "tomorrow.minimum_candidates",
    "journal",
    "unclassified",
)

# Keep the prioritisation policy separate from the parsing rules.  It is used
# only when choosing the three displayed Main candidates; source order remains
# the order shown to the user.
MAIN_PRIORITY = (
    "院試",
    "研究",
    "筋トレ",
    "競馬AI",
    "開発",
    "副業",
    "松尾研",
    "読書",
)

COMPLETED_KEYWORDS = (
    "やった", "終わった", "完了した", "進めた", "できた", "解いた", "読んだ", "確認した", "提出した", "実行した",
)
PARTIAL_KEYWORDS = ("少し進めた", "途中まで", "一部", "半分", "まだ途中", "着手した")
NOT_COMPLETED_KEYWORDS = ("できなかった", "やらなかった", "未着手", "間に合わなかった", "進まなかった", "サボった")
TOMORROW_KEYWORDS = ("明日は", "明日やる", "明日進める", "明日取り組む", "次は", "明日の予定")
GOOD_KEYWORDS = ("よかった", "嬉しかった", "うまくいった", "集中できた", "楽しかった", "助かった")
PROBLEM_KEYWORDS = ("集中できなかった", "疲れた", "眠かった", "崩れた", "失敗した", "困った", "遅れた")
CHANGE_KEYWORDS = ("明日変える", "次は", "改善する", "気をつける", "しないようにする")


def _empty_draft(day: str, *, created_at: str | None = None) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "date": day,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
        "source_entry_ids": [],
        "parser_version": PARSER_VERSION,
        "status": "draft",
        "approved_at": None,
        "approved_daily_path": None,
        "revision": 0,
        "edit_history": [],
        "today": {"main_candidates": [], "completed": [], "partial": [], "not_completed": []},
        "reflection": {"good": [], "problems": [], "causes": [], "change_next": []},
        "tomorrow": {"main_candidates": [], "other_tasks": [], "minimum_candidates": []},
        "journal": [],
        "unclassified": [],
    }


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _split_sentences(raw_text: str) -> list[str]:
    """Split only on explicit Japanese sentence boundaries and line breaks."""
    return [part.strip() for part in re.split(r"[\n。！？]+", raw_text) if part.strip()]


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _cause_from(sentence: str) -> str | None:
    marker = re.search(r"(?:原因は|理由は|なぜなら|崩れた原因)\s*(.+)", sentence)
    if marker:
        return marker.group(1).strip()
    blamed = re.search(r"(.+?)(?:のせいで|のせい)", sentence)
    if blamed:
        return blamed.group(1).strip(" 、")
    return None


def _tomorrow_items(sentence: str) -> list[str]:
    text = sentence
    for prefix in TOMORROW_KEYWORDS:
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip("は：: 、")
            break
    # A Japanese comma separates explicit independent proposals.  Do not split
    # on ASCII punctuation so terms such as O VII/O VIII remain untouched.
    return [item.strip() for item in text.split("、") if item.strip()]


def _is_clear_journal(sentence: str) -> bool:
    event_cues = ("今日は", "研究室", "先生", "会った", "話した", "行った", "帰った", "食べた")
    feeling_cues = ("よかった", "嬉しかった", "楽しかった", "疲れた", "困った")
    return _contains_any(sentence, event_cues) and _contains_any(sentence, feeling_cues)


def _priority_index(text: str) -> int:
    for index, area in enumerate(MAIN_PRIORITY):
        if area in text:
            return index
    return len(MAIN_PRIORITY)


def _select_main(candidates: list[str]) -> tuple[list[str], list[str]]:
    """Pick three with priority as a tie-breaker, while displaying source order."""
    selected_indexes = sorted(
        index for index, _ in sorted(enumerate(candidates), key=lambda item: (_priority_index(item[1]), item[0]))[:3]
    )
    selected_set = set(selected_indexes)
    return ([value for index, value in enumerate(candidates) if index in selected_set],
            [value for index, value in enumerate(candidates) if index not in selected_set])


def _ensure_shape(draft: dict[str, Any], day: str) -> dict[str, Any]:
    """Retain unknown future fields while making legacy drafts appendable."""
    base = _empty_draft(day, created_at=draft.get("created_at") if isinstance(draft.get("created_at"), str) else None)
    result = deepcopy(draft)
    for key, default in base.items():
        if key not in result or not isinstance(result[key], type(default)):
            result[key] = deepcopy(default)
    for group, fields in (("today", base["today"]), ("reflection", base["reflection"]), ("tomorrow", base["tomorrow"])):
        if not isinstance(result[group], dict):
            result[group] = deepcopy(fields)
        for key, default in fields.items():
            if not isinstance(result[group].get(key), list):
                result[group][key] = deepcopy(default)
    return result


def normalize_draft(draft: dict[str, Any], day: str) -> dict[str, Any]:
    """Return a compatible draft shape without writing or migrating a file."""
    return _ensure_shape(draft, day)


def load_draft(root: Path, day: str) -> dict[str, Any] | None:
    path = draft_path(root, day)
    if not path.exists():
        return None
    value = read_json_file(path)
    if not isinstance(value, dict) or value.get("date") not in (None, day):
        raise ValueError("整理ドラフトの日付が不正です")
    return normalize_draft(value, day)


def add_draft_revision(draft: dict[str, Any], changed_fields: list[str]) -> None:
    """Record a bounded audit trail for user edits and re-organization."""
    revision = draft.get("revision", 0)
    if not isinstance(revision, int) or revision < 0:
        revision = 0
    draft["revision"] = revision + 1
    history = draft.get("edit_history")
    if not isinstance(history, list):
        history = []
    history.append({"edited_at": now_iso(), "changed_fields": changed_fields})
    draft["edit_history"] = history[-50:]


def _classify_entries(entries: list[dict[str, Any]], draft: dict[str, Any]) -> None:
    for entry in entries:
        raw_text = entry["raw_text"]
        for sentence in _split_sentences(raw_text):
            classified = False
            # Partial wording takes precedence over completed wording.
            if _contains_any(sentence, PARTIAL_KEYWORDS):
                _append_unique(draft["today"]["partial"], sentence)
                classified = True
            elif _contains_any(sentence, NOT_COMPLETED_KEYWORDS):
                _append_unique(draft["today"]["not_completed"], sentence)
                classified = True
            elif _contains_any(sentence, COMPLETED_KEYWORDS):
                _append_unique(draft["today"]["completed"], sentence)
                classified = True

            if _contains_any(sentence, TOMORROW_KEYWORDS):
                for item in _tomorrow_items(sentence):
                    _append_unique(draft["tomorrow"]["other_tasks"], item)
                classified = True
            if _contains_any(sentence, GOOD_KEYWORDS):
                _append_unique(draft["reflection"]["good"], sentence)
                classified = True
            if _contains_any(sentence, PROBLEM_KEYWORDS):
                _append_unique(draft["reflection"]["problems"], sentence)
                classified = True
            cause = _cause_from(sentence)
            if cause:
                _append_unique(draft["reflection"]["causes"], cause)
                classified = True
            if _contains_any(sentence, CHANGE_KEYWORDS):
                _append_unique(draft["reflection"]["change_next"], sentence)
                classified = True
            if _is_clear_journal(sentence):
                _append_unique(draft["journal"], sentence)
                classified = True
            if not classified:
                _append_unique(draft["unclassified"], sentence)


def _refresh_candidates(draft: dict[str, Any], ordered_sentences: list[str] | None = None) -> None:
    stored_today = list(draft["today"]["completed"]) + list(draft["today"]["partial"])
    if ordered_sentences is not None:
        stored_set = set(stored_today)
        today_candidates = [sentence for sentence in ordered_sentences if sentence in stored_set]
        for sentence in stored_today:
            _append_unique(today_candidates, sentence)
    else:
        today_candidates = stored_today
    today_candidates = [item for item in today_candidates if len(item.strip()) >= 4]
    draft["today"]["main_candidates"], _ = _select_main(today_candidates)

    all_tomorrow = list(draft["tomorrow"]["main_candidates"]) + list(draft["tomorrow"]["other_tasks"])
    if ordered_sentences is not None:
        stored_set = set(all_tomorrow)
        ordered_tomorrow = [
            item
            for sentence in ordered_sentences
            if _contains_any(sentence, TOMORROW_KEYWORDS)
            for item in _tomorrow_items(sentence)
            if item in stored_set
        ]
        for item in all_tomorrow:
            _append_unique(ordered_tomorrow, item)
        all_tomorrow = ordered_tomorrow
    unique_tomorrow: list[str] = []
    for item in all_tomorrow:
        _append_unique(unique_tomorrow, item)
    draft["tomorrow"]["main_candidates"], draft["tomorrow"]["other_tasks"] = _select_main(unique_tomorrow)


def _load_inbox_entries(root: Path, day: str) -> list[dict[str, Any]]:
    path = inbox_path(root, day)
    if not path.exists():
        return []
    payload = read_json_file(path)
    if not isinstance(payload, dict) or payload.get("date") not in (None, day):
        raise ValueError("inbox JSONの日付が不正です")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("inbox JSONのentriesが不正です")
    checked: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("raw_text"), str):
            raise ValueError("inbox JSONのentryが不正です")
        if entry["id"] in known_ids:
            raise ValueError("inbox JSONに重複した入力IDがあります")
        known_ids.add(entry["id"])
        checked.append(entry)
    return checked


def _classification_counts(entries: list[dict[str, Any]], draft: dict[str, Any]) -> tuple[int, int]:
    sentences: list[str] = []
    for entry in entries:
        for sentence in _split_sentences(entry["raw_text"]):
            _append_unique(sentences, sentence)
    unclassified = set(draft["unclassified"])
    unclassified_count = sum(sentence in unclassified for sentence in sentences)
    return len(sentences) - unclassified_count, unclassified_count


def organize_entries(
    day: str,
    entries: list[dict[str, Any]],
    *,
    existing: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build a draft from supplied entries without writing any file."""
    if not entries:
        raise LookupError(f"{day}の入力がありません")

    if force or existing is None:
        # Unknown fields are retained when explicitly rebuilding an existing draft.
        draft = _ensure_shape(existing or {}, day)
        for group, fields in (("today", ("main_candidates", "completed", "partial", "not_completed")),
                              ("reflection", ("good", "problems", "causes", "change_next")),
                              ("tomorrow", ("main_candidates", "other_tasks", "minimum_candidates"))):
            for field in fields:
                draft[group][field] = []
        draft["journal"] = []
        draft["unclassified"] = []
        draft["source_entry_ids"] = []
        new_entries = entries
    else:
        draft = existing
        source_ids = draft.get("source_entry_ids") or []
        if not all(isinstance(value, str) for value in source_ids):
            raise ValueError("整理ドラフトのsource_entry_idsが不正です")
        new_entries = [entry for entry in entries if entry["id"] not in set(source_ids)]
        if not new_entries:
            classified_count, unclassified_count = _classification_counts(entries, draft)
            return {
                "changed": False,
                "draft": draft,
                "entry_count": len(entries),
                "new_entry_count": 0,
                "classified_count": classified_count,
                "unclassified_count": unclassified_count,
            }

    _classify_entries(new_entries, draft)
    for entry in new_entries:
        _append_unique(draft["source_entry_ids"], entry["id"])
    ordered_sentences = [sentence for entry in entries for sentence in _split_sentences(entry["raw_text"])]
    _refresh_candidates(draft, ordered_sentences)
    draft["date"] = day
    draft["parser_version"] = PARSER_VERSION
    # A re-organized draft must be reviewed again.  Existing daily data remains
    # untouched, so this does not revoke or delete a previous record.
    draft["status"] = "draft"
    draft["approved_at"] = None
    draft["approved_daily_path"] = None
    add_draft_revision(draft, ["organization"])
    draft["updated_at"] = now_iso()
    draft.setdefault("created_at", draft["updated_at"])
    classified_count, unclassified_count = _classification_counts(entries, draft)
    return {
        "changed": True,
        "draft": draft,
        "entry_count": len(entries),
        "new_entry_count": len(new_entries),
        "classified_count": classified_count,
        "unclassified_count": unclassified_count,
    }


def organize_day(root: Path, day: str, *, force: bool = False) -> dict[str, Any]:
    """Build an append-only organization draft.  This function never writes."""
    entries = _load_inbox_entries(root, day)
    result = organize_entries(day, entries, existing=load_draft(root, day), force=force)
    result["path"] = draft_path(root, day)
    return result
