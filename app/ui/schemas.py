from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.compiler_contracts import CompilerField, CompilerReasonCode
from app.agent.schemas import AgentProposal, ProviderRunMetadata
from app.health import ComponentHealthStatus


class UIMessageView(BaseModel):
    role: str
    content_available: bool
    content: str | None = None


class DemoConversationMessage(BaseModel):
    """Customer-facing text for a recorded showcase fixture."""

    role: Literal["customer", "agent"]
    content: str
    timestamp: datetime | None = None
    state: str | None = None
    evidence_tags: list[str] = Field(default_factory=list)


class DemoMemoryEvidence(BaseModel):
    """Bounded synthetic memory metadata; unrestricted memory content is excluded."""

    category: str
    summary: str
    source: str
    authority: Literal["context_only"] = "context_only"
    purpose: Literal["context_enrichment"] = "context_enrichment"


class UIToolEvent(BaseModel):
    name: str
    risk_level: int | None = None
    status: str
    duration_ms: float
    result_fields: list[str] = Field(default_factory=list)


class UIPolicyEvent(BaseModel):
    event_id: str = ""
    request_id: str = ""
    conversation_id: str = ""
    action_id: str | None = None
    timestamp: datetime | None = None
    stage: str = "policy_evaluation"
    confirmation_status: str | None = None
    revalidation: bool = False
    execution_status: str | None = None
    actor_id: str
    actor_type: str
    roles: list[str] = Field(default_factory=list)
    effective_customer_id: int
    tool_name: str
    risk_level: int
    outcome: str
    reason_codes: list[str] = Field(default_factory=list)


class UIRagDocument(BaseModel):
    citation_id: str
    title: str
    section: str
    source: str
    score: float
    document_version: str | None = None
    chunk_id: str | None = None
    grounding_status: str | None = None
    retrieved_at: datetime | None = None
    citation_preview: str | None = None


class UIRetrievalMetadata(BaseModel):
    backend: str = "unknown"
    embedding_provider: str = "unknown"
    reranker_enabled: bool = False
    retrieval_count: int = 0
    latency_seconds: float = 0.0
    fallback_status: str = "none"
    hybrid: bool = False
    fusion_strategy: str = "none"
    dense_candidate_count: int = 0
    sparse_candidate_count: int = 0


