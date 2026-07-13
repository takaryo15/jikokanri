from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class MainStatus(FlexibleModel):
    area: str
    status: str
    note: str | None = None


class StructuredReview(FlexibleModel):
    today_main: list[MainStatus] = Field(default_factory=list)
    minimum_line: dict[str, str] = Field(default_factory=dict)
    what_went_well: list[str] = Field(default_factory=list)
    breakdown_causes: list[str] = Field(default_factory=list)
    one_change_tomorrow: str | None = None


class ReviewInput(FlexibleModel):
    structured_review: StructuredReview | None = None
    diary: str | None = None

    @classmethod
    def normalize_payload(cls, payload: dict[str, Any]) -> "ReviewInput":
        if "structured_review" in payload or "diary" in payload:
            return cls.model_validate(payload)
        return cls.model_validate({"structured_review": payload})


class PlanTask(FlexibleModel):
    id: str | None = None
    area: str
    task: str
    priority: int = Field(ge=1)
    minimum_line: str

    @field_validator("minimum_line")
    @classmethod
    def minimum_line_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("最低ラインを空にはできません")
        return value


class Plan(FlexibleModel):
    status: Literal["pending_review", "approved"] | str
    target_date: str
    main: list[str] = Field(default_factory=list)
    tasks: list[PlanTask] = Field(default_factory=list)
    one_change_tomorrow: str
    approved_at: str | None = None


class ProposalInput(FlexibleModel):
    target_date: str
    main: list[str] = Field(default_factory=list)
    tasks: list[PlanTask] = Field(default_factory=list)
    one_change_tomorrow: str


class TaskResult(FlexibleModel):
    task_id: str
    status: Literal["completed", "partial", "minimum_only", "not_started", "skipped"]
    note: str | None = None
    minimum_line_achieved: StrictBool
    recorded_at: str | None = None


class TaskResultsInput(FlexibleModel):
    task_results: list[TaskResult] = Field(default_factory=list)


class DailyEntry(FlexibleModel):
    date: str
    raw_log: str | None = None
    diary: str | None = None
    structured_review: StructuredReview | None = None
    tomorrow_plan_proposal: Plan | None = None
    tomorrow_plan_final: Plan | None = None
    task_results: list[TaskResult] = Field(default_factory=list)
    created_at: str
    updated_at: str


def dump_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
