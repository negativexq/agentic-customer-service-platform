import re
from collections.abc import Callable

from app.agent.schemas import Intent
from app.agent.state import AgentState, SuspendedWorkflowState
from app.policies.confirmation import belongs_to_context
from app.policies.models import PendingActionStatus

_QUESTION_PREFIX = re.compile(
    r"^(?:what|when|where|why|how|who|which|can you|could you|would you|do you|"
    r"tell me|explain|show me|check|nedir|ne zaman|nerede|neden|nasıl|kim|hangi|"
    r"bana anlat|kontrol et)\b",
    re.IGNORECASE,
)
_CONFIRMATION_LIKE_PREFIX = re.compile(
    r"^(?:yes|no|confirm|proceed|approved|go ahead|stop|cancel|evet|hayır|"
    r"onay|devam et|iptal|vazgeç)",
    re.IGNORECASE,
)
_MIXED_REQUEST_MARKER = re.compile(
    r"\b(?:but|first|however|instead|actually|rather|ama|önce|ancak|yerine)\b",
    re.IGNORECASE,
)
_EXPLICIT_REPLACEMENT = re.compile(
    r"^(?:actually\s+)?(?:please\s+)?(?:cancel|refund|create|open|escalate|connect|"
    r"i want|i need)\b|\binstead\b",
    re.IGNORECASE,
)
_OVERRIDE_MARKER = re.compile(
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:your\s+)?"
    r"(?:previous|prior|earlier)\s+(?:instructions|rules)\b|"
    r"\b(?:bypass|override|skip)\s+(?:the\s+)?"
    r"(?:system\s+)?(?:confirmation|validation|policy)\b",
    re.IGNORECASE,
)
_PENDING_OVERRIDE_MARKER = re.compile(
    r"\b(?:ignore|disregard|bypass|override|skip)\b.*"
    r"\b(?:rules?|confirmation|validation|policy|safeguards?)\b",
    re.IGNORECASE,
)
_ROLE_OVERRIDE_MARKER = re.compile(
    r"\b(?:you\s+are|you're)\s+(?:now\s+)?(?:an?\s+)?admin\b|"
    r"\bdisable\s+(?:all\s+)?(?:the\s+)?safeguards?\b",
    re.IGNORECASE,
)
_IMPERSONATION_OVERRIDE_MARKER = re.compile(
    r"(?:^|[\n\[])\s*(?:system|developer|internal)(?:\s+message|\s+instruction)?\s*[:\]]|"
    r"\b(?:system|developer|internal)\s*:\s*|"
    r"\b(?:system|developer|internal)\s+(?:message|instruction)\b",
    re.IGNORECASE,
)
_BOUNDED_OVERRIDE_MARKER = re.compile(
    r"\b(?:this\s+is\s+a\s+test\s+environment|test\s+mode| safeguards?\s+do(?:es)?n['’]?t\s+apply|"
    r"safeguards?\s+(?:are\s+)?disabled|security\s+checks?\s+(?:are\s+)?disabled)\b|"
    r"\b(?:i['’]?m|i\s+am)\s+(?:the\s+)?(?:engineer|developer|architect)\b.*"
    r"\b(?:run|execute|approve|authorize|refund|cancel|proceed)\b|"
    r"\b(?:internal\s+operator|operator\s+override|internal\s+support\s+override)\b.*"
    r"\b(?:proceed|execute|approve|authorize|refund|cancel)\b|"
    r"\b(?:sistem\s+mesajı|sistem\s+talimatı|geliştirici\s+talimatı|"
    r"dahili\s+(?:operatör|destek)\s+(?:talimatı|geçersiz\s+kılma))\b.*"
    r"\b(?:kapat|devre\s+dışı\s+bırak|atla|bypass|onayla|uygula|iade|iptal)\b",
    re.IGNORECASE,
)
_TURKISH_OVERRIDE_MARKER = re.compile(
    r"\b(?:önceki\s+talimatları|güvenlik\s+kontrollerini|korumaları|onay\s+gereğini)\b.*"
    r"\b(?:yok\s+say|kapat|devre\s+dışı\s+bırak|atla|bypass|geç|onayla|uygula)\b",
    re.IGNORECASE,
)
_AUTHORITY_CLAIM_MARKER = re.compile(
    r"\b(?:company|support|manager|account)\b.*\b(?:permanently\s+authorized|"
    r"upgraded\s+to|already\s+approved|authorized\s+for|approved\s+all\s+future)\b.*"
    r"\b(?:automatic|unlimited|future|all|refund|action|approval|authorization)\b|"
    r"\b(?:permanently\s+authorized|upgraded\s+to\s+unlimited\s+refund\s+approval|"
    r"approved\s+all\s+future\s+actions?)\b|"
    r"\b(?:support|customer\s+service|manager|company)\b.*"
    r"\b(?:told|said|approved|authorized|pre[- ]?approved|granted)\b.*"
    r"\b(?:never\s+need|no\s+longer\s+need|without|automatic|unlimited|future|"
    r"confirmation|verification|refund|action|checks?)\b",
    re.IGNORECASE,
)
_REFUND_MARKER = re.compile(r"\b(?:refund|reimburse|money back)\b", re.IGNORECASE)
_CANCEL_MARKER = re.compile(r"\b(?:cancel|cancellation)\b", re.IGNORECASE)
_TURKISH_REFUND_MARKER = re.compile(r"\b(?:iade|geri\s+ödeme)\b", re.IGNORECASE)
_TURKISH_CANCEL_MARKER = re.compile(r"\b(?:iptal|iptali)\b", re.IGNORECASE)
_NEGATED_REFUND = re.compile(
    r"\b(?:do not|don't|do not want|don't want|not asking for)\s+(?:a\s+)?refund\b",
    re.IGNORECASE,
)
_KNOWLEDGE_MARKER = re.compile(
    r"\b(?:explain|how|what|policy|information|tell me|works?|possible)\b",
    re.IGNORECASE,
)
_CONFLICTING_ACTION_MARKER = re.compile(
    r"\b(?:cancel|submit|create|execute|approve|authorize)\b",
    re.IGNORECASE,
)
_CROSS_CUSTOMER_MARKER = re.compile(
    r"\b(?:another|other|different|previous|former)\s+customer\b|"
    r"\bsomeone\s+else(?:'s|s)?\b",
    re.IGNORECASE,
)
_CROSS_CUSTOMER_RESOURCE = re.compile(
    r"\b(?:order|ticket|details?|refund|approval|account|information|status|amount|existence)\b",
    re.IGNORECASE,
)
_INDIRECT_SCOPE_SUBJECT = re.compile(
    r"\b(?:my\s+(?:colleague|manager)|their|someone\s+else|they)\b",
    re.IGNORECASE,
)
_INDIRECT_SCOPE_AUTHORITY = re.compile(
    r"\b(?:said|told|gave\s+me\s+permission|authorized|approved|can\s+(?:see|access)|"
    r"permission|allowed)\b",
    re.IGNORECASE,
)
_RESUME_PHRASES = frozenset(
    {
        "continue",
        "resume",
        "continue my request",
        "resume my request",
        "continue the previous request",
        "resume the previous request",
        "continue with my refund",
        "continue with the refund",
        "continue my refund",
        "resume my refund",
        "resume the refund",
        "continue refund request",
        "resume refund request",
        "continue with my cancellation",
        "continue with the cancellation",
        "continue cancellation",
        "resume my cancellation",
        "resume the cancellation",
        "continue cancellation request",
        "resume cancellation request",
        "please continue",
        "proceed",
        "go ahead",
        "önceki isteğe devam et",
        "isteğime devam et",
        "iade işlemine devam et",
        "iptal işlemine devam et",
    }
)


