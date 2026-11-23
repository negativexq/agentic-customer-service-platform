"""Read and write models for the agent studio: knowledge, guidance, procedures and actions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.agent_config.schemas import ActionMode, AgentConfig


class KnowledgeDocumentView(BaseModel):
    document_id: str
    title: str
    category: str
    origin: Literal["built_in", "console"]
    edited_in_console: bool
    archived: bool
    section_count: int
    chunk_count: int
    word_count: int
    updated_at: datetime | None
    updated_by: str | None = None
    runs_citing: int
    last_cited_at: datetime | None


class KnowledgeChunkView(BaseModel):
    chunk_id: str
    content: str
    runs_citing: int


class KnowledgeSectionView(BaseModel):
    section: str
    chunks: list[KnowledgeChunkView]


class KnowledgeDocumentDetail(BaseModel):
    document_id: str
    title: str
    category: str
    origin: Literal["built_in", "console"]
    archived: bool
    content: str
    sections: list[KnowledgeSectionView]


class KnowledgeArticleInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50_000)


class KnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class KnowledgeSearchHit(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    section: str
    score: float
    content: str


class KnowledgeSearchResult(BaseModel):
    query: str
    index: str
    hits: list[KnowledgeSearchHit]


class KnowledgeIndexStatus(BaseModel):
    backend: str
    embedding_provider: str
    publish_allowed: bool
    publish_blocked_reason: str | None = None
    pending_changes: bool
    active_snapshot: str | None = None
    active_chunk_count: int | None = None
    corpus_chunk_count: int


class KnowledgePublishResult(BaseModel):
    snapshot: str
    chunk_count: int
    action: str


class ConfigVersionView(BaseModel):
    version: int
    status: str
    note: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    published_by: str | None
    published_at: datetime | None


class AgentConfigState(BaseModel):
    published: AgentConfig
    published_version: int | None
    draft: AgentConfig | None
    draft_version: int | None
    versions: list[ConfigVersionView]


class DraftInput(BaseModel):
    config: AgentConfig
    note: str = Field(default="", max_length=500)


class PublishInput(BaseModel):
    note: str = Field(default="", max_length=500)


class ActionUsage(BaseModel):
    calls: int = 0
    succeeded: int = 0
    failed: int = 0
    awaiting_confirmation: int = 0
    denied: int = 0
    last_used_at: datetime | None = None


class ActionInputField(BaseModel):
    name: str
    type: str
    required: bool
    description: str | None = None


class ActionView(BaseModel):
    name: str
    description: str
    operation: Literal["read", "write"]
    risk_level: int
    risk_label: str
    default_policy: str
    allowed_modes: list[ActionMode]
    locked: bool
    published_mode: ActionMode
    draft_mode: ActionMode | None
    safeguards: list[str]
    inputs: list[ActionInputField]
    procedures: list[str]
    usage: ActionUsage


class ProcedureStep(BaseModel):
    kind: Literal[
        "understand",
        "collect",
        "lookup",
        "knowledge",
        "policy",
        "confirm",
        "act",
        "respond",
        "handoff",
    ]
    title: str
    detail: str
    action: str | None = None
    operator_note: str | None = None


class ProcedureView(BaseModel):
    intent: str
    name: str
    summary: str
    request_type: str
    steps: list[ProcedureStep]
    actions: list[str]
    knowledge: list[str]
    guidance_count: int
    usage_runs: int
    handoff_runs: int
    clarification_runs: int
    failed_runs: int
