"""Citation-constrained, provider-neutral grounded answer generation.

The generator is deliberately extractive by default: retrieved evidence is the
only knowledge source, and every emitted factual claim carries an evidence
marker. A future synthesis provider may produce the same bounded contract, but
it must pass ``GroundingValidator`` before its output can reach a customer.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Sequence

from app.observability.metrics import get_metrics
from app.observability.tracing import span
from app.rag.context import construct_context
from app.rag.schemas import (
    AnswerGroundingStatus,
    Citation,
    GroundedAnswer,
    GroundingValidationResult,
    RetrievedChunk,
)

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_CITATION = re.compile(r"\[([^\]]+)\]")
_QUANTIFIED_FACT = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(days?|months?|years?|hours?|%|usd|eur|gbp)\b",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "and",
    "any",
    "been",
    "but",
    "did",
    "get",
    "had",
    "has",
    "have",
    "into",
    "its",
    "know",
    "much",
    "need",
    "our",
    "should",
    "some",
    "take",
    "than",
    "that",
    "them",
    "then",
    "there",
    "they",
    "usually",
    "typically",
    "generally",
    "want",
    "was",
    "were",
    "will",
    "would",
    "you",
    "about",
    "are",
    "can",
    "could",
    "does",
    "for",
    "from",
    "how",
    "is",
    "me",
    "my",
    "please",
    "tell",
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "your",
}
_EVIDENCE_REFERENCE_TERMS = {
    "article",
    "content",
    "document",
    "evidence",
    "note",
    "source",
    "text",
}
_MIN_RELEVANCE_SCORE = 0.35
_GENERIC_QUERY_TERMS = {
    "account",
    "change",
    "update",
    "allow",
    "allowed",
    "answer",
    "apply",
    "but",
    "cant",
    "cannot",
    "control",
    "controls",
    "days",
    "eligibility",
    "eligible",
    "explain",
    "first",
    "how",
    "information",
    "long",
    "many",
    "order",
    "policy",
    "process",
    "product",
    "question",
    "refund",
    "return",
    "support",
    "yes",
    "why",
    "work",
    "works",
}
_SAFE_UNCERTAINTY = (
    "The knowledge base does not contain enough information to answer this question from the "
    "retrieved evidence. Please clarify the request or provide an applicable knowledge source."
)


class GroundingValidator:
    """Fail-closed validator for bounded answer and citation contracts."""

    def __init__(self, minimum_coverage: float = 0.95) -> None:
        if not 0.0 <= minimum_coverage <= 1.0:
            raise ValueError("minimum_coverage must be between zero and one")
        self.minimum_coverage = minimum_coverage

    def validate(
        self,
        answer: GroundedAnswer,
        evidence: Sequence[RetrievedChunk],
    ) -> GroundingValidationResult:
        evidence_by_chunk = {chunk.chunk_id: chunk for chunk in evidence}
        citations_by_id = {citation.citation_id: citation for citation in answer.citations}
        valid_citation_ids: set[str] = set()
        reasons: list[str] = []
        unsupported = list(answer.unsupported_claims)

        for citation in answer.citations:
            chunk = evidence_by_chunk.get(citation.chunk_id)
            if chunk is None or citation.document_id != chunk.document_id:
                reasons.append("citation_not_in_retrieved_evidence")
                continue
            if _normalize(citation.quoted_excerpt) not in _normalize(chunk.content):
                reasons.append("citation_excerpt_mismatch")
                continue
            valid_citation_ids.add(citation.citation_id)

        supported_claims = 0
        for claim in answer.grounded_claims:
            claim_position = answer.answer.find(claim)
            if claim_position < 0:
                unsupported.append(claim)
                continue
            suffix = answer.answer[claim_position + len(claim) : claim_position + len(claim) + 160]
            markers = set(_CITATION.findall(suffix))
            supporting_citations = [
                citations_by_id[marker]
                for marker in markers & valid_citation_ids
                if marker in citations_by_id
            ]
            if any(
                _normalize(claim) in _normalize(citation.quoted_excerpt)
                for citation in supporting_citations
            ):
                supported_claims += 1
            else:
                unsupported.append(claim)
                reasons.append("answer_evidence_mismatch")

        total_claims = len(answer.grounded_claims) + len(answer.unsupported_claims)
        coverage = supported_claims / total_claims if total_claims else 0.0
        if not evidence:
            reasons.append("empty_evidence")
        if answer.grounded_claims and not answer.citations:
            reasons.append("citations_missing")
        if coverage < self.minimum_coverage:
            reasons.append("citation_coverage_below_threshold")
        if unsupported:
            reasons.append("unsupported_claims_present")

        accepted = bool(evidence) and not reasons and coverage >= self.minimum_coverage
        return GroundingValidationResult(
            citation_coverage=coverage,
            accepted=accepted,
            unsupported_claim_count=len(set(unsupported)),
            reasons=list(dict.fromkeys(reasons)),
        )


class GroundedAnswerGenerator:
    """Generate bounded extractive answers and reject unsupported synthesis."""

    def __init__(
        self,
        max_context: int = 4,
        validator: GroundingValidator | None = None,
    ) -> None:
        self.max_context = max_context
        self.validator = validator or GroundingValidator()

    def answer(self, query: str, chunks: Sequence[RetrievedChunk]) -> GroundedAnswer:
        normalized_query = normalize_knowledge_query(query)
        with span("rag.answer_generate") as generation_span:
            with span("rag.context_build") as context_span:
                context = construct_context(chunks, self.max_context)
                context_span.set_attribute("rag.final_context_chunks", len(context))
            relevant = [chunk for chunk in context if _is_relevant(normalized_query, chunk)]
            if not relevant:
                result = _uncertain_answer()
            else:
                result = self._synthesize(relevant, normalized_query)
                validation_started = time.perf_counter()
                validation_status = "error"
                try:
                    validation = self.validator.validate(result, relevant)
                    validation_status = "accepted" if validation.accepted else "rejected"
                finally:
                    get_metrics().grounding_validation_duration_seconds.record(
                        time.perf_counter() - validation_started,
                        {"status": validation_status},
                    )
                result = result.model_copy(
                    update={"validation": validation, "grounded": validation.accepted}
                )
                if not validation.accepted:
                    result = _rejected_answer(result, validation)
            _record_grounding_observability(generation_span, result, len(context))
            return result

    def _synthesize(self, chunks: Sequence[RetrievedChunk], query: str) -> GroundedAnswer:
        conflict = _find_conflict(chunks)
        selected = list(conflict or chunks[:2])
        focus = _query_focus(query)
        excerpts = [_excerpt(chunk.content, focus=focus) for chunk in selected]
        citations = [
            _citation(chunk, excerpt) for chunk, excerpt in zip(selected, excerpts, strict=True)
        ]
        claims = excerpts
        if conflict:
            statements = " ".join(
                f"{claim} [{citation.citation_id}]."
                for claim, citation in zip(claims, citations, strict=True)
            )
            answer = (
                f"The retrieved sources conflict: {statements} "
                "I cannot resolve the conflict from the available evidence."
            )
            status = AnswerGroundingStatus.CONFLICT
            confidence = 0.25
        else:
            statements = " ".join(
                f"{claim} [{citation.citation_id}]."
                for claim, citation in zip(claims, citations, strict=True)
            )
            answer = f"Based on the retrieved evidence: {statements}"
            status = AnswerGroundingStatus.PASS
            confidence = _confidence(citations)
        return GroundedAnswer(
            answer=answer,
            citations=citations,
            confidence=confidence,
            grounded_claims=claims,
            unsupported_claims=[],
            source_count=len({citation.document_id for citation in citations}),
            status=status,
            validation=GroundingValidationResult(
                citation_coverage=0.0,
                accepted=False,
                unsupported_claim_count=0,
            ),
        )


def _uncertain_answer() -> GroundedAnswer:
    return GroundedAnswer(
        answer=_SAFE_UNCERTAINTY,
        citations=[],
        confidence=0.0,
        grounded_claims=[],
        unsupported_claims=[],
        source_count=0,
        status=AnswerGroundingStatus.INSUFFICIENT_EVIDENCE,
        validation=GroundingValidationResult(
            citation_coverage=0.0,
            accepted=False,
            unsupported_claim_count=0,
            reasons=["empty_or_irrelevant_evidence"],
        ),
        grounded=False,
    )


def _rejected_answer(
    candidate: GroundedAnswer,
    validation: GroundingValidationResult,
) -> GroundedAnswer:
    rejected_claims = list(dict.fromkeys(candidate.unsupported_claims + candidate.grounded_claims))
    return GroundedAnswer(
        answer=_SAFE_UNCERTAINTY,
        citations=[],
        confidence=0.0,
        grounded_claims=[],
        unsupported_claims=rejected_claims[:20],
        source_count=0,
        status=AnswerGroundingStatus.REJECTED,
        validation=validation,
        grounded=False,
    )


def _citation(chunk: RetrievedChunk, excerpt: str | None = None) -> Citation:
    quoted_excerpt = excerpt or _excerpt(chunk.content)
    return Citation(
        citation_id=chunk.citation_id,
        document_id=chunk.document_id,
        title=chunk.title,
        chunk_id=chunk.chunk_id,
        source=chunk.source,
        relevance_score=max(0.0, chunk.rerank_score or chunk.score),
        quoted_excerpt=quoted_excerpt,
    )


def _excerpt(content: str, limit: int = 320, *, focus: str | None = None) -> str:
    normalized = " ".join(content.split())
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    if focus == "timing":
        timing_terms = re.compile(
            r"\b(?:processing|review|settlement|business\s+days?|additional\s+days?|3[–-]5)\b",
            re.IGNORECASE,
        )
        first_sentence = next(
            (sentence for sentence in sentences if timing_terms.search(sentence)), sentences[0]
        )
    elif focus == "eligibility":
        eligibility_terms = re.compile(
            r"\b(?:eligible|qualify|considered|delivered|damaged|30\s+calendar\s+days?|returned)\b",
            re.IGNORECASE,
        )
        first_sentence = next(
            (sentence for sentence in sentences if eligibility_terms.search(sentence)), sentences[0]
        )
    else:
        first_sentence = sentences[0]
    bounded = first_sentence[:limit].rstrip()
    return bounded.rstrip(".!?")


def normalize_knowledge_query(query: str) -> str:
    """Map bounded multilingual/topic phrases to the stored evidence vocabulary."""

    normalized = " ".join(query.replace("İ", "I").casefold().split())
    if re.search(
        r"\b(?:para\s+ne\s+zaman\s+yatar|ne\s+zaman\s+(?:hesabıma|geçer)|"
        r"iade\s+süresi|geri\s+ödeme\s+süresi)\b",
        normalized,
    ):
        return "refund processing review settlement"
    if re.search(r"\b(?:iade\s+uygunluk\s+süresi|uygunluk\s+süresi)\b", normalized):
        return "refund eligibility delivered 30 calendar days"
    if re.search(r"\b(?:iade\s+şart|iade\s+koşul|geri\s+ödeme\s+şart)\w*\b", normalized):
        return "refund eligibility delivered damaged returned"
    if re.search(
        r"\b(?:processing\s+time|settlement|when\s+will.*refund|refund.*take)\b",
        normalized,
    ) or (
        re.search(r"\bhow\s+long\b", normalized)
        and re.search(r"\b(?:refund|return|reimburse|money\s+back)\b", normalized)
    ):
        return "refund processing review settlement"
    return query


def _query_focus(query: str) -> str | None:
    if "processing" in query or "settlement" in query or "review" in query:
        return "timing"
    if "eligibility" in query or "eligible" in query:
        return "eligibility"
    return None


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


_SUFFIXES = ("ments", "ment", "ings", "ing", "ed", "es", "s")


def _stem(word: str) -> str:
    """Light suffix stripping so shipped, shipping and shipment share one root."""

    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiouls":
        word = word[:-1]
    return word


def _terms(value: str) -> set[str]:
    terms: set[str] = set()
    for raw in _TOKEN.findall(value.casefold()):
        if raw in _STOP_WORDS or len(raw) < 3:
            continue
        terms.add(_stem(raw))
    return terms


def content_terms(value: str) -> set[str]:
    """Stemmed content words of free text, without stop words."""

    return _terms(value)


def evidence_terms(chunk: RetrievedChunk) -> set[str]:
    return _terms(" ".join((chunk.title, chunk.category, chunk.section, chunk.content)))


def query_term_overlap(query: str, terms: set[str]) -> float:
    """Share of the query's content terms that appear in the evidence terms."""

    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    return sum(_term_matches(term, terms) for term in query_terms) / len(query_terms)


