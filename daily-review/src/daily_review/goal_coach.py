"""Clipboard-only ChatGPT coaching for saved goal evaluations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evaluation import (
    EvaluationError, attach_coach_analysis, load_monthly_evaluation, load_weekly_evaluation,
)


COACH_SCHEMA_VERSION = "1.0"
ANALYSIS_FIELDS = ("strengths", "problems", "root_causes", "patterns", "recommendations", "evidence")


class GoalCoachError(ValueError):
    pass


def evaluation_for_period(root: Path, *, week: str | None = None, month: str | None = None) -> tuple[str, str, dict[str, Any]]:
    if (week is None) == (month is None):
        raise GoalCoachError("--week または --month のどちらか一方を指定してください")
    if week:
        evaluation = load_weekly_evaluation(root, week)
        if not evaluation: raise GoalCoachError("保存済み週次評価が見つかりません")
        return "week", f"{evaluation['week_start']}_{evaluation['week_end']}", evaluation
    evaluation = load_monthly_evaluation(root, month or "")
    if not evaluation: raise GoalCoachError("保存済み月次評価が見つかりません")
    return "month", month or "", evaluation


def build_coach_prompt(root: Path, *, week: str | None = None, month: str | None = None) -> str:
    period_type, period_id, evaluation = evaluation_for_period(root, week=week, month=month)
    context = {
        "period_type": period_type, "period_id": period_id,
        "summary": evaluation.get("summary") or {}, "goals": evaluation.get("goal_evaluations") or [],
        "diagnostics": evaluation.get("diagnostics") or [], "automatic_recommendations": evaluation.get("recommendations") or [],
    }
    schema = {"schema_version": COACH_SCHEMA_VERSION, "workflow": "goal_coach", "period_type": period_type, "period_id": period_id, "analysis": {field: [] for field in ANALYSIS_FIELDS}}
    return "\n".join([
        "# daily-review goal coach", "", "以下の保存済み評価だけを根拠に、目標と計画の見直しを補助してください。",
        "", "ルール:", "- 原文にない事実を追加しない", "- 医療・心理診断をしない", "- 人格批判をしない",
        "- 一時的失敗を能力不足と断定しない", "- 修正案は確定せず、必ずevidenceを付ける",
        "- 次のschemaと同じJSONオブジェクトをコードブロック1つだけで返す", "",
        "評価コンテキスト:", "```json", json.dumps(context, ensure_ascii=False, indent=2), "```", "",
        "回答schema:", "```json", json.dumps(schema, ensure_ascii=False, indent=2), "```", "",
    ])


def validate_coach_payload(payload: Any, *, period_type: str, period_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict): raise GoalCoachError("coach回答はJSONオブジェクトにしてください")
    allowed = {"schema_version", "workflow", "period_type", "period_id", "analysis"}
    unknown = set(payload) - allowed
    if unknown: raise GoalCoachError("coach回答に未知フィールドがあります: " + ", ".join(sorted(unknown)))
    if payload.get("schema_version") != COACH_SCHEMA_VERSION or payload.get("workflow") != "goal_coach": raise GoalCoachError("coach schema_versionまたはworkflowが不正です")
    if payload.get("period_type") != period_type or payload.get("period_id") != period_id: raise GoalCoachError("coach回答の対象期間が一致しません")
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict): raise GoalCoachError("coach analysisがありません")
    unknown_analysis = set(analysis) - set(ANALYSIS_FIELDS)
    if unknown_analysis: raise GoalCoachError("coach analysisに未知フィールドがあります: " + ", ".join(sorted(unknown_analysis)))
    for field in ANALYSIS_FIELDS:
        values = analysis.get(field, [])
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values): raise GoalCoachError(f"analysis.{field}は空でない文字列の配列にしてください")
    if (analysis.get("recommendations") or analysis.get("problems") or analysis.get("root_causes")) and not analysis.get("evidence"):
        raise GoalCoachError("分析・修正案にはevidenceが必要です")
    return payload


def receive_coach_payload(root: Path, payload: dict[str, Any], *, week: str | None = None, month: str | None = None) -> dict[str, Any]:
    period_type, period_id, evaluation = evaluation_for_period(root, week=week, month=month)
    validated = validate_coach_payload(payload, period_type=period_type, period_id=period_id)
    try:
        attach_coach_analysis(root, evaluation, validated["analysis"], period_type=period_type)
    except EvaluationError as exc:
        raise GoalCoachError(str(exc)) from exc
    return validated
