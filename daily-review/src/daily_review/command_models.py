"""Versioned JSON models for the local ChatGPT command API."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from .date_utils import parse_date


API_VERSION: Literal["1"] = "1"
MAX_COMMANDS = 20
MAX_RAW_INPUT = 20_000
MAX_ITEMS = 100
Text = Annotated[StrictStr, Field(min_length=1, max_length=2_000)]
TextItems = Annotated[list[Text], Field(max_length=MAX_ITEMS)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DailyReviewPayload(ApiModel):
    date: str | None = None
    done: TextItems = Field(default_factory=list)
    not_done: TextItems = Field(default_factory=list)
    causes: TextItems = Field(default_factory=list)
    tomorrow: TextItems = Field(default_factory=list)
    minimum: TextItems = Field(default_factory=list)
    journal: StrictStr | None = Field(default=None, max_length=10_000)
    unclassified: TextItems = Field(default_factory=list)
    replace: bool = False


class CreateTaskPayload(ApiModel):
    title: Text
    description: StrictStr = Field(default="", max_length=5_000)
    category: StrictStr = Field(default="", max_length=200)
    priority: Literal["high", "medium", "low"] = "medium"
    due_date: str | None = None
    is_main_candidate: bool = False
    minimum_action: StrictStr | None = Field(default=None, max_length=2_000)


class TaskSelector(ApiModel):
    task_id: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    title: StrictStr | None = Field(default=None, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def require_selector(self) -> "TaskSelector":
        if not self.task_id and not self.title:
            raise ValueError("task_idまたはtitleが必要です")
        return self


class CompleteTaskPayload(TaskSelector):
    completed_at: str | None = None


class RescheduleTaskPayload(TaskSelector):
    new_due_date: str
    reason: StrictStr | None = Field(default=None, max_length=2_000)


class UpdateTaskPayload(TaskSelector):
    new_title: StrictStr | None = Field(default=None, min_length=1, max_length=2_000)
    priority: Literal["high", "medium", "low"] | None = None
    category: StrictStr | None = Field(default=None, max_length=200)
    description: StrictStr | None = Field(default=None, max_length=5_000)

    @model_validator(mode="after")
    def require_change(self) -> "UpdateTaskPayload":
        if all(
            value is None
            for value in (
                self.new_title,
                self.priority,
                self.category,
                self.description,
            )
        ):
            raise ValueError("更新するフィールドがありません")
        return self


class GenerateInstructionPayload(ApiModel):
    target_date: str


class InstructionSelector(ApiModel):
    instruction_id: StrictStr = Field(min_length=1, max_length=200)


class ReviseInstructionPayload(InstructionSelector):
    main: TextItems = Field(default_factory=list)
    minimum: TextItems = Field(default_factory=list)
    optional: TextItems = Field(default_factory=list)


class GetInstructionPayload(ApiModel):
    target_date: str


class ListTasksPayload(ApiModel):
    status: (
        Literal[
            "pending", "completed", "partial", "minimum_only", "not_started", "skipped"
        ]
        | None
    ) = None
    priority: Literal["high", "medium", "low"] | None = None
    category: StrictStr | None = Field(default=None, max_length=200)
    due: Literal["today", "tomorrow", "overdue"] | None = None
    main: bool = False
    minimum: bool = False
    all: bool = False


class SchedulerAtPayload(ApiModel):
    at: str | None = None


class SchedulerHistoryPayload(ApiModel):
    job: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    status: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    date: str | None = None


class SchedulerRunJobPayload(SchedulerAtPayload):
    job: StrictStr = Field(min_length=1, max_length=200)
    force: bool = False


class FlowDatePayload(SchedulerAtPayload):
    date: str | None = None
    force: bool = False


class FlowMonthPayload(SchedulerAtPayload):
    month: str | None = None
    force: bool = False


class CreateDailyReviewCommand(ApiModel):
    type: Literal["create_daily_review"]
    payload: DailyReviewPayload


class CreateTaskCommand(ApiModel):
    type: Literal["create_task"]
    payload: CreateTaskPayload


class CompleteTaskCommand(ApiModel):
    type: Literal["complete_task"]
    payload: CompleteTaskPayload


class RescheduleTaskCommand(ApiModel):
    type: Literal["reschedule_task"]
    payload: RescheduleTaskPayload


class UpdateTaskCommand(ApiModel):
    type: Literal["update_task"]
    payload: UpdateTaskPayload


class GenerateInstructionCommand(ApiModel):
    type: Literal["generate_instruction"]
    payload: GenerateInstructionPayload


class ApproveInstructionCommand(ApiModel):
    type: Literal["approve_instruction"]
    payload: InstructionSelector


class ReviseInstructionCommand(ApiModel):
    type: Literal["revise_instruction"]
    payload: ReviseInstructionPayload


class GetInstructionCommand(ApiModel):
    type: Literal["get_instruction"]
    payload: GetInstructionPayload


class ListTasksCommand(ApiModel):
    type: Literal["list_tasks"]
    payload: ListTasksPayload = Field(default_factory=ListTasksPayload)


class SchedulerStatusCommand(ApiModel):
    type: Literal["scheduler_status"]
    payload: SchedulerAtPayload = Field(default_factory=SchedulerAtPayload)


class SchedulerDueCommand(ApiModel):
    type: Literal["scheduler_due"]
    payload: SchedulerAtPayload = Field(default_factory=SchedulerAtPayload)


class SchedulerRunDueCommand(ApiModel):
    type: Literal["scheduler_run_due"]
    payload: SchedulerAtPayload = Field(default_factory=SchedulerAtPayload)


class SchedulerRunJobCommand(ApiModel):
    type: Literal["scheduler_run_job"]
    payload: SchedulerRunJobPayload


class SchedulerHistoryCommand(ApiModel):
    type: Literal["scheduler_history"]
    payload: SchedulerHistoryPayload = Field(default_factory=SchedulerHistoryPayload)


class RunNightlyFlowCommand(ApiModel):
    type: Literal["run_nightly_flow"]
    payload: FlowDatePayload = Field(default_factory=FlowDatePayload)


class RunMorningFlowCommand(ApiModel):
    type: Literal["run_morning_flow"]
    payload: FlowDatePayload = Field(default_factory=FlowDatePayload)


class RunWeeklyFlowCommand(ApiModel):
    type: Literal["run_weekly_flow"]
    payload: FlowDatePayload = Field(default_factory=FlowDatePayload)


class RunMonthlyFlowCommand(ApiModel):
    type: Literal["run_monthly_flow"]
    payload: FlowMonthPayload = Field(default_factory=FlowMonthPayload)


Command = Annotated[
    CreateDailyReviewCommand
    | CreateTaskCommand
    | CompleteTaskCommand
    | RescheduleTaskCommand
    | UpdateTaskCommand
    | GenerateInstructionCommand
    | ApproveInstructionCommand
    | ReviseInstructionCommand
    | GetInstructionCommand
    | ListTasksCommand
    | SchedulerStatusCommand
    | SchedulerDueCommand
    | SchedulerRunDueCommand
    | SchedulerRunJobCommand
    | SchedulerHistoryCommand
    | RunNightlyFlowCommand
    | RunMorningFlowCommand
    | RunWeeklyFlowCommand
    | RunMonthlyFlowCommand,
    Field(discriminator="type"),
]


class CommandRequest(ApiModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "version": "1",
                "request_id": "req_example_001",
                "idempotency_key": "review-2026-07-15",
                "mode": "preview",
                "timezone": "Asia/Tokyo",
                "effective_date": "2026-07-15",
                "source": "chatgpt",
                "commands": [
                    {
                        "type": "create_task",
                        "payload": {"title": "院試過去問", "priority": "high"},
                    }
                ],
            }
        },
    )
    version: Literal["1"] = API_VERSION
    request_id: StrictStr = Field(
        default_factory=lambda: f"req_{uuid.uuid4().hex[:16]}",
        min_length=1,
        max_length=200,
    )
    idempotency_key: StrictStr | None = Field(
        default=None, min_length=1, max_length=500
    )
    mode: Literal["preview", "commit"] = "preview"
    timezone: StrictStr = Field(default="Asia/Tokyo", min_length=1, max_length=100)
    effective_date: str
    source: StrictStr = Field(default="manual", min_length=1, max_length=100)
    raw_input: StrictStr | None = Field(default=None, max_length=MAX_RAW_INPUT)
    commands: list[Command] = Field(default_factory=list, max_length=MAX_COMMANDS)
    execution_policy: Literal["atomic", "best_effort"] = "atomic"
    confirmation_token: StrictStr | None = Field(default=None, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_structure(cls, value: Any) -> Any:
        """Bound nesting and reject characters unsafe for files and terminals."""

        def visit(item: Any, depth: int = 0) -> None:
            if depth > 20:
                raise ValueError("JSONのネストは20階層以内にしてください")
            if isinstance(item, dict):
                for key, child in item.items():
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    visit(child, depth + 1)
            elif isinstance(item, str):
                if "\x00" in item:
                    raise ValueError("null byteは使用できません")
                if any(
                    ord(character) < 32 and character not in "\n\r\t"
                    for character in item
                ):
                    raise ValueError("制御文字は使用できません")
                if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
                    raise ValueError("不正なUnicode surrogateは使用できません")

        visit(value)
        return value

    @field_validator("effective_date")
    @classmethod
    def validate_effective_date(cls, value: str) -> str:
        parse_date(value)
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"未知のtimezoneです: {value}") from exc
        return value


class ApiIssue(ApiModel):
    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    recoverable: bool = True


class CommandResponse(ApiModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "version": "1",
                "request_id": "req_example_001",
                "status": "preview_ready",
                "mode": "preview",
                "summary": "1件の変更を確認してください",
                "confirmation_required": True,
                "confirmation_token": "confirm_xxx",
            }
        },
    )
    version: Literal["1"] = API_VERSION
    request_id: str
    status: str
    mode: Literal["preview", "commit"]
    idempotency_key: str | None = None
    summary: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[ApiIssue] = Field(default_factory=list)
    errors: list[ApiIssue] = Field(default_factory=list)
    confirmation_required: bool = False
    confirmation_token: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


COMMAND_MODELS = {
    "create_daily_review": CreateDailyReviewCommand,
    "create_task": CreateTaskCommand,
    "complete_task": CompleteTaskCommand,
    "reschedule_task": RescheduleTaskCommand,
    "update_task": UpdateTaskCommand,
    "generate_instruction": GenerateInstructionCommand,
    "approve_instruction": ApproveInstructionCommand,
    "revise_instruction": ReviseInstructionCommand,
    "get_instruction": GetInstructionCommand,
    "list_tasks": ListTasksCommand,
    "scheduler_status": SchedulerStatusCommand,
    "scheduler_due": SchedulerDueCommand,
    "scheduler_run_due": SchedulerRunDueCommand,
    "scheduler_run_job": SchedulerRunJobCommand,
    "scheduler_history": SchedulerHistoryCommand,
    "run_nightly_flow": RunNightlyFlowCommand,
    "run_morning_flow": RunMorningFlowCommand,
    "run_weekly_flow": RunWeeklyFlowCommand,
    "run_monthly_flow": RunMonthlyFlowCommand,
}


WRITE_COMMANDS = set(COMMAND_MODELS) - {
    "get_instruction",
    "list_tasks",
    "scheduler_status",
    "scheduler_due",
    "scheduler_history",
}
