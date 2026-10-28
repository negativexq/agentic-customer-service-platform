"""Deterministic compilation of semantic proposals into typed runtime decisions."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.compiler_contracts import CompilerField, CompilerReasonCode
from app.agent.schemas import (
    AgentRequestType,
    Intent,
    SemanticDecision,
    SemanticTarget,
)
from app.agent.semantic_attribution import (
    CompilerClarificationCause,
    RefundReasonSupportStatus,
)
from app.agent.semantic_grounding import SemanticGrounding
from app.agent.target_admissibility import (
    TargetAdmissibility,
    assess_target_admissibility,
)
from app.core.context import ExecutionContext
from app.memory.schemas import MemoryCandidate
from app.models import Order
from app.models.entities import EscalationPriority


class CompileStatus(StrEnum):
    COMPILED_ACTION = "compiled_action"
    CLARIFICATION_REQUIRED = "clarification_required"
    NO_ACTION = "no_action"
    COMPILE_REJECTED = "compile_rejected"


COMPILER_REASON_STATUSES: Final[dict[CompilerReasonCode, CompileStatus]] = {
    CompilerReasonCode.COMPILED_ACTION: CompileStatus.COMPILED_ACTION,
    CompilerReasonCode.KNOWLEDGE_ONLY: CompileStatus.NO_ACTION,
    CompilerReasonCode.MEMORY_ACTION: CompileStatus.NO_ACTION,
    CompilerReasonCode.INVALID_ESCALATION_PRIORITY: CompileStatus.COMPILE_REJECTED,
    CompilerReasonCode.TARGET_NOT_ALLOWED: CompileStatus.COMPILE_REJECTED,
    CompilerReasonCode.UNROUTED_INTENT: CompileStatus.COMPILE_REJECTED,
    CompilerReasonCode.UNSUPPORTED_ACTION: CompileStatus.COMPILE_REJECTED,
    CompilerReasonCode.UNSUPPORTED_READ: CompileStatus.COMPILE_REJECTED,
    CompilerReasonCode.WRONG_TARGET_TYPE: CompileStatus.COMPILE_REJECTED,
    CompilerReasonCode.CONTRADICTORY_ACTION: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.ESCALATION_DETAILS_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.KNOWLEDGE_QUERY_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.LATEST_ORDER_NOT_GROUNDED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.MEMORY_CANDIDATE_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.MEMORY_KEY_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.ORDER_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.ORDER_UNRESOLVED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.REFUND_REASON_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.REFUND_REASON_UNSUPPORTED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.REQUEST_CLARIFICATION_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.TARGET_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.TICKET_DETAILS_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.TICKET_REQUEST_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
    CompilerReasonCode.TICKET_REQUIRED: CompileStatus.CLARIFICATION_REQUIRED,
}


class CompiledDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CompileStatus
    intent: Intent
    request_type: AgentRequestType
    selected_tool: str | None = None
    tool_arguments: dict[str, object] = Field(default_factory=dict)
    requires_retrieval: bool = False
    knowledge_query: str | None = None
    memory_candidate: MemoryCandidate | None = None
    memory_key: str | None = None
    reason: str = ""
    rejection_reason: str | None = None
    refund_reason_support_status: str = RefundReasonSupportStatus.NOT_APPLICABLE.value
    refund_reason_validation_invoked: bool = False
    compiler_clarification_cause: str | None = None
    reason_code: CompilerReasonCode = CompilerReasonCode.NOT_RECORDED
    missing_fields: list[CompilerField] = Field(default_factory=list, max_length=10)
    clarification_fields: list[CompilerField] = Field(
        default_factory=list,
        max_length=10,
        validation_alias=AliasChoices("clarification_fields", "missing_required_fields"),
    )

    @model_validator(mode="after")
    def validate_contract(self) -> CompiledDecision:
        for fields in (self.missing_fields, self.clarification_fields):
            if len(fields) != len(set(fields)):
                raise ValueError("compiler fields must be unique")
        if not set(self.missing_fields) <= set(self.clarification_fields):
            raise ValueError("missing fields must require clarification")
        if self.status != CompileStatus.CLARIFICATION_REQUIRED and self.clarification_fields:
            raise ValueError("only clarification results may request fields")
        if self.status == CompileStatus.COMPILED_ACTION:
            if not self.selected_tool:
                raise ValueError("compiled actions require a selected tool")
        elif self.selected_tool is not None or self.tool_arguments:
            raise ValueError("non-action outcomes cannot carry executable arguments")
        expected = COMPILER_REASON_STATUSES.get(self.reason_code)
        if self.reason_code != CompilerReasonCode.NOT_RECORDED and expected != self.status:
            raise ValueError("compiler reason code does not match outcome")
        return self


class BusinessTargetResolver:
    """Read-only, customer-scoped resolution of symbolic semantic references."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve_order_id(
        self, target: SemanticTarget, customer_id: int, tenant_id: str = "default"
    ) -> int | None:
        if target.type == "explicit_order":
            return target.order_id
        if target.type != "latest_order":
            return None
        order = self.session.scalar(
            select(Order)
            .where(Order.customer_id == customer_id, Order.tenant_id == tenant_id)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(1)
        )
        return order.id if order is not None else None


