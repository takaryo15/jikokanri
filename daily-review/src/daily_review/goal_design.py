"""Reviewable, API-free ChatGPT goal design sessions."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from .goals import (
    GoalError,
    goal_path,
    load_goals,
    new_goal,
    new_milestone,
    new_step,
    validate_goal,
)
from .models import now_iso
from .storage import atomic_write_json_data, atomic_write_json_data_many, read_json_file


DESIGN_STATUSES = {"questioning", "proposed", "applied", "cancelled"}
PROPOSAL_FIELDS = {"goal", "milestones"}
GOAL_FIELDS = {"title", "level", "category", "description", "start_date", "due_date", "qualitative", "metrics"}
MILESTONE_FIELDS = {"title", "description", "start_date", "due_date", "qualitative", "steps"}
STEP_FIELDS = {"title", "description", "start_date", "due_date", "minimum"}


class GoalDesignError(ValueError):
    pass


def designs_dir(root: Path) -> Path:
    return root / "data" / "goal-designs"


def design_path(root: Path, design_id: str) -> Path:
    if not isinstance(design_id, str) or not design_id.startswith("design-") or len(design_id) != 15:
        raise GoalDesignError("design IDの形式が不正です")
    return designs_dir(root) / f"{design_id}.json"


def create_design(root: Path, text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise GoalDesignError("目標の原文を入力してください")
    timestamp = now_iso()
    value = {
        "id": f"design-{uuid.uuid4().hex[:8]}",
        "status": "questioning",
        "raw_goal": text,
        "questions": [
            "いつまでに達成したいですか？",
            "達成を判断できる定性・定量の条件は何ですか？",
            "最初の実行ステップは何ですか？",
        ],
        "answers": [],
        "proposal": None,
        "applied_goal_id": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "applied_at": None,
        "revision": 1,
    }
    atomic_write_json_data(design_path(root, value["id"]), value)
    return value


def load_design(root: Path, design_id: str) -> dict[str, Any]:
    path = design_path(root, design_id)
    if not path.is_file():
        raise GoalDesignError("目標設計セッションが見つかりません")
    value = read_json_file(path)
    if not isinstance(value, dict) or value.get("status") not in DESIGN_STATUSES:
        raise GoalDesignError("目標設計JSONが不正です")
    return value


def save_answer(root: Path, design_id: str, answer: str) -> dict[str, Any]:
    value = load_design(root, design_id)
    if value["status"] != "questioning":
        raise GoalDesignError("質問受付中のセッションだけ回答できます")
    if not answer.strip():
        raise GoalDesignError("回答が空です")
    value.setdefault("answers", []).append(answer)
    value["updated_at"] = now_iso()
    value["revision"] = int(value.get("revision", 1)) + 1
    atomic_write_json_data(design_path(root, design_id), value)
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise GoalDesignError(f"{label}に未知フィールドがあります: {', '.join(unknown)}")


def validate_proposal(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GoalDesignError("proposalはJSONオブジェクトにしてください")
    _reject_unknown(payload, PROPOSAL_FIELDS, "proposal")
    goal = payload.get("goal")
    milestones = payload.get("milestones", [])
    if not isinstance(goal, dict) or not isinstance(milestones, list):
        raise GoalDesignError("goalまたはmilestonesの形式が不正です")
    _reject_unknown(goal, GOAL_FIELDS, "goal")
    if not isinstance(goal.get("title"), str) or not goal["title"].strip() or goal.get("level") not in {"vision", "long", "medium", "short"}:
        raise GoalDesignError("goal.titleまたはgoal.levelが不正です")
    for milestone in milestones:
        if not isinstance(milestone, dict):
            raise GoalDesignError("milestoneの形式が不正です")
        _reject_unknown(milestone, MILESTONE_FIELDS, "milestone")
        if not isinstance(milestone.get("title"), str) or not milestone["title"].strip():
            raise GoalDesignError("milestone.titleが不正です")
        steps = milestone.get("steps", [])
        if not isinstance(steps, list):
            raise GoalDesignError("milestone.stepsは配列にしてください")
        for step in steps:
            if not isinstance(step, dict):
                raise GoalDesignError("stepの形式が不正です")
            _reject_unknown(step, STEP_FIELDS, "step")
            if not isinstance(step.get("title"), str) or not step["title"].strip():
                raise GoalDesignError("step.titleが不正です")
    return payload


def receive_proposal(root: Path, design_id: str, payload: Any) -> dict[str, Any]:
    value = load_design(root, design_id)
    if value["status"] == "applied":
        raise GoalDesignError("適用済みの目標設計は更新できません")
    proposal = validate_proposal(payload)
    value["proposal"] = proposal
    value["status"] = "proposed"
    value["updated_at"] = now_iso()
    value["revision"] = int(value.get("revision", 1)) + 1
    atomic_write_json_data(design_path(root, design_id), value)
    return value


def render_prompt(value: dict[str, Any]) -> str:
    return (
        "daily-review goal design\n"
        f"design_id: {value['id']}\n"
        f"原文: {value['raw_goal']}\n"
        f"回答: {json.dumps(value.get('answers', []), ensure_ascii=False)}\n\n"
        "不明点を推測せず、goalとmilestonesのJSON候補を作成してください。\n"
        "各milestoneにstepsを含め、stepに実行可能なminimumを設定してください。\n"
        "未知のフィールドは追加しないでください。"
    )


def apply_design(root: Path, design_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    session = load_design(root, design_id)
    if session["status"] == "applied":
        raise GoalDesignError("この目標設計は適用済みです")
    if session["status"] != "proposed" or not isinstance(session.get("proposal"), dict):
        raise GoalDesignError("適用できるproposalがありません")
    proposal = validate_proposal(session["proposal"])
    goal_spec = proposal["goal"]
    goal = new_goal(
        title=goal_spec["title"],
        level=goal_spec["level"],
        category=goal_spec.get("category"),
        description=goal_spec.get("description"),
        start_date=goal_spec.get("start_date"),
        due_date=goal_spec.get("due_date"),
        qualitative=goal_spec.get("qualitative") or [],
        metrics=goal_spec.get("metrics") or [],
    )
    for milestone_spec in proposal.get("milestones", []):
        milestone = new_milestone(
            goal,
            title=milestone_spec["title"],
            description=milestone_spec.get("description"),
            start_date=milestone_spec.get("start_date"),
            due_date=milestone_spec.get("due_date"),
            qualitative=milestone_spec.get("qualitative") or [],
        )
        for index, step_spec in enumerate(milestone_spec.get("steps", []), start=1):
            milestone["steps"].append(new_step(
                title=step_spec["title"],
                description=step_spec.get("description"),
                start_date=step_spec.get("start_date"),
                due_date=step_spec.get("due_date"),
                minimum=step_spec.get("minimum"),
                order=index,
            ))
        goal["milestones"].append(milestone)
    goals = load_goals(root) + [goal]
    validate_goal(goal, goals)
    updated = copy.deepcopy(session)
    updated["status"] = "applied"
    updated["applied_goal_id"] = goal["id"]
    updated["applied_at"] = updated["updated_at"] = now_iso()
    updated["revision"] = int(updated.get("revision", 1)) + 1
    atomic_write_json_data_many([
        (goal_path(root, goal["id"]), goal),
        (design_path(root, design_id), updated),
    ])
    return goal, updated
