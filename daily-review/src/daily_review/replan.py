"""Reviewable replan proposals and transactional application."""

from __future__ import annotations

import copy
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from .date_utils import parse_date
from .evaluation import load_monthly_evaluation, load_weekly_evaluation
from .goals import (
    find_milestone,
    find_step,
    goal_path,
    goals_backup_dir,
    load_goal,
    load_goals,
    milestones_of,
    new_milestone,
    validate_goal,
)
from .models import now_iso
from .storage import (
    atomic_write_json_data,
    atomic_write_json_data_many,
    priorities_path,
    read_json_file,
)


REPLAN_STATUSES = ("draft", "applied", "cancelled")
PROPOSAL_TYPES = (
    "reduce_scope",
    "extend_deadline",
    "split_milestone",
    "move_step",
    "pause_goal",
    "resume_goal",
    "change_priority",
    "reduce_daily_load",
    "change_minimum",
    "remove_blocker",
    "add_buffer",
    "cancel_step",
    "reorder_milestones",
    "review_goal_definition",
)
CONFIDENCE_VALUES = ("low", "medium", "high")


class ReplanError(ValueError):
    pass


def replans_dir(root: Path) -> Path:
    return root / "data" / "replans"


def replans_backup_dir(root: Path) -> Path:
    return root / "data" / "backups" / "replans"


def transactions_dir(root: Path) -> Path:
    return root / "data" / "transactions"


def replan_path(root: Path, replan_id: str) -> Path:
    if (
        not isinstance(replan_id, str)
        or not replan_id.startswith("replan-")
        or len(replan_id) != 15
    ):
        raise ReplanError("replan IDの形式が不正です")
    return replans_dir(root) / f"{replan_id}.json"


def _proposal(
    kind: str,
    *,
    title: str,
    reason: str,
    evidence: list[Any],
    goal_id: str | None = None,
    milestone_id: str | None = None,
    step_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    risk: str = "変更前に内容を確認してください",
) -> dict[str, Any]:
    return {
        "id": f"proposal-{uuid.uuid4().hex[:8]}",
        "type": kind,
        "goal_id": goal_id,
        "milestone_id": milestone_id,
        "step_id": step_id,
        "title": title,
        "reason": reason,
        "evidence": evidence,
        "before": before or {},
        "after": after or {},
        "risk": risk,
        "confidence": "medium",
    }


def _deadline_after(value: str | None, days: int = 7) -> str | None:
    return (parse_date(value) + timedelta(days=days)).isoformat() if value else None