def is_interruption_candidate(message: str) -> bool:
    """Identify explicit new-request shapes without interpreting their intent."""

    normalized = _normalize(message)
    if not normalized:
        return False
    if is_instruction_override_attempt(normalized):
        return True
    marker = _MIXED_REQUEST_MARKER.search(normalized)
    suffix = normalized[marker.end() :].strip() if marker is not None else ""
    suffix = re.sub(r"^(?:first|however|instead|actually|rather|önce|ancak|yerine)\s+", "", suffix)
    has_question = bool(
        message.strip().endswith("?")
        or _QUESTION_PREFIX.match(normalized)
        or (suffix and _QUESTION_PREFIX.match(suffix))
    )
    if _CONFIRMATION_LIKE_PREFIX.match(normalized):
        return bool(
            (marker and (has_question or _EXPLICIT_REPLACEMENT.search(suffix)))
            or _EXPLICIT_REPLACEMENT.search(normalized)
        )
    return bool(has_question or _EXPLICIT_REPLACEMENT.search(normalized) or marker)


def looks_like_confirmation(message: str) -> bool:
    """True when a reply opens like a yes/no answer, even if the rest is unclear."""

    return bool(_CONFIRMATION_LIKE_PREFIX.match(_normalize(message)))


def is_instruction_override_attempt(message: str) -> bool:
    """Recognize bounded authority-override language without fuzzy approval."""

    normalized = " ".join(message.casefold().split())
    if _BOUNDED_OVERRIDE_MARKER.search(normalized) or _TURKISH_OVERRIDE_MARKER.search(normalized):
        return True
    if _IMPERSONATION_OVERRIDE_MARKER.search(normalized) and re.search(
        r"\b(?:disable|skip|bypass|override|approve|authorize|execute|refund|cancel|"
        r"confirmation|validation|security|safeguards?)\b",
        normalized,
    ):
        return True
    if _ROLE_OVERRIDE_MARKER.search(normalized):
        return True
    if _OVERRIDE_MARKER.search(normalized):
        return bool(_REFUND_MARKER.search(normalized) or _CANCEL_MARKER.search(normalized))
    if _PENDING_OVERRIDE_MARKER.search(normalized):
        return bool(
            re.search(
                r"\b(?:refund|reimburse|cancel|execute|approve|authorize|proceed|action|tool)\b",
                normalized,
            )
        )
    return bool(
        _REFUND_MARKER.search(normalized)
        and re.search(r"\b(?:approve|authorize)\b.*\bwithout\s+confirmation\b", normalized)
    )


