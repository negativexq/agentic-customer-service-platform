"""Server-owned compiler metadata; never supplied by the proposal provider."""

from enum import StrEnum


class CompilerReasonCode(StrEnum):
    NOT_RECORDED = "not_recorded"  # Historical persisted results, without inference.
    COMPILED_ACTION = "compiled_action"
    KNOWLEDGE_ONLY = "knowledge_only"
    MEMORY_ACTION = "memory_action"
    UNROUTED_INTENT = "unrouted_intent"
    TARGET_REQUIRED = "target_required"
    CONTRADICTORY_ACTION = "contradictory_action"
    WRONG_TARGET_TYPE = "wrong_target_type"
    ORDER_REQUIRED = "order_required"
    REFUND_REASON_REQUIRED = "refund_reason_required"
    REFUND_REASON_UNSUPPORTED = "refund_reason_unsupported"
    TICKET_DETAILS_REQUIRED = "ticket_details_required"
    TICKET_REQUEST_REQUIRED = "ticket_request_required"
    ORDER_UNRESOLVED = "order_unresolved"
    ESCALATION_DETAILS_REQUIRED = "escalation_details_required"
    INVALID_ESCALATION_PRIORITY = "invalid_escalation_priority"
    UNSUPPORTED_ACTION = "unsupported_action"
    TARGET_NOT_ALLOWED = "target_not_allowed"
    LATEST_ORDER_NOT_GROUNDED = "latest_order_not_grounded"
    TICKET_REQUIRED = "ticket_required"
    UNSUPPORTED_READ = "unsupported_read"
    KNOWLEDGE_QUERY_REQUIRED = "knowledge_query_required"
    REQUEST_CLARIFICATION_REQUIRED = "request_clarification_required"
    MEMORY_CANDIDATE_REQUIRED = "memory_candidate_required"
    MEMORY_KEY_REQUIRED = "memory_key_required"


class CompilerField(StrEnum):
    ORDER_ID = "order_id"
    TICKET_ID = "ticket_id"
    REASON = "reason"
    CATEGORY = "category"
    DESCRIPTION = "description"
    PRIORITY = "priority"
    SUMMARY = "summary"
    KNOWLEDGE_QUERY = "knowledge_query"
    MEMORY_CANDIDATE = "memory_candidate"
    MEMORY_KEY = "memory_key"
