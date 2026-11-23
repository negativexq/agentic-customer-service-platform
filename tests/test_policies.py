from datetime import UTC, datetime, timedelta

import pytest

from app.auth.models import ActorType, Principal
from app.core.context import ExecutionContext
from app.policies.confirmation import is_expired, parse_confirmation
from app.policies.engine import PolicyEngine
from app.policies.models import PendingAction, PolicyOutcome


def context(customer_id: int = 1) -> ExecutionContext:
    return ExecutionContext(
        request_id="req_test",
        conversation_id="conv_test",
        principal=Principal(
            actor_id="operator-test",
            actor_type=ActorType.SUPPORT_OPERATOR,
            roles=["support_operator"],
        ),
        effective_customer_id=customer_id,
    )


@pytest.mark.parametrize(
    "value",
    [
        "yes",
        "YES",
        " confirm ",
        "proceed",
        "do it",
        "yes please",
        "yes please proceed",
        "yes, please proceed with the refund",
        "yes, please proceed with the refund request",
        "I confirm",
        "proceed with the refund",
        "go ahead",
        "Yes, go ahead.",
        "OK, go ahead",
        "please continue",
        "approved",
        "evet",
        " EVET ",
        "onaylıyorum",
        "ONAYLIYORUM",
        "onayla",
        "devam   et",
        "işlemi onaylıyorum",
    ],
)
def test_confirmation_parser_accepts_bounded_confirmations(value: str) -> None:
    assert parse_confirmation(value) == "confirmed"


@pytest.mark.parametrize(
    "value",
    [
        "no",
        "cancel",
        "never mind",
        "don't do it",
        "don't proceed",
        "stop",
        "No, cancel it",
        "hayır",
        " HAYIR ",
        "iptal",
        "iptal et",
        "vazgeçtim",
        "onaylamıyorum",
    ],
)
def test_confirmation_parser_accepts_bounded_rejections(value: str) -> None:
    assert parse_confirmation(value) == "rejected"


def test_confirmation_parser_rejects_ambiguous_substitution_text() -> None:
    assert parse_confirmation("yes, and refund order 999") == "ambiguous"
    assert parse_confirmation("Okay, yes, cancel it.") == "confirmed"
    assert parse_confirmation("Sure, go ahead please") == "confirmed"
    assert parse_confirmation("tamam evet") == "confirmed"
    assert parse_confirmation("yes cancel order 5") == "ambiguous"
    assert parse_confirmation("okay but not now") == "ambiguous"
    assert parse_confirmation("evet ama başka siparişi de iptal et") == "ambiguous"
    assert parse_confirmation("Yes, but first what is your refund policy?") == "ambiguous"
    assert parse_confirmation("Yes, however, cancel my other order instead.") == "ambiguous"


def test_policy_engine_applies_registry_risk_deterministically() -> None:
    engine = PolicyEngine()
    assert (
        engine.evaluate(
            tool_name="get_order", context=context(), arguments={"customer_id": 1, "order_id": 2}
        ).outcome
        == PolicyOutcome.ALLOW
    )
    assert (
        engine.evaluate(
            tool_name="cancel_order",
            context=context(),
            arguments={"customer_id": 1, "order_id": 3},
        ).outcome
        == PolicyOutcome.REQUIRE_CONFIRMATION
    )
    assert (
        engine.evaluate(
            tool_name="escalate_to_human", context=context(), arguments={"customer_id": 1}
        ).outcome
        == PolicyOutcome.REQUIRE_HUMAN
    )


def test_policy_engine_denies_unknown_or_wrong_customer() -> None:
    engine = PolicyEngine()
    unknown = engine.evaluate(tool_name="not_registered", context=context(), arguments={})
    wrong_customer = engine.evaluate(
        tool_name="cancel_order",
        context=context(),
        arguments={"customer_id": 2, "order_id": 3},
    )
    assert unknown.outcome == PolicyOutcome.DENY
    assert "unknown_tool" in unknown.reasons
    assert wrong_customer.outcome == PolicyOutcome.DENY
    assert "ownership_required" in wrong_customer.reasons


def test_expiration_is_clock_driven() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    action = PendingAction(
        action_id="act_test",
        conversation_id="conv_test",
        actor_id="operator-test",
        actor_type=ActorType.SUPPORT_OPERATOR,
        effective_customer_id=1,
        tool_name="cancel_order",
        arguments={"customer_id": 1, "order_id": 3},
        risk_level=2,
        created_at=created,
    )
    assert not is_expired(action, created + timedelta(seconds=299), 300)
    assert is_expired(action, created + timedelta(seconds=300), 300)