def generate_replan(
    root: Path,
    *,
    week: str | None = None,
    month: str | None = None,
    goal_id: str | None = None,
) -> dict[str, Any]:
    if sum(value is not None for value in (week, month, goal_id)) != 1:
        raise ReplanError("--week、--month、--goal のどれか1つを指定してください")
    if week:
        evaluation = load_weekly_evaluation(root, week)
        source_type, source_id = "weekly_evaluation", None
        if evaluation:
            source_id = f"{evaluation['week_start']}_{evaluation['week_end']}"
    elif month:
        evaluation = load_monthly_evaluation(root, month)
        source_type, source_id = "monthly_evaluation", month
    else:
        goal = load_goal(root, goal_id or "")
        evaluation = {
            "diagnostics": [],
            "recommendations": [],
            "goal_evaluations": [
                {"goal_id": goal["id"], "status": "on_track", "evidence": []}
            ],
        }
        source_type, source_id = "goal", goal["id"]
    if not evaluation:
        raise ReplanError("保存済み評価が見つかりません")
    if (
        source_type in {"weekly_evaluation", "monthly_evaluation"}
        and evaluation.get("status") != "approved"
    ):
        raise ReplanError(
            "評価が未承認です。goal evaluate applyで承認してからreplanを作成してください"
        )
    proposals: list[dict[str, Any]] = []
    goals = {goal["id"]: goal for goal in load_goals(root)}
    for diagnostic in evaluation.get("diagnostics") or []:
        code, target_id = diagnostic.get("code"), diagnostic.get("goal_id")
        evidence = list(diagnostic.get("evidence") or [])
        target = goals.get(target_id) if target_id else None
        if code in {"overloaded", "low_completion_rate", "too_many_main_items"}:
            proposals.append(
                _proposal(
                    "reduce_daily_load",
                    title="1日のMain上限を2件へ減らす",
                    reason=diagnostic["message"],
                    evidence=evidence,
                    before={"main_limit": 3},
                    after={"main_limit": 2},
                    risk="1日あたりの進捗量が下がる可能性",
                )
            )
        elif code == "minimum_not_working" and target_id:
            proposals.append(
                _proposal(
                    "review_goal_definition",
                    title="最低ラインを見直す",
                    reason=diagnostic["message"],
                    evidence=evidence,
                    goal_id=target_id,
                    after={"review_required": True},
                )
            )
        elif code == "goal_inactive" and target:
            proposals.append(
                _proposal(
                    "pause_goal",
                    title=f"{target['title']}を一時停止候補にする",
                    reason=diagnostic["message"],
                    evidence=evidence,
                    goal_id=target_id,
                    before={"status": target.get("status")},
                    after={"status": "paused"},
                    risk="目標の進行が止まります",
                )
            )
        elif code in {"goal_delayed", "deadline_risk"} and target:
            after = _deadline_after(target.get("due_date"))
            if after:
                proposals.append(
                    _proposal(
                        "extend_deadline",
                        title=f"{target['title']}の期限を7日延長する",
                        reason=diagnostic["message"],
                        evidence=evidence,
                        goal_id=target_id,
                        before={"due_date": target.get("due_date")},
                        after={"due_date": after},
                        risk="後続目標の開始が遅れる可能性",
                    )
                )
            else:
                proposals.append(
                    _proposal(
                        "review_goal_definition",
                        title=f"{target['title']}の期限を設定する",
                        reason=diagnostic["message"],
                        evidence=evidence,
                        goal_id=target_id,
                        after={"review_required": True},
                    )
                )
        elif code == "dependency_blocked" and target:
            blocked = next(
                (
                    (milestone, step)
                    for milestone in milestones_of(target)
                    for step in milestone.get("steps") or []
                    if step.get("status") == "blocked"
                ),
                None,
            )
            if blocked:
                milestone, step = blocked
                proposals.append(
                    _proposal(
                        "remove_blocker",
                        title=f"{step['title']}のblockerを確認してtodoへ戻す",
                        reason=diagnostic["message"],
                        evidence=evidence,
                        goal_id=target_id,
                        milestone_id=milestone["id"],
                        step_id=step["id"],
                        before={"status": "blocked"},
                        after={"status": "todo"},
                        risk="blockerが未解消なら再び停止する可能性",
                    )
                )
    if not proposals:
        fallback_id = goal_id or next(
            (
                item.get("goal_id")
                for item in evaluation.get("goal_evaluations") or []
                if item.get("goal_id") in goals
            ),
            None,
        )
        target = goals.get(fallback_id or "")
        if not target:
            raise ReplanError("再計画対象の目標がありません")
        proposals.append(
            _proposal(
                "review_goal_definition",
                title=f"{target['title'] if target else '目標'}の定義と次アクションを確認する",
                reason="自動変更を決めるだけの数値根拠が不足しています",
                evidence=["予測不能"],
                goal_id=fallback_id,
                after={"review_required": True},
                risk="確認フラグだけを追加し、期限やstatusは変更しません",
            )
        )
    timestamp = now_iso()
    replan_id = f"replan-{uuid.uuid4().hex[:8]}"
    return {
        "id": replan_id,
        "source_type": source_type,
        "source_id": source_id,
        "status": "draft",
        "proposals": proposals,
        "created_at": timestamp,
        "updated_at": timestamp,
        "approved_proposal_ids": [],
        "applied_at": None,
        "revision": 1,
    }


