import re
from collections.abc import Callable, Sequence

from app.agent.decision_compiler import KNOWLEDGE_QUERIES
from app.agent.localize import detect_language
from app.agent.schemas import AgentErrorCategory, Intent
from app.agent.state import AgentState
from app.rag.answer_generator import (
    GroundedAnswerGenerator,
    content_terms,
    evidence_terms,
    insufficient_evidence_answer,
    normalize_knowledge_query,
    term_matches,
)
from app.rag.interfaces import KnowledgeRetriever
from app.rag.schemas import Citation, RetrievedChunk
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
        topic_query = _TOPIC_QUERIES.get(intent) if isinstance(intent, Intent) else None
        topic_category = _TOPIC_CATEGORIES.get(intent) if isinstance(intent, Intent) else None
        if (
            grounded.status.value == "pass"
            and policy_query is None
            and topic_category is not None
            and not _cites_only(grounded.citations, chunks, topic_category)
        ):
            # A shipping question answered from the cancellation article shares a word
            # ("shipment"), not an answer; look in the shipping article instead.
            grounded = insufficient_evidence_answer()
        if grounded.status.value != "pass" and policy_query is None and topic_query:
            # "When will it arrive?" or "ya kargo?" share few words with the article; the
            # classified policy topic still names which article answers it, so look it up again,
            # in that article only.
            try:
                topic_chunks = retriever.retrieve(normalize_knowledge_query(topic_query)).chunks
            except Exception:  # noqa: BLE001 - the first answer already stands
                topic_chunks = chunks
            others = set(_TOPIC_CATEGORIES.values()) - {topic_category}
            topic_chunks = tuple(chunk for chunk in topic_chunks if chunk.category not in others)
            retry = generator.answer(topic_query, topic_chunks)
            if retry.status.value == "pass" and not _names_what_the_article_lacks(
                customer_query, topic_chunks
            ):
                grounded, chunks = retry, topic_chunks
        knowledge_answer = grounded.answer
        if tool_result is not None and _cancelled(tool_result, intent):
            knowledge_answer = _cancelled_without_reason(tool_result)
        elif tool_result is not None:
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


_TOPIC_QUERIES = {
    Intent.SHIPPING_POLICY: "shipping processing transit delivery business days",
    Intent.REFUND_POLICY: "refund eligibility delivered damaged returned",
    Intent.CANCELLATION_POLICY: "cancellation before shipment processing",
}

_TOPIC_CATEGORIES = {
    Intent.SHIPPING_POLICY: "shipping",
    Intent.REFUND_POLICY: "refund",
    Intent.CANCELLATION_POLICY: "cancellation",
}


def _cites_only(
    citations: Sequence[Citation], chunks: Sequence[RetrievedChunk], category: str
) -> bool:
    """No citation comes from a different policy article than the classified one."""

    categories = {chunk.chunk_id: chunk.category for chunk in chunks}
    topics = set(_TOPIC_CATEGORIES.values())
    return not any(
        categories.get(citation.chunk_id) in topics - {category} for citation in citations
    )


def _names_what_the_article_lacks(question: str, chunks: Sequence[RetrievedChunk]) -> bool:
    """The question names some of the article's words but not all of them.

    A vague follow-up ("ne zaman gelir?") shares no words with the article and the classified
    topic is its best reading. "Do you ship internationally?" or "how much is shipping?" names
    the topic and something more; when the article lacks that something, the honest answer is
    that the articles don't cover it, not the article's general text.
    """

    if detect_language(question) is not None:
        # A Turkish question can't be compared word for word with an English article; any
        # shared word is chance ("normal mi?"), so the classified topic stands.
        return False
    evidence: set[str] = set()
    for chunk in chunks:
        evidence |= evidence_terms(chunk)
    terms = content_terms(normalize_knowledge_query(question))
    matched = {term for term in terms if term_matches(term, evidence)}
    if not matched:
        return False
    # A longer word the article lacks ("internationally") names what is really asked; short
    # words ("like", "ok") are too often filler to count.
    if any(len(term) >= 5 for term in terms - matched):
        return True
    # "How much is shipping?": a price question needs a price in the article.
    return bool(_ASKS_AMOUNT.search(question)) and not any(
        _AMOUNT.search(chunk.content) for chunk in chunks
    )


_ASKS_AMOUNT = re.compile(r"\b(?:how much|cost|costs|price|fee|fees|charge)\b|ücret|fiyat", re.I)
_AMOUNT = re.compile(r"[$€£]\s?\d|\d+(?:[.,]\d+)?\s?(?:usd|eur|gbp|tl|dollars?)\b", re.I)


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


def _cancelled(tool_result: dict[str, object], intent: object) -> bool:
    return intent == Intent.CANCELLATION_EXPLANATION and tool_result.get("status") == "cancelled"


def _cancelled_without_reason(tool_result: dict[str, object]) -> str:
    """Orders keep no cancellation reason, so "why?" gets the fact and a next step, not policy."""

    order_id = tool_result.get("id")
    subject = f"Order #{order_id}" if order_id is not None else "This order"
    return (
        f"{subject} is cancelled. Our records don't show why it was cancelled. If you didn't "
        "ask for this, I can open a support ticket or connect you with a person to look into it."
    )
