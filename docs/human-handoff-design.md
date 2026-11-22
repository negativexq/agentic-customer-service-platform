# Human Handoff Design

_Status: proposed. Baseline: `origin/main` at `4726fc6`. Nothing here is implemented._

## 1. Problem

`escalate_to_human` writes an `Escalation` row with status `queued`, and the flow ends there:

- No code ever sets `assigned` or `resolved`, and the table has no assignee.
- `Escalation` has no `conversation_id`, so it cannot be tied to the chat that raised it.
- The agent never reads escalation state, so the AI keeps answering after a handoff.
- There is no operator queue and no way for an operator to reply to a customer.
- Operators cannot read the conversation: `/ui/conversations/{id}` returns
  `content_available: false` placeholders.

## 2. Goal

The smallest slice that works end to end:

1. A customer asks for a human, and the AI stops replying in that conversation.
2. An operator sees the conversation in a queue and claims it.
3. The operator reads the history and replies to the customer.
4. The operator resolves the conversation or hands it back to the AI.

### Non-goals

SLA timers, automatic assignment, CSAT, attachments, new channels, canned replies, Agent Assist,
and payment connectors. Real-time push is also out of scope; the first version polls.

## 3. Boundaries to preserve

The [hardening roadmap](engineering-hardening-roadmap.md) strengths still apply. In particular:

- Ownership is deterministic server state. The LLM can propose an escalation through the existing
  tool path but can never claim, release, or resolve a conversation.
- Operator replies are text to the customer. They never enter the graph, the decision compiler, or
  tool execution, and they are never treated as customer confirmation.
- Tenant scope and customer scope are enforced independently on every new path.

## 4. Key design decisions

### 4.1 Ownership lives in a database row, not in graph state

Checkpoint threads are actor-scoped (`checkpoint_thread_id` includes the actor type and ID), so a
customer and an operator using the same `conversation_id` have separate graph state. Graph state
therefore cannot be the shared answer to "who owns this conversation?". There is also no
conversation table today; `conversation_id` is client-supplied.

A new `conversations` row becomes the single source of truth, unique on
`(tenant_id, customer_id, conversation_id)`. This matches the customer scoping of checkpoint
threads, so a reused ID under another customer is simply a separate conversation and the response
cannot reveal that the ID is in use.

### 4.2 The gate runs before the runtime

`POST /agent/chat` checks ownership before calling `runtime.run`. When the conversation is not
AI-owned, the route stores the customer message and returns a fixed acknowledgement without calling
the LLM. This also means a confirmation such as "yes" cannot reach `check_pending_action` during a
handoff. The turn still writes a minimal run projection with no message text, so the returned
`agent_run_id` resolves and operators can see the gated turn.

### 4.3 Ownership changes are revalidated at the write boundary

An AI run can be in flight when ownership changes, and holding a row lock across an LLM call is not
acceptable. Instead, `conversations.ownership_version` increments on each transition and each run
captures it at the gate. Then:

- Before any business write, the execution path re-reads ownership. If the version changed, the
  write is refused and fails closed through the existing error taxonomy.
- Before persisting an AI reply, the same check runs. A stale reply is dropped from the customer
  transcript and recorded in the run projection.

### 4.4 Escalating and changing ownership commit together

`escalate_to_human` already commits through `commit_business_write` with an idempotency receipt. The
ownership transition joins that transaction, so an escalation can never exist without the AI being
silenced, or the reverse. A partial unique index allows one active escalation per conversation.
`Escalation.conversation_id` is nullable because `POST /escalations` has no conversation.

### 4.5 Pending actions do not survive a handoff

`confirmation_ttl_seconds` defaults to 300, so a short handoff could otherwise leave a live pending
action. `check_pending_action` treats any action with `created_at` earlier than
`conversations.ai_resumed_at` as expired, using the existing `expired` route. After handing back,
the customer must start the action again.

### 4.6 Message history is a new governed store

Messages exist only in actor-scoped checkpoints, which are the wrong source for operators. A new
`conversation_messages` table holds the text, with these rules:

- Content passes through the existing DLP in `app/memory/dlp.py` before storage. Redactable values
  are redacted, and restricted content is replaced by a category placeholder.
