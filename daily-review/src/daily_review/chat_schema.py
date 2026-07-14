"""Safe parsing and validation for copied ChatGPT reflection JSON."""
from __future__ import annotations

import json
from datetime import timedelta
from difflib import get_close_matches
from typing import Any

from .date_utils import parse_date, today_string


SCHEMA_VERSION = "1.0"
MAX_INPUT_BYTES = 100 * 1024

ROOT_FIELDS = {"schema_version", "date", "raw_text", "today", "reflection", "tomorrow", "journal", "unclassified"}
OPTIONAL_ROOT_FIELDS = {"handoff"}
SECTION_FIELDS = {
    "today": {"main", "completed", "partial", "not_completed"},
    "reflection": {"good", "problems", "causes", "change_next"},
    "tomorrow": {"main", "other_tasks", "minimum"},
}


class ChatSchemaError(ValueError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    """Extract exactly one JSON object without relying on a single regex."""
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ChatSchemaError("入力サイズが上限を超えています（上限: 100KB）")
    lines = text.splitlines()
    inside_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            if inside_fence:
                inside_fence = False
            else:
                inside_fence = True
    if inside_fence:
        raise ChatSchemaError("JSONコードブロックが閉じられていません")
    # JSONDecoder lets ordinary prose and Markdown coexist without treating a
    # code fence as a trust boundary.  It also sees a second object outside a
    # fence, which must be rejected rather than silently ignored.
    candidates = _find_objects(text)
    if not candidates:
        raise ChatSchemaError("JSONを抽出できません")
    if len(candidates) != 1:
        raise ChatSchemaError("JSON候補が複数あります。1つのJSONだけをコピーしてください")
    return candidates[0]


def _find_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict):
            candidates.append(value)
            index = start + end
        else:
            index = start + 1
    return candidates


def validate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate required data and return a safe, known-fields-only payload."""
    if not isinstance(payload, dict):
        raise ChatSchemaError("JSONの最上位はオブジェクトにしてください")
    missing = sorted(ROOT_FIELDS - set(payload))
    if missing:
        raise ChatSchemaError("必須フィールドがありません: " + ", ".join(missing))
    version = payload["schema_version"]
    if version != SCHEMA_VERSION:
        raise ChatSchemaError(f"未対応のschema_versionです: {version}\n対応バージョン: {SCHEMA_VERSION}")
    if not isinstance(payload["date"], str):
        raise ChatSchemaError("dateはYYYY-MM-DD形式の文字列にしてください")
    try:
        imported_day = parse_date(payload["date"])
    except ValueError as exc:
        raise ChatSchemaError("dateはYYYY-MM-DD形式にしてください") from exc
    if imported_day > parse_date(today_string()) + timedelta(days=366):
        raise ChatSchemaError("dateが不自然に遠い未来です")
    raw_text = payload["raw_text"]
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ChatSchemaError("raw_textは空でない文字列にしてください")

    warnings = _unknown_field_warnings(payload, ROOT_FIELDS | OPTIONAL_ROOT_FIELDS)
    result: dict[str, Any] = {"schema_version": version, "date": payload["date"], "raw_text": raw_text}
    for section, fields in SECTION_FIELDS.items():
        value = payload[section]
        if not isinstance(value, dict):
            raise ChatSchemaError(f"{section}はオブジェクトにしてください")
        warnings.extend(_unknown_field_warnings(value, fields, section=section))
        missing_fields = sorted(fields - set(value))
        if missing_fields:
            raise ChatSchemaError(f"{section}の必須フィールドがありません: " + ", ".join(missing_fields))
        result[section] = {field: _validate_text_list(value[field], f"{section}.{field}") for field in fields}
    result["journal"] = _validate_text_list(payload["journal"], "journal")
    result["unclassified"] = _validate_text_list(payload["unclassified"], "unclassified")
    if "handoff" in payload:
        if not isinstance(payload["handoff"], dict):
            raise ChatSchemaError("handoffはオブジェクトにしてください")
        result["handoff"] = dict(payload["handoff"])
    if len(result["today"]["main"]) > 3 or len(result["tomorrow"]["main"]) > 3:
        raise ChatSchemaError("Mainは最大3件です")
    return result, warnings


def _validate_text_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ChatSchemaError(f"{field}は文字列の配列にしてください")
    if any(not item.strip() for item in value):
        raise ChatSchemaError(f"{field}に空白だけの値があります")
    if len(value) != len(set(value)):
        raise ChatSchemaError(f"{field}に重複値があります")
    return list(value)


def _unknown_field_warnings(value: dict[str, Any], known: set[str], *, section: str = "") -> list[str]:
    warnings: list[str] = []
    for key in sorted(set(value) - known):
        label = f"{section}.{key}" if section else key
        warning = f"unknown field: {label}"
        close = get_close_matches(key, sorted(known), n=1, cutoff=0.75)
        if close:
            warning += f"（もしかして {section + '.' if section else ''}{close[0]} ですか？）"
        warnings.append(warning)
    return warnings