def validate_replan(value: Any) -> None:
    if not isinstance(value, dict) or value.get("status") not in REPLAN_STATUSES:
        raise ReplanError("replan JSONまたはstatusが不正です")
    if not isinstance(value.get("id"), str) or not value["id"].startswith("replan-"):
        raise ReplanError("replan IDが不正です")
    proposals = value.get("proposals")
    if not isinstance(proposals, list):
        raise ReplanError("proposalsが不正です")
    ids = set()
    for item in proposals:
        if (
            not isinstance(item, dict)
            or item.get("type") not in PROPOSAL_TYPES
            or item.get("confidence") not in CONFIDENCE_VALUES
        ):
            raise ReplanError("proposalの形式が不正です")
        if not isinstance(item.get("id"), str) or item["id"] in ids:
            raise ReplanError("proposal IDが不正または重複しています")
        ids.add(item["id"])
        if not isinstance(item.get("evidence"), list) or not isinstance(
            item.get("after"), dict
        ):
            raise ReplanError("proposal evidenceまたはafterが不正です")
    approved = value.get("approved_proposal_ids") or []
    if not isinstance(approved, list) or not set(approved) <= ids:
        raise ReplanError("承認対象proposalが不正です")
    if value["status"] == "applied" and not isinstance(value.get("applied_at"), str):
        raise ReplanError("適用済みreplanにapplied_atがありません")


def save_replan(root: Path, value: dict[str, Any]) -> Path:
    validate_replan(value)
    path = replan_path(root, value["id"])
    if path.exists():
        raise ReplanError("同じreplan IDが既に存在します")
    atomic_write_json_data(path, value)
    return path


def load_replan(root: Path, replan_id: str) -> dict[str, Any]:
    path = replan_path(root, replan_id)
    if not path.exists():
        raise ReplanError("replanが見つかりません")
    value = read_json_file(path)
    validate_replan(value)
    return value


def list_replans(root: Path) -> list[dict[str, Any]]:
    if not replans_dir(root).is_dir():
        return []
    return [
        load_replan(root, path.stem)
        for path in sorted(replans_dir(root).glob("replan-*.json"))
    ]


def _backup_replan(root: Path, value: dict[str, Any]) -> None:
    path = replan_path(root, value["id"])
    if path.exists():
        target = (
            replans_backup_dir(root)
            / f"{path.stem}_{now_iso().replace(':', '').replace('+', '_')}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def edit_replan(
    root: Path,
    replan_id: str,
    *,
    remove: str | None = None,
    approve: str | None = None,
    setting: str | None = None,
) -> dict[str, Any]:
    if sum(value is not None for value in (remove, approve, setting)) != 1:
        raise ReplanError("--remove、--approve、--set のどれか1つを指定してください")
    value = load_replan(root, replan_id)
    if value["status"] != "draft":
        raise ReplanError("draft以外のreplanは編集できません")
    if remove:
        remaining = [item for item in value["proposals"] if item["id"] != remove]
        if len(remaining) == len(value["proposals"]):
            raise ReplanError("削除対象proposalが見つかりません")
        value["proposals"] = remaining
        value["approved_proposal_ids"] = [
            item for item in value["approved_proposal_ids"] if item != remove
        ]
    elif approve:
        if approve not in {item["id"] for item in value["proposals"]}:
            raise ReplanError("承認対象proposalが見つかりません")
        if approve not in value["approved_proposal_ids"]:
            value["approved_proposal_ids"].append(approve)
    else:
        if "=" not in (setting or ""):
            raise ReplanError("--setはproposal-id.after.field=value形式にしてください")
        path_text, raw = (setting or "").split("=", 1)
        parts = path_text.split(".")
        if (
            len(parts) != 3
            or parts[1] != "after"
            or parts[2]
            not in {
                "due_date",
                "status",
                "minimum",
                "position",
                "main_limit",
                "title",
                "review_required",
                "target_milestone_id",
            }
        ):
            raise ReplanError("変更できないフィールドです")
        proposal = next(
            (item for item in value["proposals"] if item["id"] == parts[0]), None
        )
        if not proposal:
            raise ReplanError("編集対象proposalが見つかりません")
        field = parts[2]
        allowed_by_type = {
            "extend_deadline": {"due_date"},
            "split_milestone": {"title", "due_date"},
            "move_step": {"target_milestone_id"},
            "pause_goal": {"status"},
            "resume_goal": {"status"},
            "change_priority": {"position"},
            "reduce_daily_load": {"main_limit"},
            "change_minimum": {"minimum"},
            "remove_blocker": {"status"},
            "cancel_step": {"status"},
            "reorder_milestones": {"position"},
            "review_goal_definition": {"review_required"},
        }
        if field not in allowed_by_type.get(proposal["type"], set()):
            raise ReplanError(f"{proposal['type']}ではafter.{field}を変更できません")
        if field == "due_date":
            parse_date(raw)
            parsed: Any = raw
        elif field in {"position", "main_limit"}:
            try:
                parsed = int(raw)
            except ValueError as exc:
                raise ReplanError(f"{field}は整数にしてください") from exc
            if parsed < 1:
                raise ReplanError(f"{field}は1以上にしてください")
        elif field == "review_required":
            parsed = raw.lower() == "true"
        else:
            parsed = raw
        if (
            field in {"minimum", "title", "status", "target_milestone_id"}
            and not str(parsed).strip()
        ):
            raise ReplanError(f"{field}を空にはできません")
        fixed_statuses = {
            "pause_goal": "paused",
            "resume_goal": "active",
            "remove_blocker": "todo",
            "cancel_step": "cancelled",
        }
        if field == "status" and parsed != fixed_statuses.get(proposal["type"]):
            raise ReplanError(
                f"{proposal['type']}のstatusは{fixed_statuses[proposal['type']]}だけ指定できます"
            )
        proposal["after"][field] = parsed
    _backup_replan(root, value)
    value["updated_at"] = now_iso()
    value["revision"] = int(value.get("revision", 1)) + 1
    validate_replan(value)
    atomic_write_json_data(replan_path(root, replan_id), value)
    return value


