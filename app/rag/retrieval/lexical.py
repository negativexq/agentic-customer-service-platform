from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.schemas import DocumentChunk

LEXICAL_VECTOR_NAME = "lexical"
LEXICAL_METADATA_KEY = "lexical_index"
LEXICAL_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LexicalIndex:
    """Deterministic vocabulary and IDF weights used by Qdrant sparse vectors."""

    vocabulary: dict[str, int]
    inverse_document_frequency: dict[str, float]
    average_document_length: float
    document_count: int

    def encode(self, text: str) -> tuple[list[int], list[float]]:
        tokens = _tokens(text)
        counts = Counter(tokens)
        length = max(len(tokens), 1)
        denominator = 0.25 + 0.75 * length / max(self.average_document_length, 1.0)
        indices: list[int] = []
        values: list[float] = []
        for token in sorted(counts):
            index = self.vocabulary.get(token)
            idf = self.inverse_document_frequency.get(token)
            if index is None or idf is None:
                continue
            indices.append(index)
            values.append(counts[token] * idf / denominator)
        return indices, values

    def encode_query(self, text: str) -> tuple[list[int], list[float]]:
        counts = Counter(_tokens(text))
        indices: list[int] = []
        values: list[float] = []
        for token in sorted(counts):
            index = self.vocabulary.get(token)
            idf = self.inverse_document_frequency.get(token)
            if index is None or idf is None:
                continue
            indices.append(index)
            values.append(counts[token] * idf)
        return indices, values

    def to_metadata(self, *, version: int = LEXICAL_SCHEMA_VERSION) -> dict[str, object]:
        return {
            "version": version,
            "vocabulary": self.vocabulary,
            "inverse_document_frequency": self.inverse_document_frequency,
            "average_document_length": self.average_document_length,
            "document_count": self.document_count,
        }

    @classmethod
    def from_metadata(
        cls, metadata: object, *, expected_version: int = LEXICAL_SCHEMA_VERSION
    ) -> LexicalIndex:
        if not isinstance(metadata, dict):
            raise ValueError("Qdrant lexical metadata is missing.")
        if metadata.get("version") != expected_version:
            raise ValueError("Qdrant lexical metadata version is unsupported.")
        vocabulary = metadata.get("vocabulary")
        inverse_document_frequency = metadata.get("inverse_document_frequency")
        average_document_length = metadata.get("average_document_length")
        document_count = metadata.get("document_count")
        if not isinstance(vocabulary, dict) or not isinstance(inverse_document_frequency, dict):
            raise ValueError("Qdrant lexical metadata has an invalid vocabulary.")
        if not isinstance(average_document_length, (float, int)):
            raise ValueError("Qdrant lexical metadata has an invalid average length.")
        if not isinstance(document_count, int) or document_count < 1:
            raise ValueError("Qdrant lexical metadata has an invalid document count.")
        normalized_vocabulary: dict[str, int] = {}
        normalized_idf: dict[str, float] = {}
        for token, index in vocabulary.items():
            if not isinstance(token, str) or not isinstance(index, int) or index < 1:
                raise ValueError("Qdrant lexical metadata has an invalid token index.")
            value = inverse_document_frequency.get(token)
            if not isinstance(value, (float, int)) or value <= 0:
                raise ValueError("Qdrant lexical metadata has an invalid IDF weight.")
            normalized_vocabulary[token] = index
            normalized_idf[token] = float(value)
        return cls(
            vocabulary=normalized_vocabulary,
            inverse_document_frequency=normalized_idf,
            average_document_length=float(average_document_length),
            document_count=document_count,
        )


METADATA_PRECISION = 12


def build_lexical_index(chunks: Sequence[DocumentChunk]) -> LexicalIndex:
    documents = [_tokens(chunk.content) for chunk in chunks]
    document_count = len(documents)
    if document_count < 1:
        raise ValueError("At least one knowledge chunk is required for lexical indexing.")
    document_frequency = Counter(token for tokens in documents for token in set(tokens))
    vocabulary = {token: index for index, token in enumerate(sorted(document_frequency), start=1)}
    # Rounded so the values survive Qdrant's JSON metadata round trip bit-for-bit; full
    # double precision can come back with a different last digit and break provenance hashes.
    inverse_document_frequency = {
        token: round(math.log((document_count + 1) / (frequency + 1)) + 1.0, METADATA_PRECISION)
        for token, frequency in document_frequency.items()
    }
    average_document_length = round(
        sum(len(tokens) for tokens in documents) / document_count, METADATA_PRECISION
    )
    return LexicalIndex(
        vocabulary=vocabulary,
        inverse_document_frequency=inverse_document_frequency,
        average_document_length=average_document_length,
        document_count=document_count,
    )


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())