ACTION_TOOLS: Final[dict[Intent, str]] = {
    Intent.ORDER_CANCEL: "cancel_order",
    Intent.REFUND_REQUEST: "request_refund",
    Intent.TICKET_CREATE: "create_support_ticket",
    Intent.HUMAN_ESCALATION: "escalate_to_human",
}

READ_TOOLS: Final[dict[Intent, str]] = {
    Intent.CUSTOMER_LOOKUP: "get_customer",
    Intent.ORDER_LOOKUP: "get_order",
    Intent.ORDER_LIST: "get_customer_orders",
    Intent.TICKET_LOOKUP: "get_ticket",
    Intent.TICKET_LIST: "get_customer_tickets",
}

KNOWLEDGE_INTENTS: Final[frozenset[Intent]] = frozenset(
    {
        Intent.CAPABILITY_QUESTION,
        Intent.REFUND_POLICY,
        Intent.CANCELLATION_POLICY,
        Intent.SHIPPING_POLICY,
        Intent.SUPPORT_FAQ,
    }
)

KNOWLEDGE_AND_ACTION_INTENTS: Final[frozenset[Intent]] = frozenset(
    {Intent.REFUND_ELIGIBILITY, Intent.CANCELLATION_EXPLANATION}
)
KNOWLEDGE_QUERIES: Final[dict[Intent, str]] = {
    Intent.REFUND_ELIGIBILITY: "refund eligibility policy",
    Intent.CANCELLATION_EXPLANATION: "cancellation after shipment",
}
_TICKET_REQUEST_MARKERS = frozenset(
    {"ticket", "support", "open", "create", "record", "kayıt", "destek", "aç", "oluştur"}
)
_GENERIC_REASON_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "arrived",
        "because",
        "for",
        "i",
        "item",
        "it",
        "my",
        "need",
        "number",
        "of",
        "order",
        "please",
        "refund",
        "request",
        "the",
        "to",
        "want",
        "bir",
        "ben",
        "benim",
        "geldi",
        "geldiği",
        "iade",
        "iadesi",
        "için",
        "lütfen",
        "numaralı",
        "para",
        "sipariş",
        "siparişe",
        "siparişi",
        "yap",
        "yapın",
    }
)
_LATEST_ORDER_MARKERS = frozenset(
    {
        "latest",
        "last order",
        "most recent",
        "recent order",
        "recent orders",
        "en son",
        "son sipariş",
        "son siparişler",
        "en yeni",
        "yakın tarihli",
    }
)
_CONTRADICTORY_CANCEL_MARKERS = frozenset(
    {
        "don't cancel",
        "do not cancel",
        "no cancel",
        "no, cancel",
        "never cancel",
        "not cancel",
        "no longer cancel",
        "not sure",
        "uncertain",
        "maybe",
        "cancel etmeyin",
        "hayır iptal",
        "hayır, iptal",
        "iptal etme",
        "iptal etmeyin",
        "iptal etmek istemiyorum",
        "vazgeç",
        "emin değilim",
        "kararsız",
        "keep it active",
        "keep it open",
        "aktif kalsın",
        "açık kalsın",
    }
)
_UNSUPPORTED_REASON_INSTRUCTION_MARKERS = frozenset(
    {
        "make up a reason",
        "invent a reason",
        "fabricate a reason",
        "bir neden uydur",
        "neden uydur",
        "bir sebep uydur",
        "sebep uydur",
    }
)