def cancel_replan(root: Path, replan_id: str) -> dict[str, Any]:
    value = load_replan(root, replan_id)
    if value["status"] == "applied":
        raise ReplanError("適用済みreplanはcancelできません")
    _backup_replan(root, value)
    value["status"] = "cancelled"
    value["updated_at"] = now_iso()
    value["revision"] = int(value.get("revision", 1)) + 1
    atomic_write_json_data(replan_path(root, replan_id), value)
    return value


def _normalize(items: list[dict[str, Any]]) -> None:
    for index, item in enumerate(items, start=1):
        item["order"] = index


def _apply_to_payload(goal: dict[str, Any], proposal: dict[str, Any]) -> list[str]:
    kind, after = proposal["type"], proposal["after"]
    milestone = (
        find_milestone(goal, proposal["milestone_id"])
        if proposal.get("milestone_id")
        else None
    )
    step = (
        find_step(goal, proposal["milestone_id"], proposal["step_id"])[1]
        if proposal.get("step_id")
        else None
    )
    changed: list[str] = []
    touched_items: list[dict[str, Any]] = []
    if kind in {"pause_goal", "resume_goal"}:
        goal["status"] = after["status"]
        changed.append("status")
    elif kind == "extend_deadline":
        target = step or milestone or goal
        target["due_date"] = after["due_date"]
        changed.append("due_date")
        if target is not goal:
            touched_items.append(target)
    elif kind in {"cancel_step", "remove_blocker"}:
        if not step:
            raise ReplanError(f"{kind}にはstep_idが必要です")
        step["status"] = after["status"]
        step["completed_at"] = None
        changed.append("status")
        touched_items.append(step)
    elif kind == "change_minimum":
        if not step:
            raise ReplanError("change_minimumにはstep_idが必要です")
        step["minimum"] = after["minimum"]
        changed.append("minimum")
        touched_items.append(step)
    elif kind == "reorder_milestones":
        if not milestone:
            raise ReplanError("reorder_milestonesにはmilestone_idが必要です")
        items = milestones_of(goal)
        items.remove(milestone)
        items.insert(min(int(after["position"]) - 1, len(items)), milestone)
        _normalize(items)
        changed.append("order")
        touched_items.append(milestone)
    elif kind == "move_step":
        if not milestone or not step:
            raise ReplanError("move_stepにはmilestone_idとstep_idが必要です")
        destination = find_milestone(goal, after["target_milestone_id"])
        milestone["steps"].remove(step)
        _normalize(milestone["steps"])
        destination.setdefault("steps", []).append(step)
        _normalize(destination["steps"])
        changed.append("milestone_id")
        touched_items.extend((milestone, destination, step))
    elif kind == "split_milestone":
        if not milestone:
            raise ReplanError("split_milestoneにはmilestone_idが必要です")
        created = new_milestone(
            goal,
            title=after.get("title") or f"{milestone['title']}（分割）",
            due_date=after.get("due_date"),
        )
        milestones_of(goal).append(created)
        _normalize(milestones_of(goal))
        changed.append("milestones")
    elif kind == "reduce_scope":
        if step:
            step["status"] = "cancelled"
            step["completed_at"] = None
            changed.append("status")
            touched_items.append(step)
        else:
            goal["scope_review"] = after.get("scope") or "reduced"
            changed.append("scope_review")
    elif kind == "review_goal_definition":
        goal["review_required"] = bool(after.get("review_required", True))
        changed.append("review_required")
    else:
        raise ReplanError(f"このproposalはgoal JSONへ直接適用できません: {kind}")
    timestamp = now_iso()
    seen: set[int] = set()
    for item in touched_items:
        if id(item) in seen:
            continue
        seen.add(id(item))
        item["revision"] = int(item.get("revision", 1)) + 1
        item["updated_at"] = timestamp
    return changed