def _is_relevant(query: str, chunk: RetrievedChunk) -> bool:
    query_terms = _terms(query)
    if not query_terms:
        return False
    score = chunk.rerank_score if chunk.rerank_score is not None else chunk.score
    if query_terms & _EVIDENCE_REFERENCE_TERMS:
        return score >= _MIN_RELEVANCE_SCORE
    chunk_terms = evidence_terms(chunk)
    covered = {term for term in query_terms if _term_matches(term, chunk_terms)}
    distinctive = query_terms - _GENERIC_QUERY_TERMS
    # A deterministic reranker can score a short, highly specific question
    # below the general threshold even when every distinctive term is present
    # in the source.  Permit that bounded case, but never let a low-confidence
    # generic query pass without stronger retrieval support.
    matched = {term for term in distinctive if _term_matches(term, chunk_terms)}
    if score < _MIN_RELEVANCE_SCORE:
        if score < 0.2 or not distinctive:
            return False
        return bool(covered) and matched == distinctive
    # With solid retrieval support, most (not every) distinctive term must appear:
    # customers phrase questions in words the policy text does not repeat verbatim.
    if not distinctive:
        return bool(covered)
    return bool(matched) and len(matched) / len(distinctive) >= 0.5


def _term_matches(term: str, evidence_terms: set[str]) -> bool:
    if term in evidence_terms:
        return True
    if len(term) < 5:
        return False
    return any(
        len(evidence_term) >= 5
        and (term.startswith(evidence_term[:5]) or evidence_term.startswith(term[:5]))
        for evidence_term in evidence_terms
    )