def is_authority_claim_attempt(message: str) -> bool:
    """Recognize bounded indirect authority claims before business routing."""

    return bool(_AUTHORITY_CLAIM_MARKER.search(_normalize(message)))


def is_memory_summary_request(message: str) -> bool:
    """Recognize the read-only customer memory summary command."""

    return _normalize(message) in {
        "what do you remember about me",
        "what information do you remember about me",
        "what preferences do you remember about me",
        "what do you remember",
    }


def has_conflicting_intents(message: str) -> bool:
    """Reject mixed knowledge/mutation requests instead of guessing a write route."""

    normalized = _normalize(message)
    if not _NEGATED_REFUND.search(normalized) or not _KNOWLEDGE_MARKER.search(normalized):
        return False
    remainder = _NEGATED_REFUND.sub("", normalized)
    return bool(_CONFLICTING_ACTION_MARKER.search(remainder))


def is_cross_customer_access_attempt(message: str) -> bool:
    """Recognize explicit requests to use another customer's scope."""

    normalized = _normalize(message)
    return bool(
        (
            _CROSS_CUSTOMER_MARKER.search(normalized)
            or (
                _INDIRECT_SCOPE_SUBJECT.search(normalized)
                and _INDIRECT_SCOPE_AUTHORITY.search(normalized)
            )
        )
        and _CROSS_CUSTOMER_RESOURCE.search(normalized)
    )


def is_resume_request(message: str) -> bool:
    """Recognize bounded resume commands; resumption never confirms the action."""

    return _normalize(message) in _RESUME_PHRASES


def explicit_replacement_intent(
    message: str, previous_intent: Intent | str | None
) -> Intent | None:
    """Return a bounded replacement intent for an active opposite mutation.

    This is workflow routing, not semantic authority. It only applies when the
    customer explicitly names the opposite mutation and the existing workflow
    supplies the prior target for the normal compiler and policy gates.
    """

    normalized = _normalize(message)
    try:
        previous = Intent(previous_intent) if previous_intent is not None else None
    except ValueError:
        previous = None
    if (
        previous == Intent.ORDER_CANCEL
        and (_REFUND_MARKER.search(normalized) or _TURKISH_REFUND_MARKER.search(normalized))
        and re.search(r"\b(?:instead|rather|let['’]?s|no|hayır|yerine|iade)\b", normalized)
    ):
        return Intent.REFUND_REQUEST
    if (
        previous == Intent.REFUND_REQUEST
        and (_CANCEL_MARKER.search(normalized) or _TURKISH_CANCEL_MARKER.search(normalized))
        and re.search(r"\b(?:instead|rather|let['’]?s|no|hayır|yerine|iptal)\b", normalized)
    ):
        return Intent.ORDER_CANCEL
    return None


