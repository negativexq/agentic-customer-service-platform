from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import qdrant_client
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.llm.provider import OpenAICompatibleProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, SemanticDecisionV3, StructuredDecision
from app.core import database
from app.core.config import Settings
from app.rag.embeddings import DeterministicEmbeddingProvider
from app.rag.interfaces import RetrievalResult
from app.rag.reranking.service import Reranker
from app.rag.retrieval.hybrid import HybridRetriever
from app.rag.schemas import DocumentChunk, RetrievedChunk
from app.rag.storage.qdrant import QdrantKnowledgeStore
from app.resilience.config import ResilienceConfig


def decision(*, requires_retrieval: bool = False) -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.REFUND_POLICY if requires_retrieval else Intent.CAPABILITY_QUESTION,
        request_type=(
            AgentRequestType.KNOWLEDGE_ONLY
            if requires_retrieval
            else AgentRequestType.INFORMATIONAL
        ),
        reason="timeout test",
        requires_retrieval=requires_retrieval,
        knowledge_query="refund policy" if requires_retrieval else None,
    )


class SlowDecisionProvider:
    def decide(self, **kwargs: object) -> StructuredDecision:
        del kwargs
        raise httpx.ReadTimeout("native LLM request deadline")


class SlowRetriever:
    def retrieve(self, query: str) -> RetrievalResult:
        del query
        raise TimeoutError("native retrieval request deadline")


class SlowReranker(Reranker):
    def score(self, query: str, chunks: Sequence[RetrievedChunk]) -> list[float]:
        del query, chunks
        raise TimeoutError("native reranker deadline")


def timeout_config() -> ResilienceConfig:
    return ResilienceConfig(
        max_retries=0,
        initial_backoff_ms=0,
        max_backoff_ms=0,
        llm_timeout_seconds=0.01,
        retrieval_timeout_seconds=0.01,
        reranker_timeout_seconds=0.01,
    )


def test_llm_invocation_timeout_is_enforced(db_session: Session) -> None:
    result = AgentRuntime(provider=SlowDecisionProvider(), resilience_config=timeout_config()).run(
        conversation_id="llm-timeout",
        customer_id=1,
        message="Help",
        session=db_session,
    )

    assert result.error_category == "llm_error"
    assert result.failure_category == "llm_timeout"


def test_retrieval_timeout_is_enforced(db_session: Session) -> None:
    result = AgentRuntime(
        provider=FakeDecisionProvider([decision(requires_retrieval=True)]),
        knowledge_retriever=SlowRetriever(),
        resilience_config=timeout_config(),
    ).run(
        conversation_id="retrieval-timeout",
        customer_id=1,
        message="What is the refund policy?",
        session=db_session,
    )

    assert result.error_category == "retrieval_error"
    assert result.failure_category == "retrieval_timeout"


def test_reranker_timeout_degrades_to_fused_results() -> None:
    retriever = HybridRetriever(
        DeterministicEmbeddingProvider(),
        reranker=SlowReranker(),
        final_context_count=1,
        reranker_timeout_seconds=0.01,
    )
    retriever.upsert(
        [
            DocumentChunk(
                chunk_id="refund#timeout#0",
                document_id="refund",
                title="Refund",
                category="refund",
                section="timeout",
                source="knowledge/refund.md",
                chunk_index=0,
                content="Delivered orders can be reviewed for refunds.",
            )
        ]
    )

    result = retriever.retrieve("refund")
    assert result.chunks
    assert result.degraded_components == ("reranker",)


def test_llm_http_client_has_explicit_connect_and_request_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def with_structured_output(self, schema: object) -> object:
            del schema
            return object()

    monkeypatch.setattr("app.agent.llm.provider.ChatOpenAI", FakeChatOpenAI)
    OpenAICompatibleProvider(Settings(llm_connect_timeout_seconds=1.5, llm_timeout_seconds=7.0))

    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.read == 7.0
    assert timeout.write == 7.0
    assert captured["max_retries"] == 0


