import operator
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from app.agent.decision_compiler import CompiledDecision
from app.agent.schemas import (
    AgentErrorCategory,
    AgentExecutionMode,
    AgentProposal,
    AgentRequestType,
    Intent,
    ProviderRunMetadata,
    SemanticDecision,
)
from app.core.context import ExecutionContext
from app.memory.schemas import MemoryCandidate
from app.policies.models import PendingAction, PolicyDecision


class ConversationMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


WorkflowLifecycleState = Literal[
    "active",
    "waiting_confirmation",
    "suspended",
    "superseded",
    "completed",
    "cancelled",
]


class SuspendedWorkflowState(TypedDict, total=False):
    """Bounded workflow snapshot; it contains no prompts or hidden reasoning."""

    intent: Intent
    pending_action: PendingAction | None
    pending_workflow_decision: SemanticDecision | None
    collected_entities: dict[str, str | int | bool]
    tool_arguments: dict[str, object]
    missing_required_fields: list[str]
    validation_context: dict[str, object]
    policy_inputs: dict[str, object]
    source_state: WorkflowLifecycleState
    workflow_id: str
    superseded_by: str | None


class AgentState(TypedDict, total=False):
    execution_context: ExecutionContext
    conversation_id: str
    conversation_tenant_id: str
    conversation_customer_id: int
    conversation_actor_id: str
    conversation_actor_type: str
    conversation_ownership_version: int
    conversation_ai_resumed_at: datetime | None
    messages: Annotated[list[ConversationMessage], operator.add]
    intent: Intent
    request_type: AgentRequestType
    semantic_decision: SemanticDecision | None
    compile_result: CompiledDecision | None
    grounding_status: str
    grounding_reference_type: str | None
    grounding_trusted_source: str | None
    target_validation_status: str
    selected_tool: str | None
    tool_arguments: dict[str, object]
    tool_result: dict[str, object] | None
    pending_action: PendingAction | None
    policy_decision: PolicyDecision | None
    confirmation_status: str | None
    action_id: str | None
    requires_retrieval: bool
    knowledge_query: str | None
    retrieved_chunks: list[dict[str, object]]
    retrieval_metadata: dict[str, object]
    answer_grounding: dict[str, object]
    knowledge_answer: str | None
    citations: list[dict[str, object]]
    retry_count: int
    last_error: str | None
    error_category: AgentErrorCategory | None
    decision_reason: str | None
    final_response: str
    agent_run_id: str
    tool_execution_status: str | None
    memory_context: list[dict[str, object]]
    memory_candidate: MemoryCandidate | None
    memory_key: str | None
    memory_operation_status: str | None
    memory_written: dict[str, object] | None
    memory_policy_outcome: str | None
    failure_category: str | None
    degraded_components: list[str]
    recovery_action: str | None
    write_outcome_unknown: bool
    replay_detected: bool
    idempotency_outcome: str | None
    execution_mode: AgentExecutionMode
    provider: str
    model: str | None
    fallback_message: str | None
    proposal: AgentProposal | None
    provider_metadata: ProviderRunMetadata | None
    security_signal: str | None
    memory_security_signal: str | None
    memory_summary_requested: bool
    previous_intent: Intent | None
    pending_workflow_decision: SemanticDecision | None
    missing_required_fields: list[str]
    collected_entities: dict[str, str | int | bool]
    workflow_active: bool
    workflow_resume_status: str | None
    workflow_state: WorkflowLifecycleState
    workflow_interruption_pending: bool
    workflow_interruption_status: str | None
    previous_workflow_intent: Intent | None
    interruption_intent: Intent | None
    workflow_resume_source: str | None
    suspended_workflow: SuspendedWorkflowState | None
    superseded_workflow: SuspendedWorkflowState | None
    workflow_id: str | None
    previous_workflow_id: str | None
    superseded_by: str | None
    workflow_transition: str | None
    workflow_interruption_type: str | None
    workflow_tool_arguments: dict[str, object]
    workflow_validation_context: dict[str, object]
    workflow_policy_inputs: dict[str, object]
    pending_action_restored: bool
    restored_fields_count: int
    compilation_resumed: bool
    original_policy_inputs_hash: str
    restored_policy_inputs_hash: str
    policy_input_diff: str
    original_pending_policy_inputs: str
    restored_policy_inputs: str
    original_policy_inputs_normalized: str
    restored_policy_inputs_normalized: str
    policy_revalidation_stage: str
    policy_revalidation_result: str
