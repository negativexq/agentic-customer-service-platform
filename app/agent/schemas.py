from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.memory.schemas import MemoryCandidate
from app.policies.models import PendingAction
from app.rag.schemas import Citation


class Intent(StrEnum):
    CUSTOMER_LOOKUP = "customer_lookup"
    ORDER_LOOKUP = "order_lookup"
    ORDER_LIST = "order_list"
    TICKET_LOOKUP = "ticket_lookup"
    TICKET_LIST = "ticket_list"
    TICKET_CREATE = "ticket_create"
    ORDER_CANCEL = "order_cancel"
    REFUND_REQUEST = "refund_request"
    HUMAN_ESCALATION = "human_escalation"
    CAPABILITY_QUESTION = "capability_question"
    REFUND_POLICY = "refund_policy"
    CANCELLATION_POLICY = "cancellation_policy"
    SHIPPING_POLICY = "shipping_policy"
    SUPPORT_FAQ = "support_faq"
    REFUND_ELIGIBILITY = "refund_eligibility"
    CANCELLATION_EXPLANATION = "cancellation_explanation"
    MEMORY_REMEMBER = "memory_remember"
    MEMORY_FORGET = "memory_forget"
    UNKNOWN = "unknown"


class AgentRequestType(StrEnum):
    INFORMATIONAL = "informational"
    READ_ACTION = "read_action"
    WRITE_ACTION = "write_action"
    ESCALATION = "escalation"
    UNCLEAR = "unclear"
    KNOWLEDGE_ONLY = "knowledge_only"
    ACTION_ONLY = "action_only"
    KNOWLEDGE_AND_ACTION = "knowledge_and_action"
    MEMORY_ACTION = "memory_action"


class AgentExecutionMode(StrEnum):
    """Explicit playground modes; neither mode grants model output authority."""

    RECORDED_REPLAY = "recorded_replay"
    LIVE_PROPOSAL = "live_proposal"


class ProposalValidationStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    REJECTED = "rejected"
    NOT_RECORDED = "not_recorded"


class AgentProposal(BaseModel):
    """Bounded proposal projection; raw prompts, reasoning, and tool arguments are excluded."""

    model_config = ConfigDict(extra="forbid")

    intent: str
    suggested_action: str | None = None
    extracted_fields: dict[str, str | int | bool] = Field(default_factory=dict)
    evidence_references: list[str] = Field(default_factory=list)
    validation: ProposalValidationStatus = ProposalValidationStatus.NOT_RECORDED


class ProviderRunMetadata(BaseModel):
    """Safe provider metadata; token fields remain absent when the provider omits usage."""

    provider: str
    model: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class SemanticTarget(BaseModel):
    """A typed user-referent, never an executable tool argument bag."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["explicit_order", "latest_order", "explicit_ticket"]
    order_id: int | None = Field(default=None, gt=0)
    ticket_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_reference_shape(self) -> "SemanticTarget":
        if self.type == "explicit_order" and self.order_id is None:
            raise ValueError("explicit_order requires order_id")
        if self.type == "explicit_ticket" and self.ticket_id is None:
            raise ValueError("explicit_ticket requires ticket_id")
        if self.type == "latest_order" and (
            self.order_id is not None or self.ticket_id is not None
        ):
            raise ValueError("latest_order cannot include an identifier")
        if self.type == "explicit_order" and self.ticket_id is not None:
            raise ValueError("explicit_order cannot include ticket_id")
        if self.type == "explicit_ticket" and self.order_id is not None:
            raise ValueError("explicit_ticket cannot include order_id")
        return self


class SemanticDecision(BaseModel):
    """Provider-facing semantic proposal for the semantic_decision_v2 contract."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    request_type: AgentRequestType = AgentRequestType.UNCLEAR
    target: SemanticTarget | None = None
    reason: str = Field(default="", max_length=300)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    priority: str | None = Field(default=None, max_length=20)
    summary: str | None = Field(default=None, max_length=5000)
    clarification_required: bool = False
    requires_retrieval: bool = False
    knowledge_query: str | None = Field(default=None, max_length=500)
    memory_candidate: MemoryCandidate | None = None
    memory_key: str | None = Field(default=None, max_length=64)


class ExplicitOrderTargetV3(BaseModel):
    """Complete explicit order reference exposed directly in the transport schema."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["explicit_order"]
    order_id: int = Field(gt=0)


class LatestOrderTargetV3(BaseModel):
    """Symbolic order reference; concrete resolution remains server-owned."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["latest_order"]


