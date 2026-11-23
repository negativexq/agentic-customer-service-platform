from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.rag.config import EmbeddingProviderType
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.deterministic import DeterministicEmbeddingProvider


class OpenAIEmbeddingProvider:
    """Lazy adapter around the already-supported LangChain OpenAI integration."""

    provider_type = "openai"

    def __init__(
        self,
        *,
        model: str,
        api_key: SecretStr | None,
        base_url: str | None,
        connect_timeout_seconds: float = 5.0,
        timeout_seconds: float = 30.0,
        dimensions: int | None = None,
        query_instruction: str = "",
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.query_instruction = query_instruction
        self.api_key = api_key
        self.base_url = base_url
        self.connect_timeout_seconds = connect_timeout_seconds
        self.timeout_seconds = timeout_seconds
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            from langchain_openai import OpenAIEmbeddings

            kwargs: dict[str, object] = {"model": self.model}
            if self.api_key is not None:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
                # OpenAI-compatible servers such as Ollama take raw text, not tiktoken ids.
                kwargs["check_embedding_ctx_length"] = False
            if self.dimensions is not None:
                kwargs["dimensions"] = self.dimensions
            kwargs["timeout"] = httpx.Timeout(
                connect=self.connect_timeout_seconds,
                read=self.timeout_seconds,
                write=self.timeout_seconds,
                pool=self.connect_timeout_seconds,
            )
            # Application retry policy owns retries; the SDK must not create hidden attempts.
            kwargs["max_retries"] = 0
            self._client = OpenAIEmbeddings(**kwargs)
        return self._client

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return cast(list[list[float]], self._get_client().embed_documents(list(texts)))

    def embed_query(self, text: str) -> list[float]:
        return cast(list[float], self._get_client().embed_query(self.query_instruction + text))

    def embed(self, text: str) -> list[float]:
        return self.embed_query(text)


class HuggingFaceEmbeddingProvider:
    """Optional lazy adapter; no heavyweight model dependency is required by the app."""

    provider_type = "huggingface"

    def __init__(self, *, model: str) -> None:
        self.model = model
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from langchain_huggingface import HuggingFaceEmbeddings
            except ImportError as error:
                raise RuntimeError(
                    "The huggingface embedding provider requires the optional "
                    "langchain-huggingface package."
                ) from error
            self._client = HuggingFaceEmbeddings(model_name=self.model)
        return self._client

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return cast(list[list[float]], self._get_client().embed_documents(list(texts)))

    def embed_query(self, text: str) -> list[float]:
        return cast(list[float], self._get_client().embed_query(text))

    def embed(self, text: str) -> list[float]:
        return self.embed_query(text)


def build_embedding_provider(
    settings: Settings,
    *,
    timeout_seconds: float | None = None,
    connect_timeout_seconds: float | None = None,
) -> EmbeddingProvider:
    provider = EmbeddingProviderType(settings.embedding_provider)
    if provider is EmbeddingProviderType.DETERMINISTIC:
        return DeterministicEmbeddingProvider(settings.embedding_dimension)
    if provider is EmbeddingProviderType.OPENAI:
        return OpenAIEmbeddingProvider(
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            connect_timeout_seconds=(
                connect_timeout_seconds
                if connect_timeout_seconds is not None
                else settings.embedding_connect_timeout_seconds
            ),
            timeout_seconds=(
                timeout_seconds
                if timeout_seconds is not None
                else settings.embedding_timeout_seconds
            ),
            dimensions=settings.embedding_output_dimension,
            query_instruction=settings.embedding_query_instruction,
        )
    return HuggingFaceEmbeddingProvider(model=settings.embedding_model)