def make_handle_workflow_interruption_node() -> Callable[[AgentState], AgentState]:
    def handle_workflow_interruption(state: AgentState) -> AgentState:
        previous_intent = _workflow_intent(state)
        interruption_intent = _current_intent(state)
        if (
            previous_intent is None
            or interruption_intent in {None, Intent.UNKNOWN}
            or interruption_intent == previous_intent
        ):
            return {
                "intent": previous_intent or Intent.UNKNOWN,
                "semantic_decision": None,
                "selected_tool": None,
                "tool_arguments": {},
                "confirmation_status": (
                    "ambiguous" if state.get("pending_action") is not None else None
                ),
                "workflow_interruption_pending": False,
                "workflow_interruption_status": "continued",
                "workflow_state": (
                    "waiting_confirmation" if state.get("pending_action") is not None else "active"
                ),
            }
        snapshot = _capture_workflow(state, previous_intent)
        superseded = _is_superseding_request(state)
        previous_workflow_id = snapshot.get("workflow_id") or _workflow_id(state)
        new_workflow_id = (
            f"workflow:{state['agent_run_id']}" if superseded else state.get("workflow_id")
        )
        if superseded:
            snapshot["source_state"] = "superseded"
            snapshot["superseded_by"] = new_workflow_id
        return {
            "pending_action": None,
            "action_id": None,
            "pending_workflow_decision": None,
            "missing_required_fields": [],
            "collected_entities": {},
            "workflow_active": False,
            "workflow_interruption_pending": False,
            "workflow_interruption_status": "superseded" if superseded else "suspended",
            "workflow_state": "superseded" if superseded else "suspended",
            "previous_workflow_intent": previous_intent,
            "interruption_intent": interruption_intent,
            "suspended_workflow": None if superseded else snapshot,
            "superseded_workflow": snapshot if superseded else None,
            "workflow_id": new_workflow_id,
            "previous_workflow_id": previous_workflow_id,
            "superseded_by": new_workflow_id if superseded else None,
            "workflow_transition": (
                "waiting_confirmation_to_superseded"
                if superseded and state.get("pending_action") is not None
                else "active_to_superseded"
                if superseded
                else "waiting_confirmation_to_suspended"
                if state.get("pending_action") is not None
                else "active_to_suspended"
            ),
            "workflow_interruption_type": (
                "explicit_replacement" if superseded else "temporary_request"
            ),
            "confirmation_status": "interrupted",
        }

    return handle_workflow_interruption


def make_restore_suspended_workflow_node() -> Callable[[AgentState], AgentState]:
    def restore_suspended_workflow(state: AgentState) -> AgentState:
        snapshot = state.get("suspended_workflow")
        if snapshot is None:
            return {
                "workflow_interruption_status": "resume_unavailable",
                "workflow_resume_status": "reset",
                "workflow_resume_source": "explicit_user_resume",
            }
        pending_action = snapshot.get("pending_action")
        context = state.get("execution_context")
        if pending_action is not None and (
            context is None or not belongs_to_context(pending_action, context)
        ):
            return {
                "workflow_interruption_status": "resume_denied",
                "workflow_resume_status": "reset",
                "workflow_resume_source": "explicit_user_resume",
            }
        restored_count = _restored_fields_count(snapshot)
        common: AgentState = {
            "intent": snapshot["intent"],
            "collected_entities": dict(snapshot.get("collected_entities", {})),
            "tool_arguments": dict(snapshot.get("tool_arguments", {})),
            "workflow_interruption_pending": False,
            "workflow_interruption_status": "resumed",
            "workflow_resume_status": "resumed",
            "workflow_resume_source": "explicit_user_resume",
            "suspended_workflow": None,
            "workflow_id": snapshot.get("workflow_id") or state.get("workflow_id"),
            "previous_workflow_id": state.get("workflow_id"),
            "superseded_by": None,
            "workflow_transition": "suspended_to_resumed",
            "workflow_interruption_type": "explicit_resume",
            "restored_fields_count": restored_count,
            "error_category": None,
            "last_error": None,
        }
        validation = snapshot.get("validation_context", {})
        common.update(
            {
                "grounding_status": str(validation.get("grounding_status", "not_recorded")),
                "grounding_reference_type": _optional_string(
                    validation.get("grounding_reference_type")
                ),
                "grounding_trusted_source": _optional_string(
                    validation.get("grounding_trusted_source")
                ),
                "target_validation_status": str(
                    validation.get("target_validation_status", "not_recorded")
                ),
            }
        )
        if pending_action is not None:
            common.update(
                {
                    "pending_action": pending_action,
                    "pending_workflow_decision": None,
                    "missing_required_fields": [],
                    "workflow_active": False,
                    "workflow_state": "waiting_confirmation",
                    "confirmation_status": "resumed",
                    "selected_tool": pending_action.tool_name,
                    "action_id": pending_action.action_id,
                }
            )
            return common
        common.update(
            {
                "pending_action": None,
                "pending_workflow_decision": snapshot.get("pending_workflow_decision"),
                "missing_required_fields": list(snapshot.get("missing_required_fields", [])),
                "workflow_active": True,
                "workflow_state": "active",
                "confirmation_status": None,
                "selected_tool": None,
            }
        )
        return common

    return restore_suspended_workflow