def test_schema_structured_output_mode_asks_for_a_non_strict_json_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Strict provider-side enforcement rejects the decision contract (OpenAI refuses its
    # discriminated unions), so schema mode must not ask for it.
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def with_structured_output(self, schema: object, **kwargs: object) -> object:
            captured["schema"] = schema
            captured["structured_output_kwargs"] = kwargs
            return object()

    monkeypatch.setattr("app.agent.llm.provider.ChatOpenAI", FakeChatOpenAI)
    OpenAICompatibleProvider(Settings(_env_file=None))

    assert captured["structured_output_kwargs"] == {"method": "json_schema", "strict": False}
    # A model class would make the OpenAI SDK force strict mode, so a plain schema is sent.
    schema = captured["schema"]
    assert isinstance(schema, dict)
    assert schema["name"] == "SemanticDecisionV3"
    assert schema["strict"] is False
    assert schema["schema"] == SemanticDecisionV3.model_json_schema()


@pytest.mark.parametrize(
    "contract_version", ["direct_tool_v1", "semantic_decision_v2", "semantic_decision_v3"]
)
def test_schema_mode_request_turns_strict_enforcement_off(contract_version: str) -> None:
    provider = OpenAICompatibleProvider(
        Settings(
            _env_file=None,
            llm_api_key="test-key",
            agent_decision_contract_version=contract_version,
        )
    )
    binding = cast(Any, provider._model).steps[0]
    request_kwargs = {
        key: value for key, value in binding.kwargs.items() if key != "ls_structured_output_format"
    }

    payload = binding.bound._get_request_payload([("user", "hi")], **request_kwargs)

    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is False


def test_schema_mode_validates_the_plain_json_reply_against_the_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRunnable:
        def invoke(self, messages: object) -> dict[str, object]:
            del messages
            return {"intent": "order_lookup", "target": {"type": "latest_order"}}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def with_structured_output(self, schema: object, **kwargs: object) -> FakeRunnable:
            del schema, kwargs
            return FakeRunnable()

    monkeypatch.setattr("app.agent.llm.provider.ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr("app.agent.llm.provider.guidance_prompt", lambda: "")
    provider = OpenAICompatibleProvider(Settings(_env_file=None))

    decision = provider.decide(
        messages=[{"role": "user", "content": "Where is my latest order?"}],
        customer_id=1,
    )

    assert isinstance(decision, SemanticDecisionV3)
    assert decision.intent == Intent.ORDER_LOOKUP


def test_function_calling_structured_output_parses_decision_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected: dict[str, object] = {
        "intent": "order_lookup",
        "request_type": "read_action",
        "tool_name": "get_order",
        "arguments": {"customer_id": 1, "order_id": 3},
        "reason": "customer asked for an order status",
    }

    class FakeRunnable:
        def invoke(self, messages: object) -> dict[str, object]:
            del messages
            return expected

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def with_structured_output(self, schema: object, **kwargs: object) -> FakeRunnable:
            captured["schema"] = schema
            captured["structured_output_kwargs"] = kwargs
            return FakeRunnable()

    monkeypatch.setattr("app.agent.llm.provider.ChatOpenAI", FakeChatOpenAI)
    provider = OpenAICompatibleProvider(
        Settings(
            _env_file=None,
            llm_structured_output_mode="function_calling",
            agent_decision_contract_version="direct_tool_v1",
        )
    )

    result = provider.decide(
        messages=[{"role": "user", "content": "Where is order 3?"}],
        customer_id=1,
    )

    assert result == StructuredDecision.model_validate(expected)
    assert captured["structured_output_kwargs"] == {"method": "function_calling"}


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high"])
def test_llm_reasoning_effort_is_forwarded_when_configured(
    monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def with_structured_output(self, schema: object) -> object:
            del schema
            return object()

    monkeypatch.setattr("app.agent.llm.provider.ChatOpenAI", FakeChatOpenAI)
    OpenAICompatibleProvider(Settings(llm_reasoning_effort=effort))

    assert captured["reasoning_effort"] == effort


def test_llm_reasoning_effort_is_omitted_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def with_structured_output(self, schema: object) -> object:
            del schema
            return object()

    monkeypatch.setattr("app.agent.llm.provider.ChatOpenAI", FakeChatOpenAI)
    OpenAICompatibleProvider(Settings())

    assert "reasoning_effort" not in captured


def test_invalid_llm_reasoning_effort_fails_settings_validation() -> None:
    with pytest.raises(ValueError):
        Settings(llm_reasoning_effort="extreme")


def test_empty_llm_reasoning_effort_is_treated_as_unset() -> None:
    assert Settings(llm_reasoning_effort="").llm_reasoning_effort is None


def test_qdrant_http_client_has_an_explicit_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeQdrantClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(qdrant_client, "QdrantClient", FakeQdrantClient)
    QdrantKnowledgeStore(
        "http://qdrant.test",
        "knowledge",
        DeterministicEmbeddingProvider(),
        timeout_seconds=2.2,
    )

    assert captured["timeout"] == 2


def test_qdrant_request_uses_native_timeout_before_retry() -> None:
    events: list[str] = []
    calls = 0

    class FakeClient:
        def get_collection(self, name: str) -> SimpleNamespace:
            del name
            return SimpleNamespace(
                config=SimpleNamespace(
                    metadata={
                        "lexical_index": {
                            "version": 1,
                            "vocabulary": {"refund": 1},
                            "inverse_document_frequency": {"refund": 1.0},
                            "average_document_length": 1.0,
                            "document_count": 1,
                        }
                    }
                )
            )

        def query_points(self, **kwargs: object) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            events.append(f"start:{calls}:{kwargs['timeout']}")
            try:
                if calls == 1:
                    raise TimeoutError("qdrant native timeout")
                return SimpleNamespace(points=[])
            finally:
                events.append(f"end:{calls}")

    from app.rag.backends.qdrant import QdrantKnowledgeBackend

    backend = QdrantKnowledgeBackend(
        url="http://qdrant.test",
        collection_name="knowledge",
        embedding_provider=DeterministicEmbeddingProvider(),
        reranker=None,
        reranker_enabled=False,
        rerank_candidates=4,
        final_context_count=2,
        timeout_seconds=1.5,
        reranker_timeout_seconds=0.1,
        client=FakeClient(),
    )

    from app.resilience.config import ResilienceConfig
    from app.resilience.retry import run_with_retry

    result = run_with_retry(
        lambda: backend.retrieve("refund"),
        dependency="retrieval",
        config=ResilienceConfig(max_retries=1, initial_backoff_ms=0, max_backoff_ms=0),
        timeout_seconds=1.5,
        sleeper=lambda _: None,
    )

    assert result.chunks == ()
    assert events == ["start:1:1", "end:1", "start:2:1", "end:2"]


def test_openai_embedding_client_has_native_timeout_and_no_hidden_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeEmbeddings:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def embed_query(self, text: str) -> list[float]:
            del text
            return [1.0]

    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", FakeEmbeddings)
    from app.rag.embeddings.providers import OpenAIEmbeddingProvider

    provider = OpenAIEmbeddingProvider(
        model="embedding-model",
        api_key=None,
        base_url="http://embedding.test/v1",
        connect_timeout_seconds=1.5,
        timeout_seconds=7.0,
    )
    provider.embed_query("refund")

    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.read == 7.0
    assert timeout.write == 7.0
    assert captured["max_retries"] == 0


def test_postgres_engine_has_explicit_connect_pool_and_query_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> Engine:
        captured["url"] = url
        captured.update(kwargs)
        return cast(Engine, object())

    settings = Settings(
        database_url="postgresql+psycopg://app:app@db:5432/customer_service",
        database_connect_timeout_seconds=2.1,
        database_query_timeout_seconds=4.2,
        database_pool_timeout_seconds=1.5,
    )
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "create_engine", fake_create_engine)

    database.build_engine()

    assert captured["pool_timeout"] == 1.5
    connect_args = cast(dict[str, object], captured["connect_args"])
    assert connect_args["connect_timeout"] == 3
    assert connect_args["options"] == "-c statement_timeout=4200"
