from collections.abc import Sequence

from app.rag.answer_generator import evidence_terms, query_term_overlap
from app.rag.schemas import RetrievedChunk


class DeterministicReranker:
    """Score each chunk by the share of the question's content terms it covers.

    Uses the same stop-word removal and light stemming as the grounding gate, so
    "shipping" matches "shipment" and filler words do not dilute the score.
    """

    def score(self, query: str, chunks: Sequence[RetrievedChunk]) -> list[float]:
        return [query_term_overlap(query, evidence_terms(chunk)) for chunk in chunks]
