from app.rag.answer_generator import GroundedAnswerGenerator, GroundingValidator
from app.rag.schemas import (
    AnswerGroundingStatus,
    Citation,
    GroundedAnswer,
    GroundingValidationResult,
    RetrievedChunk,
)


def _chunk(
    content: str, *, document_id: str = "refund-policy", score: float = 0.9
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{document_id}#eligibility#0",
        document_id=document_id,
        title="Refund Policy",
        category="refund",
        section="eligibility",
        source=f"knowledge/{document_id}.md",
        content=content,
        score=score,
    )


def test_generated_factual_claims_are_covered_by_retrieved_citations() -> None:
    evidence = _chunk("Damaged products may qualify for refund review within 30 days.")

    answer = GroundedAnswerGenerator().answer("refund policy for damaged products", [evidence])

    assert answer.status == AnswerGroundingStatus.PASS
    assert answer.validation.accepted is True
    assert answer.validation.citation_coverage == 1.0
    assert answer.citations[0].chunk_id == evidence.chunk_id
    assert answer.citations[0].quoted_excerpt in evidence.content
    assert answer.source_count == 1
    assert answer.unsupported_claims == []


def test_processing_time_query_selects_processing_evidence_not_eligibility_window() -> None:
    evidence = _chunk(
        "Customers should request a refund within 30 calendar days of delivery. "
        "Once a request is accepted for processing, the review normally takes 3–5 business days. "
        "Payment-provider settlement may require additional business days after approval."
    )

    answer = GroundedAnswerGenerator().answer("How long does a refund normally take?", [evidence])

    assert answer.validation.accepted is True
    assert "3–5 business days" in answer.answer
    assert "30 calendar days" not in answer.answer


def test_generic_how_long_query_keeps_non_refund_topic() -> None:
    evidence = RetrievedChunk(
        chunk_id="warranty-policy#section#0",
        document_id="warranty-policy",
        title="Warranty Policy",
        category="policy",
        section="coverage",
        source="knowledge/warranty-policy.md",
        content="The standard warranty lasts 12 months.",
        score=0.9,
    )

    answer = GroundedAnswerGenerator().answer("How long is the warranty?", [evidence])

    assert answer.status == AnswerGroundingStatus.PASS
    assert answer.validation.accepted is True
    assert "12 months" in answer.answer


def test_turkish_refund_conditions_use_equivalent_english_evidence() -> None:
    evidence = _chunk(
        "Refund requests are generally considered after an order has been delivered. "
        "A delivered order may qualify when the item is damaged or returned under an "
        "applicable service promise."
    )

    answer = GroundedAnswerGenerator().answer("İade şartlarınız nelerdir?", [evidence])

    assert answer.validation.accepted is True
    assert "delivered" in answer.answer


def test_irrelevant_retrieval_fails_closed_without_repeating_evidence() -> None:
    evidence = _chunk("Refund requests require an eligible order.")

    answer = GroundedAnswerGenerator().answer("What is the CEO phone number?", [evidence])

    assert answer.status == AnswerGroundingStatus.INSUFFICIENT_EVIDENCE
    assert answer.validation.accepted is False
    assert answer.citations == []
    assert answer.confidence == 0.0
    assert "phone" not in answer.answer.casefold()


def test_low_confidence_evidence_fails_closed() -> None:
    evidence = _chunk("Refund requests require an eligible order.", score=0.2)

    answer = GroundedAnswerGenerator().answer("What is the refund policy?", [evidence])

    assert answer.status == AnswerGroundingStatus.INSUFFICIENT_EVIDENCE
    assert answer.validation.accepted is False
    assert answer.citations == []


def test_unrelated_faq_evidence_does_not_answer_unsupported_question() -> None:
    evidence = _chunk(
        "Refund requests are reviewed after delivery.",
        document_id="refund-faq",
    )

    answer = GroundedAnswerGenerator().answer("Do you support cryptocurrency refunds?", [evidence])

    assert answer.status == AnswerGroundingStatus.INSUFFICIENT_EVIDENCE
    assert answer.validation.accepted is False
    assert answer.citations == []
    assert "not contain enough information" in answer.answer