MEMORY_INTENTS: Final[frozenset[Intent]] = frozenset({Intent.MEMORY_REMEMBER, Intent.MEMORY_FORGET})

SEMANTIC_INTENT_ROUTES: Final[dict[Intent, str]] = {
    **{intent: "action" for intent in ACTION_TOOLS},
    **{intent: "read" for intent in READ_TOOLS},
    **{intent: "knowledge" for intent in KNOWLEDGE_INTENTS},
    **{intent: "knowledge_and_action" for intent in KNOWLEDGE_AND_ACTION_INTENTS},
    **{intent: "memory" for intent in MEMORY_INTENTS},
    Intent.UNKNOWN: "clarification",
}


class DecisionCompiler:
    def __init__(self, resolver: BusinessTargetResolver) -> None:
        self.resolver = resolver

    def compile(
        self,
        decision: SemanticDecision,
        context: ExecutionContext,
        *,
        grounding: SemanticGrounding | None = None,
        user_message: str = "",
        restored_action: bool = False,
    ) -> CompiledDecision:
        route = SEMANTIC_INTENT_ROUTES.get(decision.intent)
        if route is None:
            return self._rejected(
                decision,
                "Semantic intent has no compiler route.",
                reason_code=CompilerReasonCode.UNROUTED_INTENT,
            )
        # Knowledge-only requests never authorize a mutation.  A provider may
        # carry forward a conversational target while answering an FAQ; that
        # target is irrelevant to read-only compilation and must not turn a
        # grounded answer into an invalid tool-argument failure.
        if route == "knowledge":
            return self._compile_knowledge(decision)
        admissibility = assess_target_admissibility(decision.intent, decision.target, grounding)
        if admissibility in {
            TargetAdmissibility.REQUIRES_CLARIFICATION,
            TargetAdmissibility.INVALID,
        }:
            return self._clarification(
                decision,
                "The request needs a specific, authorized target.",
                reason_code=CompilerReasonCode.TARGET_REQUIRED,
                clarification_fields=_target_fields(decision),
            )
        if route == "action":
            if decision.intent == Intent.ORDER_CANCEL and self._is_contradictory_cancel(
                user_message
            ):
                return self._clarification(
                    decision,
                    "The cancellation request is contradictory and needs clarification.",
                    reason_code=CompilerReasonCode.CONTRADICTORY_ACTION,
                    clarification_fields=(),
                )
            return self._compile_action(
                decision, context, user_message, restored_action=restored_action
            )
        if route == "read":
            return self._compile_read(decision, context, user_message)
        if route == "knowledge_and_action":
            return self._compile_knowledge_and_action(decision, context)
        if route == "memory":
            return self._compile_memory(decision)
        return self._clarification(
            decision,
            "The request needs clarification.",
            reason_code=CompilerReasonCode.REQUEST_CLARIFICATION_REQUIRED,
            clarification_fields=(),
        )

    def _compile_action(
        self,
        decision: SemanticDecision,
        context: ExecutionContext,
        user_message: str,
        *,
        restored_action: bool = False,
    ) -> CompiledDecision:
        tool = ACTION_TOOLS[decision.intent]
        if decision.intent in {Intent.ORDER_CANCEL, Intent.REFUND_REQUEST}:
            if self._wrong_order_target(decision.target):
                return self._rejected(
                    decision,
                    "Order action received a ticket target.",
                    reason_code=CompilerReasonCode.WRONG_TARGET_TYPE,
                )
            order_id = self._order_target(
                decision.target, context.effective_customer_id, context.tenant_id
            )
            if order_id is None:
                return self._clarification(
                    decision,
                    "A specific order is required.",
                    reason_code=CompilerReasonCode.ORDER_REQUIRED,
                    clarification_fields=(CompilerField.ORDER_ID,),
                )
            if decision.intent == Intent.ORDER_CANCEL:
                return self._action(decision, context, tool, {"order_id": order_id})
            if not decision.reason:
                return self._clarification(
                    decision,
                    "A refund reason is required.",
                    refund_reason_support_status=RefundReasonSupportStatus.MISSING,
                    compiler_clarification_cause=CompilerClarificationCause.MISSING_REFUND_REASON,
                    reason_code=CompilerReasonCode.REFUND_REASON_REQUIRED,
                    clarification_fields=(CompilerField.REASON,),
                )
            if not restored_action and not self._reason_is_user_supported(
                decision.reason, user_message
            ):
                return self._clarification(
                    decision,
                    "The refund reason must come from the customer request.",
                    refund_reason_support_status=RefundReasonSupportStatus.UNSUPPORTED,
                    refund_reason_validation_invoked=True,
                    compiler_clarification_cause=CompilerClarificationCause.UNSUPPORTED_REFUND_REASON,
                    reason_code=CompilerReasonCode.REFUND_REASON_UNSUPPORTED,
                    clarification_fields=(CompilerField.REASON,),
                )
            return self._action(
                decision,
                context,
                tool,
                {"order_id": order_id, "reason": decision.reason},
                refund_reason_support_status=RefundReasonSupportStatus.SUPPORTED,
                refund_reason_validation_invoked=True,
            )
        if decision.intent == Intent.TICKET_CREATE:
            if not decision.category or not decision.description:
                return self._clarification(
                    decision,
                    "Ticket category and description are required.",
                    reason_code=CompilerReasonCode.TICKET_DETAILS_REQUIRED,
                    clarification_fields=_absent_fields(
                        decision, (CompilerField.CATEGORY, CompilerField.DESCRIPTION)
                    ),
                )
            if user_message and not self._contains_ticket_request(user_message):
                return self._clarification(
                    decision,
                    "The customer must explicitly request a support ticket.",
                    reason_code=CompilerReasonCode.TICKET_REQUEST_REQUIRED,
                    clarification_fields=(),
                )
            order_id = None
            if decision.target is not None:
                if self._wrong_order_target(decision.target):
                    return self._rejected(
                        decision,
                        "Ticket creation received a ticket target.",
                        reason_code=CompilerReasonCode.WRONG_TARGET_TYPE,
                    )
                order_id = self._order_target(
                    decision.target, context.effective_customer_id, context.tenant_id
                )
                if order_id is None:
                    return self._clarification(
                        decision,
                        "The referenced order could not be resolved.",
                        reason_code=CompilerReasonCode.ORDER_UNRESOLVED,
                        clarification_fields=(CompilerField.ORDER_ID,),
                    )
            return self._action(
                decision,
                context,
                tool,
                {
                    "order_id": order_id,
                    "category": decision.category,
                    "description": decision.description,
                },
            )
        if decision.intent == Intent.HUMAN_ESCALATION:
            if not decision.reason or not decision.priority or not decision.summary:
                return self._clarification(
                    decision,
                    "Escalation details are required.",
                    reason_code=CompilerReasonCode.ESCALATION_DETAILS_REQUIRED,
                    clarification_fields=_absent_fields(
                        decision,
                        (CompilerField.REASON, CompilerField.PRIORITY, CompilerField.SUMMARY),
                    ),
                )
            try:
                priority = EscalationPriority(decision.priority).value
            except ValueError:
                return self._rejected(
                    decision,
                    "Unsupported escalation priority.",
                    reason_code=CompilerReasonCode.INVALID_ESCALATION_PRIORITY,
                )
            escalated_order_id: int | None = None
            ticket_id: int | None = None
            if decision.target is not None:
                if decision.target.type in {"explicit_order", "latest_order"}:
                    escalated_order_id = self._order_target(
                        decision.target, context.effective_customer_id, context.tenant_id
                    )
                    if escalated_order_id is None:
                        return self._clarification(
                            decision,
                            "The referenced order could not be resolved.",
                            reason_code=CompilerReasonCode.ORDER_UNRESOLVED,
                            clarification_fields=(CompilerField.ORDER_ID,),
                        )
                elif decision.target.type == "explicit_ticket":
                    ticket_id = decision.target.ticket_id
            return self._action(
                decision,
                context,
                tool,
                {
                    "ticket_id": ticket_id,
                    "order_id": escalated_order_id,
                    "reason": decision.reason,
                    "priority": priority,
                    "summary": decision.summary,
                },
            )
        return self._rejected(
            decision,
            "Executable semantic intent is unsupported.",
            reason_code=CompilerReasonCode.UNSUPPORTED_ACTION,
        )

    def _compile_read(
        self, decision: SemanticDecision, context: ExecutionContext, user_message: str
    ) -> CompiledDecision:
        tool = READ_TOOLS[decision.intent]
        if decision.intent == Intent.CUSTOMER_LOOKUP:
            if decision.target is not None:
                return self._rejected(
                    decision,
                    "Customer lookup does not accept a target.",
                    reason_code=CompilerReasonCode.TARGET_NOT_ALLOWED,
                )
            return self._action(decision, context, tool, {})
        if decision.intent == Intent.ORDER_LIST:
            if decision.target is not None:
                return self._rejected(
                    decision,
                    "Order list does not accept a target.",
                    reason_code=CompilerReasonCode.TARGET_NOT_ALLOWED,
                )
            return self._action(decision, context, tool, {})
        if decision.intent == Intent.TICKET_LIST:
            if decision.target is not None:
                return self._rejected(
                    decision,
                    "Ticket list does not accept a target.",
                    reason_code=CompilerReasonCode.TARGET_NOT_ALLOWED,
                )
            return self._action(decision, context, tool, {})
        if decision.intent == Intent.ORDER_LOOKUP:
            if self._wrong_order_target(decision.target):
                return self._rejected(
                    decision,
                    "Order lookup received a ticket target.",
                    reason_code=CompilerReasonCode.WRONG_TARGET_TYPE,
                )
            if decision.target is not None and decision.target.type == "latest_order":
                if not self._is_latest_order_request(user_message):
                    return self._clarification(
                        decision,
                        "A latest-order reference is not grounded in the current request.",
                        reason_code=CompilerReasonCode.LATEST_ORDER_NOT_GROUNDED,
                        clarification_fields=(CompilerField.ORDER_ID,),
                    )
            order_id = self._order_target(
                decision.target, context.effective_customer_id, context.tenant_id
            )
            if order_id is None:
                return self._clarification(
                    decision,
                    "A specific order is required.",
                    reason_code=CompilerReasonCode.ORDER_REQUIRED,
                    clarification_fields=(CompilerField.ORDER_ID,),
                )
            return self._action(decision, context, tool, {"order_id": order_id})
        if decision.intent == Intent.TICKET_LOOKUP:
            if decision.target is None or decision.target.type != "explicit_ticket":
                if decision.target is not None:
                    return self._rejected(
                        decision,
                        "Ticket lookup received a non-ticket target.",
                        reason_code=CompilerReasonCode.WRONG_TARGET_TYPE,
                    )
                return self._clarification(
                    decision,
                    "A specific ticket is required.",
                    reason_code=CompilerReasonCode.TICKET_REQUIRED,
                    clarification_fields=(CompilerField.TICKET_ID,),
                )
            return self._action(decision, context, tool, {"ticket_id": decision.target.ticket_id})
        return self._rejected(
            decision,
            "Read semantic intent is unsupported.",
            reason_code=CompilerReasonCode.UNSUPPORTED_READ,
        )

    def _compile_knowledge(self, decision: SemanticDecision) -> CompiledDecision:
        if decision.clarification_required and not decision.knowledge_query:
            return self._clarification(
                decision,
                "The knowledge request needs clarification.",
                reason_code=CompilerReasonCode.KNOWLEDGE_QUERY_REQUIRED,
                clarification_fields=(CompilerField.KNOWLEDGE_QUERY,),
            )
        return CompiledDecision(
            status=CompileStatus.NO_ACTION,
            intent=decision.intent,
            request_type=AgentRequestType.KNOWLEDGE_ONLY,
            reason_code=CompilerReasonCode.KNOWLEDGE_ONLY,
            requires_retrieval=decision.requires_retrieval,
            knowledge_query=decision.knowledge_query,
            reason=decision.reason,
        )

    def _compile_knowledge_and_action(
        self, decision: SemanticDecision, context: ExecutionContext
    ) -> CompiledDecision:
        if self._wrong_order_target(decision.target):
            return self._rejected(
                decision,
                "Knowledge-and-action request received a ticket target.",
                reason_code=CompilerReasonCode.WRONG_TARGET_TYPE,
            )
        order_id = self._order_target(
            decision.target, context.effective_customer_id, context.tenant_id
        )
        if order_id is None:
            return self._clarification(
                decision,
                "A specific order is required.",
                reason_code=CompilerReasonCode.ORDER_REQUIRED,
                clarification_fields=(CompilerField.ORDER_ID,),
            )
        if not decision.requires_retrieval or not decision.knowledge_query:
            knowledge_query = KNOWLEDGE_QUERIES[decision.intent]
        else:
            knowledge_query = decision.knowledge_query
        return CompiledDecision(
            status=CompileStatus.COMPILED_ACTION,
            reason_code=CompilerReasonCode.COMPILED_ACTION,
            intent=decision.intent,
            request_type=AgentRequestType.KNOWLEDGE_AND_ACTION,
            selected_tool="get_order",
            tool_arguments={
                "customer_id": context.effective_customer_id,
                "order_id": order_id,
            },
            requires_retrieval=True,
            knowledge_query=knowledge_query,
            reason=decision.reason,
        )

    @staticmethod
    def _contains_ticket_request(user_message: str) -> bool:
        words = set(re.findall(r"[\w’]+", user_message.casefold()))
        return bool(words & _TICKET_REQUEST_MARKERS)

    @staticmethod
    def _is_latest_order_request(user_message: str) -> bool:
        normalized = " ".join(user_message.casefold().split())
        return any(marker in normalized for marker in _LATEST_ORDER_MARKERS)

    @staticmethod
    def _is_contradictory_cancel(user_message: str) -> bool:
        normalized = " ".join(user_message.casefold().split())
        if "cancel" not in normalized and "iptal" not in normalized:
            return False
        return any(marker in normalized for marker in _CONTRADICTORY_CANCEL_MARKERS)

    @staticmethod
    def _reason_is_user_supported(reason: str, user_message: str) -> bool:
        """Require an extractive, non-boilerplate reason from authoritative user text.

        Refund action language is not business justification.  In particular, a Turkish
        request such as ``para iadesi yap`` must not support a model-provided reason of
        ``para iadesi`` merely because the words overlap.  Requiring every meaningful
        reason token to be present also prevents the model from appending an unsupported
        qualifier to an otherwise supported reason.
        """
        normalized_message = " ".join(user_message.casefold().split())
        if any(marker in normalized_message for marker in _UNSUPPORTED_REASON_INSTRUCTION_MARKERS):
            return False
        reason_words = set(re.findall(r"[\w’]+", reason.casefold())) - _GENERIC_REASON_WORDS
        message_words = set(re.findall(r"[\w’]+", user_message.casefold())) - _GENERIC_REASON_WORDS
        return bool(reason_words) and reason_words <= message_words

    def _compile_memory(self, decision: SemanticDecision) -> CompiledDecision:
        if decision.intent == Intent.MEMORY_REMEMBER and decision.memory_candidate is None:
            return self._clarification(
                decision,
                "A specific memory is required.",
                reason_code=CompilerReasonCode.MEMORY_CANDIDATE_REQUIRED,
                clarification_fields=(CompilerField.MEMORY_CANDIDATE,),
            )
        if decision.intent == Intent.MEMORY_FORGET and not decision.memory_key:
            return self._clarification(
                decision,
                "A memory key is required.",
                reason_code=CompilerReasonCode.MEMORY_KEY_REQUIRED,
                clarification_fields=(CompilerField.MEMORY_KEY,),
            )
        return CompiledDecision(
            status=CompileStatus.NO_ACTION,
            intent=decision.intent,
            request_type=AgentRequestType.MEMORY_ACTION,
            reason_code=CompilerReasonCode.MEMORY_ACTION,
            memory_candidate=decision.memory_candidate,
            memory_key=decision.memory_key,
            reason=decision.reason,
        )

    def _order_target(
        self, target: SemanticTarget | None, customer_id: int, tenant_id: str = "default"
    ) -> int | None:
        if target is None or target.type == "explicit_ticket":
            return None
        return self.resolver.resolve_order_id(target, customer_id, tenant_id)

    @staticmethod
    def _wrong_order_target(target: SemanticTarget | None) -> bool:
        return target is not None and target.type == "explicit_ticket"

    @staticmethod
    def _action(
        decision: SemanticDecision,
        context: ExecutionContext,
        tool: str,
        semantic_arguments: dict[str, object],
        *,
        refund_reason_support_status: RefundReasonSupportStatus = (
            RefundReasonSupportStatus.NOT_APPLICABLE
        ),
        refund_reason_validation_invoked: bool = False,
    ) -> CompiledDecision:
        return CompiledDecision(
            status=CompileStatus.COMPILED_ACTION,
            reason_code=CompilerReasonCode.COMPILED_ACTION,
            intent=decision.intent,
            request_type=(
                AgentRequestType.WRITE_ACTION
                if tool in ACTION_TOOLS.values()
                else AgentRequestType.READ_ACTION
            ),
            selected_tool=tool,
            tool_arguments={"customer_id": context.effective_customer_id, **semantic_arguments},
            reason=decision.reason,
            refund_reason_support_status=refund_reason_support_status.value,
            refund_reason_validation_invoked=refund_reason_validation_invoked,
        )

    @staticmethod
    def _clarification(
        decision: SemanticDecision,
        reason: str,
        *,
        reason_code: CompilerReasonCode,
        clarification_fields: tuple[CompilerField, ...] = (),
        refund_reason_support_status: RefundReasonSupportStatus | None = None,
        refund_reason_validation_invoked: bool = False,
        compiler_clarification_cause: CompilerClarificationCause = CompilerClarificationCause.OTHER,
    ) -> CompiledDecision:
        status = (
            RefundReasonSupportStatus.NOT_EVALUATED
            if decision.intent is Intent.REFUND_REQUEST
            else RefundReasonSupportStatus.NOT_APPLICABLE
        )
        return CompiledDecision(
            status=CompileStatus.CLARIFICATION_REQUIRED,
            intent=decision.intent,
            request_type=AgentRequestType.UNCLEAR,
            reason=reason,
            refund_reason_support_status=(refund_reason_support_status or status).value,
            refund_reason_validation_invoked=refund_reason_validation_invoked,
            compiler_clarification_cause=compiler_clarification_cause.value,
            reason_code=reason_code,
            missing_fields=list(_absent_fields(decision, clarification_fields)),
            clarification_fields=list(clarification_fields),
        )

    @staticmethod
    def _rejected(
        decision: SemanticDecision, reason: str, *, reason_code: CompilerReasonCode
    ) -> CompiledDecision:
        status = (
            RefundReasonSupportStatus.NOT_EVALUATED
            if decision.intent is Intent.REFUND_REQUEST
            else RefundReasonSupportStatus.NOT_APPLICABLE
        )
        return CompiledDecision(
            status=CompileStatus.COMPILE_REJECTED,
            intent=decision.intent,
            request_type=AgentRequestType.UNCLEAR,
            reason="The request could not be compiled safely.",
            rejection_reason=reason,
            reason_code=reason_code,
            refund_reason_support_status=status.value,
        )