def _validate_apply_proposal(proposal: dict[str, Any]) -> None:
    kind, after = proposal["type"], proposal["after"]
    required_after = {
        "pause_goal": {"status"},
        "resume_goal": {"status"},
        "extend_deadline": {"due_date"},
        "cancel_step": {"status"},
        "remove_blocker": {"status"},
        "change_minimum": {"minimum"},
        "reorder_milestones": {"position"},
        "move_step": {"target_milestone_id"},
        "reduce_daily_load": {"main_limit"},
        "change_priority": {"category", "position"},
    }
    missing = required_after.get(kind, set()) - set(after)
    if missing:
        raise ReplanError(
            f"{kind}のafterに必須フィールドがありません: {', '.join(sorted(missing))}"
        )
    global_types = {"reduce_daily_load", "add_buffer", "change_priority"}
    if kind not in global_types and not isinstance(proposal.get("goal_id"), str):
        raise ReplanError(f"{kind}にはgoal_idが必要です")
    if kind in {
        "change_minimum",
        "cancel_step",
        "remove_blocker",
        "move_step",
    } and not isinstance(proposal.get("step_id"), str):
        raise ReplanError(f"{kind}にはstep_idが必要です")
    if kind in {
        "change_minimum",
        "cancel_step",
        "remove_blocker",
        "move_step",
        "reorder_milestones",
        "split_milestone",
    } and not isinstance(proposal.get("milestone_id"), str):
        raise ReplanError(f"{kind}にはmilestone_idが必要です")
    if kind == "extend_deadline":
        parse_date(after["due_date"])
    if kind == "reduce_daily_load" and (
        not isinstance(after["main_limit"], int)
        or isinstance(after["main_limit"], bool)
        or not 1 <= after["main_limit"] <= 3
    ):
        raise ReplanError("main_limitは1〜3にしてください")


