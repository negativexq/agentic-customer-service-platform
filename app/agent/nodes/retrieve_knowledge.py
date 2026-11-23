from collections.abc import Callable

from app.agent.decision_compiler import KNOWLEDGE_QUERIES
from app.agent.schemas import AgentErrorCategory, Intent
from app.agent.state import AgentState
from app.rag.answer_generator import GroundedAnswerGenerator, normalize_knowledge_query
from app.rag.interfaces import KnowledgeRetriever
from app.resilience.config import ResilienceConfig
from app.resilience.control import ReliabilityController
from app.resilience.errors import ResilienceError, RetryExhaustedError
from app.resilience.fallbacks import degraded_message
from app.resilience.retry import run_with_retry


def make_retrieve_node(
    retriever: KnowledgeRetriever,
    generator: GroundedAnswerGenerator,
    resilience_config: ResilienceConfig | None = None,
    reliability_controller: ReliabilityController | None = None,
) -> Callable[[AgentState], AgentState]:
    def retrieve_knowledge(state: AgentState) -> AgentState:
        customer_query = _latest_user_message(state)
        query = normalize_knowledge_query(customer_query)
        if query == customer_query:
            query = normalize_knowledge_query(state.get("knowledge_query") or customer_query)
        timeout_seconds = (resilience_config or ResilienceConfig()).retrieval_timeout_seconds
        try:
            retrieval = run_with_retry(
                lambda: retriever.retrieve(query),
                dependency="retrieval",
                config=resilience_config,
                controller=reliability_controller,
                service_identity=f"retrieval:{type(retriever).__name__}",
                timeout_seconds=timeout_seconds,
            )
        except (RetryExhaustedError, ResilienceError) as error:
            knowledge_only = state.get("tool_result") is None
            fallback = degraded_message(error.category, knowledge_only=knowledge_only)
            if knowledge_only:
                return {
                    "last_error": fallback,
                    "error_category": AgentErrorCategory.RETRIEVAL_ERROR,
                    "failure_category": error.category.value,
                    "recovery_action": "degraded",
                    "degraded_components": ["retrieval"],
                    "answer_grounding": _unavailable_grounding(),
                }
            existing_tool_result = state.get("tool_result") or {}
            current_status = existing_tool_result.get("status")
            business_fallback = (
                f"The current business result is {current_status}. "
                if current_status is not None
                else "The current business result was retrieved. "
            )
            return {
                "knowledge_answer": business_fallback + fallback,
                "failure_category": error.category.value,
                "recovery_action": "degraded",
                "degraded_components": ["retrieval"],
                "answer_grounding": _unavailable_grounding(),
            }
        chunks = retrieval.chunks
        degraded = retrieval.degraded_components
        metadata = retrieval.metadata
        retrieval_metadata = {
            "backend": metadata.backend,
            "embedding_provider": metadata.embedding_provider,
            "reranker_enabled": metadata.reranker_enabled,
            "retrieval_count": metadata.retrieval_count,
            "latency_seconds": metadata.latency_seconds,
            "fallback_status": metadata.fallback_status,
            "hybrid": metadata.hybrid,
            "fusion_strategy": metadata.fusion_strategy,
            "dense_candidate_count": metadata.dense_candidate_count,
            "sparse_candidate_count": metadata.sparse_candidate_count,
        }
        # Retrieval may use a normalized semantic query, but grounding must be
        # checked against the customer's observable question. This prevents a
        # broad classifier query from making unrelated evidence look relevant.
        tool_result = state.get("tool_result")
        # "Why not?" carries no policy words of its own. With an order in hand, ground the
        # answer on the fixed policy question for this intent instead of the bare follow-up.
        intent = state.get("intent")
        policy_query = (
            KNOWLEDGE_QUERIES.get(intent) if tool_result and isinstance(intent, Intent) else None
        )
        grounded = generator.answer(policy_query or customer_query, chunks)
        knowledge_answer = grounded.answer
        if tool_result is not None:
            knowledge_answer = _with_order_status(
                tool_result, grounded.answer if grounded.status.value == "pass" else ""
            )
        return {
            "retrieved_chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "retrieval_metadata": retrieval_metadata,
            "knowledge_answer": knowledge_answer,
            "citations": [citation.model_dump(mode="json") for citation in grounded.citations],
            "answer_grounding": {
                "status": grounded.status.value,
                "sources_used": grounded.source_count,
                "citation_count": len(grounded.citations),
                "citation_coverage": grounded.validation.citation_coverage,
                "unsupported_claim_count": len(grounded.unsupported_claims),
                "confidence": grounded.confidence,
                "accepted": grounded.validation.accepted,
            },
            "degraded_components": list(degraded),
            "recovery_action": "degraded" if degraded else None,
        }

    return retrieve_knowledge


def _unavailable_grounding() -> dict[str, object]:
    return {
        "status": "retrieval_unavailable",
        "sources_used": 0,
        "citation_count": 0,
        "citation_coverage": 0.0,
        "unsupported_claim_count": 0,
        "confidence": 0.0,
        "accepted": False,
    }


def _latest_user_message(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if message["role"] == "user":
            return message["content"]
    return ""


_STATUS_PHRASES = {
    "pending": "is still pending",
    "processing": "is being processed",
    "shipped": "has already shipped",
    "delivered": "has been delivered",
    "cancelled": "is already cancelled",
}


def _with_order_status(tool_result: dict[str, object], policy: str) -> str:
    """Lead with the order's own state in plain words; the business system is the authority."""

    status = tool_result.get("status")
    if not isinstance(status, str):
        return policy
    phrase = _STATUS_PHRASES.get(status, f"is {status.replace('_', ' ')}")
    order_id = tool_result.get("id")
    subject = f"Order #{order_id}" if order_id is not None else "This order"
    lead = f"{subject} {phrase}."
    policy = policy.removeprefix("Based on the retrieved evidence: ")
    return f"{lead} {policy}".strip()
