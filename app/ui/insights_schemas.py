"""Improve-loop read models: performance, gaps, quality monitors, errors and simulations."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PerformanceSummary(BaseModel):
    window_days: int
    conversations: int
    ai_resolved: int
    handed_off: int
    ai_resolution_rate: float | None
    handoff_rate: float | None
    csat_average: float | None
    csat_responses: int
    csat_ai_average: float | None
    csat_human_average: float | None
    ai_runs: int
    failed_runs: int


class DailyPoint(BaseModel):
    day: date
    conversations: int
    ai_resolved: int
    handed_off: int
    csat_average: float | None


class TopicRow(BaseModel):
    intent: str
    runs: int
    conversations: int
    handoffs: int
    clarifications: int
    failures: int
    handoff_rate: float | None
    csat_average: float | None


class FlowRow(BaseModel):
    """Where conversations of one request type ended up."""

    request_type: str
    conversations: int
    ai_resolved: int
    routed: int
    handled_by_team: int


class PerformanceView(BaseModel):
    summary: PerformanceSummary
    daily: list[DailyPoint]
    topics: list[TopicRow]
    flows: list[FlowRow] = Field(default_factory=list)


GapKind = Literal["content", "clarification", "action", "handoff"]


class GapExample(BaseModel):
    question: str
    conversation_pk: int | None
    run_id: str | None
    at: datetime


class GapView(BaseModel):
    kind: GapKind
    key: str
    title: str
    description: str
    intent: str | None
    count: int
    fix: Literal["knowledge", "guidance", "actions", "procedures"]
    examples: list[GapExample]


class ErrorRow(BaseModel):
    run_id: str
    conversation_id: str
    customer_id: int
    at: datetime
    intent: str
    status: str
    category: str
    failed_tools: list[str]
    degraded_components: list[str]
    recovery_action: str | None


class ErrorCategory(BaseModel):
    category: str
    count: int
    last_seen: datetime


class ErrorLogView(BaseModel):
    window_days: int
    total_runs: int
    failed_runs: int
    categories: list[ErrorCategory]
    daily: list[dict[str, Any]]
    errors: list[ErrorRow]


MonitorSender = Literal["customer", "ai", "operator", "any"]
ReviewStatus = Literal["open", "reviewed", "dismissed", "coached"]


class MonitorView(BaseModel):
    key: str
    name: str
    description: str
    severity: Literal["low", "medium", "high"]
    built_in: bool
    enabled: bool
    sender_type: MonitorSender | None = None
    phrases: list[str] = Field(default_factory=list)
    open_flags: int
    total_flags: int


class FlagView(BaseModel):
    monitor_key: str
    monitor_name: str
    severity: str
    conversation_pk: int
    conversation_id: str
    customer_id: int
    customer_name: str
    detail: str
    excerpt: str | None
    at: datetime
    status: ReviewStatus
    note: str
    run_id: str | None
    intent: str | None


class QualityView(BaseModel):
    window_days: int
    conversations: int = 0
    monitors: list[MonitorView]
    flags: list[FlagView]


class MonitorInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    sender_type: MonitorSender = "customer"
    phrases: list[str] = Field(min_length=1, max_length=30)
    severity: Literal["low", "medium", "high"] = "medium"
    enabled: bool = True


class MonitorPreviewInput(BaseModel):
    sender_type: MonitorSender = "customer"
    phrases: list[str] = Field(min_length=1, max_length=30)
    days: int = Field(default=30, ge=1, le=90)


class MonitorPreviewMatch(BaseModel):
    conversation_pk: int
    conversation_id: str
    customer_name: str
    detail: str
    excerpt: str | None
    at: datetime


class MonitorPreview(BaseModel):
    window_days: int
    conversations: int
    matched: int
    examples: list[MonitorPreviewMatch]


class ReviewInput(BaseModel):
    monitor_key: str = Field(min_length=1, max_length=80)
    conversation_pk: int
    status: ReviewStatus
    note: str = Field(default="", max_length=500)


class ScenarioView(BaseModel):
    id: str
    category: str
    customer_id: int
    turns: list[str]
    expected_intent: str | None
    completion: str
    expected_tools: list[str]
    forbidden_tools: list[str]
    critical_safety: bool


class SimulationInput(BaseModel):
    scenario_ids: list[str] = Field(min_length=1, max_length=200)
    against: Literal["published", "draft"] = "published"
    label: str = Field(default="", max_length=120)


class SimulationTurn(BaseModel):
    user: str
    assistant: str
    intent: str | None = None
    tool: str | None = None
    status: str | None = None


class SimulationResultView(BaseModel):
    scenario_id: str
    category: str
    passed: bool
    critical_safety: bool = False
    failure_reasons: list[str]
    selected_tools: list[str]
    executed_tools: list[str]
    latency_ms: float
    transcript: list[SimulationTurn]


class SimulationRunView(BaseModel):
    id: int
    label: str
    against: str
    config_version: int | None
    started_by: str
    started_at: datetime
    finished_at: datetime | None
    scenario_count: int
    passed_count: int
    results: list[SimulationResultView] | None = None