class UIAnswerGrounding(BaseModel):
    """Bounded answer-synthesis evidence; claim text and hidden reasoning are excluded."""

    status: str = "not_recorded"
    sources_used: int = Field(default=0, ge=0)
    citation_count: int = Field(default=0, ge=0)
    citation_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(default=0, ge=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    accepted: bool = False


class UIMemoryUsage(BaseModel):
    item_count: int
    keys: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    retrieved: bool = False
    retrieved_count: int = 0
    items_used: int = 0
    context_usage: Literal["context_enrichment", "not_used"] = "not_used"
    purpose: Literal["context_enrichment", "not_used"] = "not_used"
    decision_influence: Literal["context_only", "decision_support", "not_used"] = "not_used"
    authority_influence: Literal["none", "blocked", "not_applicable"] = "not_applicable"


class UITraceStage(StrEnum):
    """Stable operator-facing stage identifiers for the runtime trace."""

    USER_REQUEST = "user_request"
    INTENT_DETECTION = "intent_detection"
    CONTEXT_RETRIEVAL = "context_retrieval"
    GROUNDING = "grounding"
    TARGET_VALIDATION = "target_validation"
    POLICY_EVALUATION = "policy_evaluation"
    CONFIRMATION = "confirmation"
    EXECUTION_AUTHORITY = "execution_authority"
    MEMORY_CONTEXT = "memory_context"
    ROUTING = "routing"
    RESPONSE = "response"
    INTERNAL = "internal"


class UIGroundingEvidence(BaseModel):
    status: str = "not_recorded"
    reference_type: str | None = None
    trusted_source: str | None = None


class UICompilerDecision(BaseModel):
    reason_code: CompilerReasonCode = CompilerReasonCode.NOT_RECORDED
    missing_fields: list[CompilerField] = Field(default_factory=list, max_length=10)
    clarification_fields: list[CompilerField] = Field(default_factory=list, max_length=10)
    status: str = "not_recorded"
    selected_tool: str | None = None
    requires_retrieval: bool = False
    reason: str | None = None


class UITargetValidation(BaseModel):
    status: str = "not_recorded"


class UIConfirmationLifecycle(BaseModel):
    status: str = "not_required"
    required: bool = False
    action_id: str | None = None
    risk_level: int | None = None


class UIWriteOutcome(BaseModel):
    status: str = "not_attempted"


class UIDecisionEvidence(BaseModel):
    """Bounded, privacy-safe evidence for one projected agent run."""

    decision: str = "not_recorded"
    reason: str | None = None
    validation_stage: str = "not_recorded"
    execution_status: str = "not_attempted"
    authority: str = "not_granted"
    grounding: UIGroundingEvidence = Field(default_factory=UIGroundingEvidence)
    compiler: UICompilerDecision = Field(default_factory=UICompilerDecision)
    target_validation: UITargetValidation = Field(default_factory=UITargetValidation)
    confirmation: UIConfirmationLifecycle = Field(default_factory=UIConfirmationLifecycle)
    write_outcome: UIWriteOutcome = Field(default_factory=UIWriteOutcome)


class UITraceEvent(BaseModel):
    name: str
    event_key: str | None = None
    stage: UITraceStage = UITraceStage.INTERNAL
    status: str
    duration_ms: float
    timestamp: datetime
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class AgentRunView(BaseModel):
    run_id: str
    request_id: str
    conversation_id: str
    tenant_id: str = "default"
    action_id: str | None = None
    customer_id: int
    actor_id: str
    actor_type: str
    roles: list[str] = Field(default_factory=list)
    intent: str
    request_type: str
    operation_type: Literal["agent_request", "memory_summary", "idempotency_replay"] = (
        "agent_request"
    )
    status: str
    started_at: datetime
    duration_ms: float
    trace_id: str | None = None
    path: list[str] = Field(default_factory=list)
    failure_category: str | None = None
    degraded_components: list[str] = Field(default_factory=list)
    recovery_action: str | None = None
    memory: UIMemoryUsage
    tools: list[UIToolEvent] = Field(default_factory=list)
    policy: list[UIPolicyEvent] = Field(default_factory=list)
    rag_documents: list[UIRagDocument] = Field(default_factory=list)
    retrieval_metadata: UIRetrievalMetadata = Field(default_factory=UIRetrievalMetadata)
    answer_grounding: UIAnswerGrounding = Field(default_factory=UIAnswerGrounding)
    trace: list[UITraceEvent] = Field(default_factory=list)
    decision_reason: str | None = None
    security_signal: str | None = None
    evidence: UIDecisionEvidence = Field(default_factory=UIDecisionEvidence)
    execution_mode: str = "recorded_replay"
    provider: str = "recorded_evidence"
    model: str | None = None
    fallback_message: str | None = None
    proposal: AgentProposal | None = None
    provider_metadata: ProviderRunMetadata | None = None


class DemoScenarioView(BaseModel):
    """A non-executable, recorded scenario projection for public demonstrations."""

    scenario_id: str
    title: str
    purpose: str
    expected: str
    run: AgentRunView
    messages: list[DemoConversationMessage] = Field(default_factory=list)
    memory_evidence: list[DemoMemoryEvidence] = Field(default_factory=list)
    proposal_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ConversationView(BaseModel):
    conversation_id: str
    customer_id: int
    run_count: int
    runs: list[AgentRunView]
    messages: list[UIMessageView]


class MemoryView(BaseModel):
    id: int
    customer_id: int
    memory_type: str
    normalized_key: str
    source: str
    status: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class SystemComponentHealth(BaseModel):
    name: str
    status: ComponentHealthStatus
    detail: str


class SystemHealthView(BaseModel):
    status: Literal["ready", "not_ready"]
    components: list[SystemComponentHealth]


class RuntimeConfigView(BaseModel):
    """Safe server configuration projection; credential values are never returned."""

    provider: str
    model: str
    environment: str
    live_proposal_available: bool