def test_conflicting_evidence_is_surfaced_without_silent_selection() -> None:
    evidence = [
        _chunk("Refund requests are allowed within 30 days.", document_id="refund-v3"),
        _chunk("Refund requests are allowed within 14 days.", document_id="refund-v2"),
    ]

    answer = GroundedAnswerGenerator().answer(
        "How many days does the refund policy allow?", evidence
    )

    assert answer.status == AnswerGroundingStatus.CONFLICT
    assert answer.validation.accepted is True
    assert len(answer.citations) == 2
    assert "conflict" in answer.answer.casefold()
    assert "30 days" in answer.answer
    assert "14 days" in answer.answer


def test_validator_rejects_uncited_claim_and_mismatched_excerpt() -> None:
    evidence = _chunk("Refund requests require an eligible order.")
    candidate = GroundedAnswer(
        answer="Refunds are always automatic.",
        citations=[
            Citation(
                citation_id=evidence.citation_id,
                document_id=evidence.document_id,
                title=evidence.title,
                chunk_id=evidence.chunk_id,
                source=evidence.source,
                relevance_score=evidence.score,
                quoted_excerpt="This text was not retrieved.",
            )
        ],
        confidence=0.9,
        grounded_claims=["Refunds are always automatic"],
        unsupported_claims=[],
        source_count=1,
        status=AnswerGroundingStatus.PASS,
        validation=GroundingValidationResult(
            citation_coverage=0.0,
            accepted=False,
            unsupported_claim_count=0,
        ),
    )

    validation = GroundingValidator().validate(candidate, [evidence])

    assert validation.accepted is False
    assert validation.citation_coverage == 0.0
    assert validation.unsupported_claim_count == 1
    assert "citation_excerpt_mismatch" in validation.reasons


def test_validator_rejects_answer_mismatch_even_with_valid_citation_marker() -> None:
    evidence = _chunk("Refund requests require an eligible order.")
    claim = "Refunds are always automatic"
    candidate = GroundedAnswer(
        answer=f"{claim} [{evidence.citation_id}].",
        citations=[
            Citation(
                citation_id=evidence.citation_id,
                document_id=evidence.document_id,
                title=evidence.title,
                chunk_id=evidence.chunk_id,
                source=evidence.source,
                relevance_score=evidence.score,
                quoted_excerpt="Refund requests require an eligible order",
            )
        ],
        confidence=0.9,
        grounded_claims=[claim],
        unsupported_claims=[],
        source_count=1,
        status=AnswerGroundingStatus.PASS,
        validation=GroundingValidationResult(
            citation_coverage=0.0,
            accepted=False,
            unsupported_claim_count=0,
        ),
    )

    validation = GroundingValidator().validate(candidate, [evidence])

    assert validation.accepted is False
    assert validation.unsupported_claim_count == 1
    assert "answer_evidence_mismatch" in validation.reasons


def test_paraphrased_question_matches_policy_wording() -> None:
    evidence = _chunk(
        "After an order has shipped it can no longer be cancelled; request a return instead.",
        document_id="cancellation-policy",
        score=0.44,
    )

    answer = GroundedAnswerGenerator().answer(
        "Can I cancel an order after it has shipped?", [evidence]
    )

    assert answer.status == AnswerGroundingStatus.PASS
    assert answer.citations[0].chunk_id == evidence.chunk_id


def test_partial_overlap_on_unrelated_topic_still_fails_closed() -> None:
    evidence = _chunk("After an order has shipped it can no longer be cancelled.", score=0.44)

    answer = GroundedAnswerGenerator().answer("Do you sell gift cards?", [evidence])

    assert answer.status == AnswerGroundingStatus.INSUFFICIENT_EVIDENCE


def test_shared_account_vocabulary_does_not_answer_a_different_topic() -> None:
    evidence = _chunk(
        "Plan changes on your account take effect at the next renewal.",
        document_id="subscription-policy",
        score=0.67,
    )

    answer = GroundedAnswerGenerator().answer(
        "How do I change the email on my account?", [evidence]
    )

    assert answer.status == AnswerGroundingStatus.INSUFFICIENT_EVIDENCE