- Rows carry `expires_at` from a new `CONVERSATION_MESSAGE_TTL_DAYS` setting, with deletion by
  customer alongside existing memory deletion.
- Message text is never added to traces, metrics, logs, or run projections.

The placeholder response on `/ui/conversations/{id}` stays until this store exists, then serves
real content only through the new access-checked path.

**Consequence:** the DLP runs on every message, not only memory candidates. It was built for
memory, so its false-positive rate on ordinary support text must be measured in H2.

### 4.7 Operators need an explicit tenant-wide grant

`Principal.customer_ids` is capped at 100 and, for OIDC operators, is a row-level boundary. That
works for scoped investigation but not for a shared queue.

Add an optional `OIDC_SUPPORT_TENANT_WIDE_ROLE` setting, unset by default. An operator holding that
role can access every customer in their tenant. Without it, the queue shows only `customer_ids`.
The shared primitive replaces the private `_customer_filter` in `app/api/routes/ui.py`, so existing
UI routes and the new routes cannot disagree. Tenant-wide access never crosses tenants.

## 5. State model

| Owner | Meaning | AI replies? | Customer messages |
| --- | --- | --- | --- |
| `ai` | Default | Yes | Sent through the graph |
| `queued` | Waiting for an operator | No | Stored, fixed acknowledgement |
| `human` | Claimed by one operator | No | Stored, no automatic reply |
| `closed` | Resolved by an operator | Yes, after reopening | Reopens to `ai` (decision 3) |

| Transition | Actor | Escalation status | Notes |
| --- | --- | --- | --- |
| `ai → queued` | System, on escalation | `queued` | Same transaction as the escalation |
| `queued → human` | Operator claim | `assigned` | Compare-and-set; losing a race returns 409 |
| `ai → human` | Operator takeover | Creates `assigned` | Audited with a reason code |
| `human → human` | Operator reassign | `assigned` | Only the assignee, in this slice |
| `human → ai` | Assignee release | `resolved` | Sets `ai_resumed_at` |
| `human → closed` | Assignee resolve | `resolved` | |
| `closed → ai` | System, on a new customer message | Unchanged | Sets `ai_resumed_at` |

Customers can trigger only two transitions: the existing escalation path and reopening a closed
conversation. Every
transition appends a row to `conversation_ownership_events`: actor ID, from and to state, bounded
reason code, and timestamp, with no message text.

## 6. Data model

New Alembic migration after `0009`. All new tables carry `tenant_id` and follow
[multi-tenancy](multi-tenancy.md).

```text
conversations
  id, tenant_id, conversation_id, customer_id
  owner, assignee_actor_id, ownership_version, ai_resumed_at
  created_at, updated_at
  unique (tenant_id, customer_id, conversation_id)
  index (tenant_id, owner, updated_at)

conversation_messages
  id, tenant_id, conversation_pk, sequence, sender_type (customer|ai|operator|system)
  sender_actor_id, content, redaction_state, created_at, expires_at
  unique (conversation_pk, sequence)

conversation_ownership_events
  id, tenant_id, conversation_pk, actor_id, from_owner, to_owner, reason_code, created_at

escalations (altered)
  + conversation_pk (nullable), + assignee_actor_id, + assigned_at, + resolved_at
  partial unique (conversation_pk) where status in (queued, assigned)
```

`assignee_actor_id` stores the one-way `oidc:<digest>` actor ID, never a subject or email.

## 7. API

Operator routes require `require_support_operator` and customer-scope authorization, and return
the standard not-found response when out of scope.

| Method and path | Purpose |
| --- | --- |
| `GET /operator/queue` | Bounded, cursor-paged list filtered by owner and priority |
| `GET /operator/conversations/{id}` | Metadata, escalation summary, and messages |
| `POST /operator/conversations/{id}/claim` | `queued → human` or takeover |
| `POST /operator/conversations/{id}/messages` | Reply; assignee only; requires an idempotency key |
| `POST /operator/conversations/{id}/release` | `human → ai` |
| `POST /operator/conversations/{id}/resolve` | `human → closed` |

Mutations include the expected `ownership_version` and return 409 when it is stale.

Customer routes:

