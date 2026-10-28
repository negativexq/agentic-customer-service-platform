# Structured compiler result contract

Tracking: [issue #29](https://github.com/negativexq/agentic-customer-service-platform/issues/29).

The compiler previously inferred resumable fields by searching its English explanation for phrases such as `specific`, `refund reason`, and `category and description`. The same explanation was both presentation copy and a workflow input. `_missing_required_fields(intent, reason)` has been removed.

Each deterministic compiler branch now declares its own `CompilerReasonCode` and `CompilerField` values. These are server-owned result metadata; the provider-facing semantic schemas and prompts do not gain these fields.

For a refund with no business justification:

```json
{
  "status": "clarification_required",
  "reason_code": "refund_reason_required",
  "missing_fields": ["reason"],
  "clarification_fields": ["reason"],
  "reason": "A refund reason is required."
}
```

For a supplied justification that is unsupported by the customer's message, `reason_code` is `refund_reason_unsupported`, `missing_fields` is empty, and `clarification_fields` still contains `reason`. Similarly, a supplied but ungrounded order ID needs clarification without being labeled absent. Ticket and escalation checks report only the actually absent detail fields.

## Validation and consumers

- Every compiler-produced outcome has a reason code; the finite code/status catalog covers compiled actions, no-action routes, clarification, and rejection.
- Field names are enum values with bounded, unique lists. Missing fields must be a subset of clarification fields. Non-clarification outcomes cannot request fields; non-action outcomes cannot carry executable tool arguments. Compilation success still requires the existing policy, confirmation, revalidation, and execution gates.
- The workflow node uses the reason code and clarification fields. The persisted `AgentState.missing_required_fields` key remains unchanged for existing suspended workflows. The current incremental-workflow boundary is preserved explicitly: memory/knowledge/contradictory-request clarification metadata does not create a new business workflow. This change does not add new follow-up parsers for ticket details or escalation priority/summary.
- Operator projections expose the three typed fields additively. Frontend types keep them optional for older server/fixture payloads. Display explanations remain bounded presentation text.
- Refund justification provenance still checks authoritative user input. That business check is separate from parsing compiler explanation copy and remains in place.

## Persistence compatibility

Historical `CompiledDecision` payloads can supply the old `missing_required_fields` key at the deserialization boundary. It populates `clarification_fields`, whose meaning matches the old required-input list. Historical reason codes remain `not_recorded`; genuinely absent fields are not reconstructed from old prose. Older operator views receive the same explicit unknown-code default and empty field lists.

New results serialize only the canonical field names. The checkpoint serializer allowlist adds exactly `CompilerReasonCode` and `CompilerField`; the existing `CompiledDecision` and `CompileStatus` module paths remain intact. Arbitrary checkpoint constructors remain blocked.

Compatibility is old-to-new. Older runtimes do not know the new checkpoint enum types; deploy updated checkpoint readers together rather than mixing old/new workers on newly written threads. No table migration or historical JSON backfill is required.

## Verification

The local full suite with real PostgreSQL enabled passed **1237 tests, 1 skipped, 0 warnings** under `-W error`. The existing optional production-frontend-bundle check accounts for the skip. Frontend type checking, Ruff, and mypy also passed.

[Contract regressions](../tests/test_compiler_contracts.py) cover:

- all four compiler outcome states, required-field combinations, unsupported reasons, ungrounded targets, and defensive registry failures;
- invalid codes/field names, inconsistent statuses, duplicate fields, executable arguments on clarification, and exclusion from provider-owned schemas;
- explanations rewritten *before* clarification fields are produced, including Turkish and misleading English phrases;
- strict serializer round trips and an actual historical-shaped serialized model;
- restart/resumption, explicit confirmation, and one refund effect across replay;
- bounded operator projection and durable SQL projection round trips.

The original compiler, grounding, workflow, checkpoint, authorization, revalidation, idempotency, and PostgreSQL refund regressions remain part of the complete suite. No model-quality or production-capacity score is inferred from these regression counts.