def _find_conflict(chunks: Sequence[RetrievedChunk]) -> tuple[RetrievedChunk, ...] | None:
    facts: dict[str, dict[str, RetrievedChunk]] = defaultdict(dict)
    for chunk in chunks:
        for value, unit in _QUANTIFIED_FACT.findall(chunk.content):
            normalized_unit = unit.casefold().rstrip("s")
            facts[normalized_unit].setdefault(value, chunk)
    for values in facts.values():
        if len(values) > 1:
            return tuple(list(values.values())[:2])
    return None


def _confidence(citations: Sequence[Citation]) -> float:
    if not citations:
        return 0.0
    scores = [min(citation.relevance_score, 1.0) for citation in citations]
    return round(sum(scores) / len(scores), 4)


def _record_grounding_observability(
    generation_span: object,
    answer: GroundedAnswer,
    retrieval_count: int,
) -> None:
    attributes = {
        "rag.grounding.status": answer.status.value,
        "rag.grounding.citation_coverage": answer.validation.citation_coverage,
        "rag.grounding.unsupported_claim_count": len(answer.unsupported_claims),
        "rag.grounding.retrieval_count": retrieval_count,
        "rag.grounding.answer_confidence": answer.confidence,
        "rag.grounding.accepted": answer.validation.accepted,
    }
    for key, value in attributes.items():
        generation_span.set_attribute(key, value)  # type: ignore[attr-defined]
    labels = {"status": answer.status.value}
    metrics = get_metrics()
    metrics.rag_grounding_citation_coverage.record(answer.validation.citation_coverage, labels)
    metrics.rag_grounding_unsupported_claim_count.record(len(answer.unsupported_claims), labels)
    metrics.rag_grounding_retrieval_count.record(retrieval_count, labels)
    metrics.rag_grounding_answer_confidence.record(answer.confidence, labels)