class ExplicitTicketTargetV3(BaseModel):
    """Complete explicit ticket reference exposed directly in the transport schema."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["explicit_ticket"]
    ticket_id: int = Field(gt=0)


SemanticTargetV3 = Annotated[
    ExplicitOrderTargetV3 | LatestOrderTargetV3 | ExplicitTicketTargetV3,
    Field(discriminator="type"),
]


class SemanticDecisionV3(BaseModel):
    """Provider-facing semantic_decision_v3 proposal with transport-visible targets."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    request_type: AgentRequestType = AgentRequestType.UNCLEAR
    target: SemanticTargetV3 | None = None
    reason: str = Field(default="", max_length=300)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    priority: str | None = Field(default=None, max_length=20)
    summary: str | None = Field(default=None, max_length=5000)
    clarification_required: bool = False
    requires_retrieval: bool = False
    knowledge_query: str | None = Field(default=None, max_length=500)
    memory_candidate: MemoryCandidate | None = None
    memory_key: str | None = Field(default=None, max_length=64)


def normalize_semantic_decision(
    decision: SemanticDecision | SemanticDecisionV3,
) -> SemanticDecision:
    """Convert a valid semantic contract proposal into the existing internal representation."""

    if isinstance(decision, SemanticDecision):
        return decision
    target: SemanticTarget | None = None
    if isinstance(decision.target, ExplicitOrderTargetV3):
        target = SemanticTarget(type="explicit_order", order_id=decision.target.order_id)
    elif isinstance(decision.target, LatestOrderTargetV3):
        target = SemanticTarget(type="latest_order")
    elif isinstance(decision.target, ExplicitTicketTargetV3):
        target = SemanticTarget(type="explicit_ticket", ticket_id=decision.target.ticket_id)
    payload = decision.model_dump(exclude={"target"})
    return SemanticDecision.model_validate({**payload, "target": target})


class AgentErrorCategory(StrEnum):
    RESOURCE_NOT_FOUND = "resource_not_found"
    OWNERSHIP_VIOLATION = "ownership_violation"
    INVALID_STATE = "invalid_state"
    DUPLICATE_ACTION = "duplicate_action"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    UNKNOWN_TOOL = "unknown_tool"
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    DEPENDENCY_ERROR = "dependency_error"
    INTERNAL_ERROR = "internal_error"
    UNKNOWN_WRITE_OUTCOME = "unknown_write_outcome"
    POLICY_DENIED = "policy_denied"
    RETRIEVAL_ERROR = "retrieval_error"
    RERANKER_ERROR = "reranker_error"
    CONFIRMATION_EXPIRED = "confirmation_expired"
    DEPENDENCY_FAILURE = "dependency_failure"


class StructuredDecision(BaseModel):
    intent: Intent
    request_type: AgentRequestType
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=300)
    requires_retrieval: bool = False
    knowledge_query: str | None = Field(default=None, max_length=500)
    memory_candidate: MemoryCandidate | None = None
    memory_key: str | None = Field(default=None, max_length=64)


class AgentToolCall(BaseModel):
    name: str
    status: str
    result: dict[str, object] | None = None


class AgentResponse(BaseModel):
    conversation_id: str
    agent_run_id: str
    message: str
    intent: Intent
    request_type: AgentRequestType
    tool_call: AgentToolCall | None = None
    pending_action: PendingAction | None = None
    decision_reason: str | None = None
    security_signal: str | None = None
    error_category: AgentErrorCategory | None = None
    citations: list[Citation] = Field(default_factory=list)
    failure_category: str | None = None
    degraded_components: list[str] = Field(default_factory=list)
    recovery_action: str | None = None
    write_outcome_unknown: bool = False
    execution_mode: AgentExecutionMode = AgentExecutionMode.RECORDED_REPLAY
    provider: str = "recorded_evidence"
    model: str | None = None
    fallback_message: str | None = None
    proposal: AgentProposal | None = None
    provider_metadata: ProviderRunMetadata | None = None
    conversation_owner: str = "ai"


class AgentChatRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    customer_id: int = Field(gt=0)
    message: str = Field(min_length=1, max_length=5000)
    execution_mode: AgentExecutionMode = AgentExecutionMode.RECORDED_REPLAY