def all_semantic_intents_are_routed() -> bool:
    return set(SEMANTIC_INTENT_ROUTES) == set(Intent)


def _target_fields(decision: SemanticDecision) -> tuple[CompilerField, ...]:
    if decision.intent == Intent.TICKET_LOOKUP or (
        decision.intent == Intent.HUMAN_ESCALATION
        and decision.target is not None
        and decision.target.type == "explicit_ticket"
    ):
        return (CompilerField.TICKET_ID,)
    if decision.intent in {
        Intent.ORDER_CANCEL,
        Intent.REFUND_REQUEST,
        Intent.ORDER_LOOKUP,
        Intent.REFUND_ELIGIBILITY,
        Intent.CANCELLATION_EXPLANATION,
        Intent.TICKET_CREATE,
        Intent.HUMAN_ESCALATION,
    }:
        return (CompilerField.ORDER_ID,)
    return ()


def _absent_fields(
    decision: SemanticDecision, fields: tuple[CompilerField, ...]
) -> tuple[CompilerField, ...]:
    """Inspect typed inputs, never presentation copy, for genuinely absent values."""
    return tuple(
        field
        for field in fields
        if not (
            getattr(decision.target, field.value, None)
            if field in {CompilerField.ORDER_ID, CompilerField.TICKET_ID}
            else getattr(decision, field.value, None)
        )
    )