def apply_replan(root: Path, replan_id: str) -> dict[str, Any]:
    value = load_replan(root, replan_id)
    if value["status"] != "draft":
        raise ReplanError("draftのreplanだけ適用できます")
    selected = [
        item
        for item in value["proposals"]
        if item["id"] in value["approved_proposal_ids"]
    ]
    if not selected:
        raise ReplanError("承認済みproposalがありません")
    for proposal in selected:
        _validate_apply_proposal(proposal)
    goals = load_goals(root)
    mapped = {item["id"]: copy.deepcopy(item) for item in goals}
    priorities_file = priorities_path(root)
    priorities_payload = (
        read_json_file(priorities_file)
        if priorities_file.exists()
        else {"priorities": []}
    )
    planning_file = root / "config" / "planning.json"
    planning_payload = (
        read_json_file(planning_file)
        if planning_file.exists()
        else {"max_daily_main": 3, "buffer_fraction": 0.33}
    )
    touched: dict[str, list[tuple[str, list[str]]]] = {}
    config_changed = planning_changed = False
    for proposal in selected:
        kind = proposal["type"]
        if kind in {"reduce_daily_load", "add_buffer"}:
            if kind == "reduce_daily_load":
                planning_payload["max_daily_main"] = int(
                    proposal["after"]["main_limit"]
                )
            else:
                planning_payload["buffer_fraction"] = float(
                    proposal["after"].get("buffer_fraction", 0.34)
                )
            planning_changed = True
            continue
        if kind == "change_priority":
            category = proposal["after"].get("category")
            position = int(proposal["after"].get("position", 1))
            if not isinstance(category, str) or category not in priorities_payload.get(
                "priorities", []
            ):
                raise ReplanError("優先カテゴリが存在しません")
            priorities_payload["priorities"].remove(category)
            priorities_payload["priorities"].insert(position - 1, category)
            config_changed = True
            continue
        goal_id = proposal.get("goal_id")
        if goal_id not in mapped:
            raise ReplanError("proposalの対象goalが存在しません")
        changed = _apply_to_payload(mapped[goal_id], proposal)
        touched.setdefault(goal_id, []).append((proposal["id"], changed))
    staged_goals = list(mapped.values())
    timestamp = now_iso()
    for goal_id, changes in touched.items():
        goal = mapped[goal_id]
        goal["revision"] = int(goal.get("revision", 1)) + 1
        goal["updated_at"] = timestamp
        history = list(goal.get("history") or [])
        for proposal_id, fields in changes:
            history.append(
                {
                    "changed_at": timestamp,
                    "source": "replan",
                    "replan_id": replan_id,
                    "proposal_id": proposal_id,
                    "changed_fields": fields,
                }
            )
        goal["history"] = history[-100:]
    for goal in staged_goals:
        validate_goal(goal, staged_goals)
    transaction_id = f"transaction-{uuid.uuid4().hex[:12]}"
    updated_replan = copy.deepcopy(value)
    updated_replan["status"] = "applied"
    updated_replan["applied_at"] = timestamp
    updated_replan["updated_at"] = timestamp
    updated_replan["revision"] = int(value.get("revision", 1)) + 1
    updated_replan["transaction_id"] = transaction_id
    validate_replan(updated_replan)
    # All backups are completed before the first write.  A backup failure
    # therefore leaves every source file untouched.
    for goal_id in touched:
        source = goal_path(root, goal_id)
        target = (
            goals_backup_dir(root)
            / f"{goal_id}_{timestamp.replace(':', '').replace('+', '_')}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _backup_replan(root, value)
    if config_changed and priorities_file.exists():
        target = (
            replans_backup_dir(root)
            / f"priorities_{timestamp.replace(':', '').replace('+', '_')}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(priorities_file, target)
    if planning_changed and planning_file.exists():
        target = (
            replans_backup_dir(root)
            / f"planning_{timestamp.replace(':', '').replace('+', '_')}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(planning_file, target)
    writes = [(goal_path(root, goal_id), mapped[goal_id]) for goal_id in touched]
    if config_changed:
        writes.append((priorities_file, priorities_payload))
    if planning_changed:
        writes.append((planning_file, planning_payload))
    writes.append((replan_path(root, replan_id), updated_replan))
    transaction_path = transactions_dir(root) / f"{transaction_id}.json"
    transaction = {
        "id": transaction_id,
        "type": "replan_apply",
        "replan_id": replan_id,
        "status": "prepared",
        "created_at": timestamp,
        "updated_at": timestamp,
        "targets": [
            {
                "path": str(path.relative_to(root)),
                "existed": path.exists(),
                "before": read_json_file(path) if path.exists() else None,
            }
            for path, _ in writes
        ],
    }
    # The durable manifest is written before any source document.  Normal
    # exceptions are rolled back by the multi-file writer; a process crash
    # leaves a readable manifest for doctor and a later retry.
    atomic_write_json_data(transaction_path, transaction)
    try:
        atomic_write_json_data_many(writes)
        transaction["status"] = "committed"
        transaction["updated_at"] = now_iso()
        atomic_write_json_data(transaction_path, transaction)
    except Exception:
        restore_writes: list[tuple[Path, dict[str, Any]]] = []
        for target in transaction["targets"]:
            path = root / target["path"]
            if target["existed"]:
                restore_writes.append((path, target["before"]))
            elif path.exists():
                path.unlink()
        if restore_writes:
            atomic_write_json_data_many(restore_writes)
        transaction["status"] = "rolled_back"
        transaction["updated_at"] = now_iso()
        try:
            atomic_write_json_data(transaction_path, transaction)
        except OSError:
            pass
        raise
    return updated_replan