def _capture_workflow(state: AgentState, intent: Intent) -> SuspendedWorkflowState:
    action = state.get("pending_action")
    workflow_id = _workflow_id(state)
    if action is not None:
        return {
            "intent": intent,
            "pending_action": action,
            "pending_workflow_decision": None,
            "collected_entities": dict(action.collected_entities),
            "tool_arguments": dict(action.arguments),
            "missing_required_fields": [],
            "validation_context": dict(action.validation_context),
            "policy_inputs": dict(action.policy_inputs),
            "source_state": "waiting_confirmation",
            "workflow_id": workflow_id,
            "superseded_by": None,
        }
    return {
        "intent": intent,
        "pending_action": None,
        "pending_workflow_decision": state.get("pending_workflow_decision"),
        "collected_entities": dict(state.get("collected_entities", {})),
        "tool_arguments": dict(state.get("workflow_tool_arguments", {})),
        "missing_required_fields": list(state.get("missing_required_fields", [])),
        "validation_context": dict(state.get("workflow_validation_context", {})),
        "policy_inputs": dict(state.get("workflow_policy_inputs", {})),
        "source_state": "active",
        "workflow_id": workflow_id,
        "superseded_by": None,
    }


def _is_superseding_request(state: AgentState) -> bool:
    decision = state.get("semantic_decision")
    if decision is None:
        return False
    # Knowledge-only interruptions should be resumable.  Some providers classify
    # an FAQ request as ``knowledge_and_action`` because it mentions the same
    # business object as the pending workflow; the intent is still the safer
    # source of truth for whether the old workflow is being replaced.
    if decision.intent in {
        Intent.REFUND_POLICY,
        Intent.CANCELLATION_POLICY,
        Intent.SHIPPING_POLICY,
        Intent.SUPPORT_FAQ,
        Intent.REFUND_ELIGIBILITY,
        Intent.CANCELLATION_EXPLANATION,
        Intent.CAPABILITY_QUESTION,
    }:
        return False
    return decision.request_type.value in {
        "write_action",
        "action_only",
        "knowledge_and_action",
        "escalation",
    }


def _workflow_id(state: AgentState) -> str:
    existing = state.get("workflow_id")
    if existing:
        return existing
    action = state.get("pending_action")
    if action is not None:
        return action.action_id
    return f"workflow:{state['agent_run_id']}"


def _workflow_intent(state: AgentState) -> Intent | None:
    action = state.get("pending_action")
    if action is not None and action.status == PendingActionStatus.PENDING and action.intent:
        try:
            return Intent(action.intent)
        except ValueError:
            return None
    pending_decision = state.get("pending_workflow_decision")
    if pending_decision is not None:
        return pending_decision.intent
    return state.get("previous_intent")


def _current_intent(state: AgentState) -> Intent | None:
    decision = state.get("semantic_decision")
    if decision is not None:
        return decision.intent
    return state.get("intent")


def _restored_fields_count(snapshot: SuspendedWorkflowState) -> int:
    return sum(
        bool(snapshot.get(field))
        for field in (
            "intent",
            "pending_action",
            "pending_workflow_decision",
            "collected_entities",
            "tool_arguments",
            "missing_required_fields",
            "validation_context",
            "policy_inputs",
        )
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _normalize(message: str) -> str:
    normalized = message.translate(str.maketrans("Iİ", "ıi")).casefold()
    normalized = re.sub(r"[,.!?;:]+", " ", normalized)
    return " ".join(normalized.split())