| Method and path | Purpose |
| --- | --- |
| `POST /agent/chat` | Existing; gains `conversation_owner` in the response |
| `GET /conversations/{id}/messages?after=<sequence>` | Bounded polling for new messages |

Customers read their own conversation only, and the response omits operator actor IDs.

## 8. Frontend

- **Operator Console:** a new Inbox view in the existing `routes` and `WorkspaceRail` structure,
  following [frontend design guidelines](frontend-design-guidelines.md).
- **Customer widget:** a separate entry point that shares no operator code paths or operator
  tokens. Authentication stays outside this repository, as [security](security.md) already says
  for the console: a deployment-owned frontend or BFF presents the access token.

## 9. Failure modes

| Scenario | Behavior |
| --- | --- |
| Two operators claim together | Compare-and-set; one succeeds, the other gets 409 |
| Escalation arrives during an AI run | Version check blocks the stale write and reply |
| Commit outcome unknown during escalation | Existing `UnknownWriteOutcomeError` path; ownership shares the outcome |
| Duplicate operator reply | Idempotency key replays the first result |
| Assignee loses access while assigned | Writes are refused; the conversation stays stranded (decision 2) |
| Ownership lookup fails | Fail closed: no LLM call, return the existing dependency error |
| Message store write fails at the gate | Return an error so the customer can retry; never acknowledge an unsaved message |

## 10. Observability

Add bounded metrics for transitions and queue depth by owner and priority. New labels must pass the
allowlist in `app/observability/metric_privacy.py`. No conversation IDs, actor IDs, or message text
go into metric labels.

## 11. Delivery plan

**Status:** H1–H4 ship together in one change. Every customer, AI, operator, and system message
is recorded after value-level DLP redaction (whole messages are never dropped, unlike memory
candidates), with `CONVERSATION_MESSAGE_TTL_DAYS` expiry and per-customer deletion through
`DELETE /operator/customers/{id}/messages`. Operators may claim a queued handoff or take over a
live AI conversation (`operator_takeover`). The customer polling route exists; the widget (H5)
does not. Operator replies carry the operator display name from the OIDC `name` claim.

Each package is a separate PR that keeps CI green. H1 alone delivers the core safety property.

| Package | Scope | Depends on |
| --- | --- | --- |
| **H1** | `conversations`, ownership events, chat gate, version checks, escalation transaction, pending-action rule, claim/release/resolve service functions | None |
| **H2** | `conversation_messages`, DLP, TTL, deletion, DLP false-positive measurement | H1 |
| **H3** | Tenant-wide grant, shared authorization primitive, operator API, operator takeover | H1, H2 |
| **H4** | Operator Inbox UI and E2E coverage | H3 |
| **H5** | Customer polling route and widget | H2 |

### Required tests

- The LLM provider is not called while a conversation is `queued` or `human`.
- A run started before escalation can neither write nor reply after it (real PostgreSQL concurrency,
  in the style of `tests/test_refund_postgres_concurrency.py`).
- A confirmation sent during or after a handoff does not execute a pre-handoff action.
- Concurrent claims produce exactly one owner.
- Cross-tenant and cross-customer access match the not-found response of unknown records.
- Operator reply text cannot reach the compiler or tool execution.
- Message content is absent from traces, metrics, logs, and projections.
- Malicious operator or customer text in history is never used as instructions after handing back.

## 12. Decisions

This is a demo and portfolio project with no real operators or customers, which settles the
following for this slice:

1. **After-hours queue.** Show a fixed expected-wait message. Operators release manually; there is
   no timeout until SLA work exists.
2. **Reassignment by supervisors.** Deferred. Only the assignee can reassign, and a stranded
   conversation is an accepted limitation of the demo.
3. **Messages after `closed`.** A new customer message reopens the conversation to `ai` and sets
   `ai_resumed_at`, so the customer is never stuck.
4. **AI context after handing back.** The AI does not see the human exchange. It resumes from its
   own checkpoint, which avoids prompt injection through history and keeps the graph unchanged.
5. **Message retention.** Demo and local configuration use 30 days. The setting keeps no production
   default, because a real deployment would need a legal and business decision.
6. **Refund review.** Separate package after H3, using the same operator authorization and the
   transition rules required by [refund concurrency](refund-concurrency.md).
