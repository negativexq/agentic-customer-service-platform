# Engineering Hardening Roadmap

_Production-oriented Agentic AI Control Plane — post-release hardening backlog_

This document is the durable engineering backlog for production-boundary work
identified during the latest main-branch technical review. It is intentionally
not a feature wishlist. The items below are grounded in the current repository
architecture. Completed controls below record their merged resolutions and evidence;
unchecked items remain evidence gaps, architectural debt, governance work, or
future optimization.

**Last reconciled:** 2026-10-28, against merged main baseline
[`c650a2b`](https://github.com/negativexq/agentic-customer-service-platform/commit/c650a2bacc4c22f8b6ac3fdede248b37721844bf),
with warning-cleanup closure evidence recorded in item 11.
DONE means the scoped fix is merged, its issue is closed, and the linked
regressions passed; it is not a claim of unrestricted production certification.

The current baseline already has strong control-plane boundaries: typed tool
execution, `semantic_decision_v3`, a deterministic `DecisionCompiler`, explicit
confirmation and post-confirmation revalidation, idempotency receipts and
database uniqueness, persisted workflows/checkpoints, fail-closed RAG
grounding, memory consent/DLP/TTL/tenant scoping, deterministic QA, real-LLM
QA, and full-stack integration smoke coverage. This roadmap hardens those
boundaries; it should not replace them with more LLM-driven behavior.

## Priority checklist

- [x] P0 OIDC operator customer-scope authorization — #16 / #20
- [x] P0/P1 production checkpoint fail-closed — #17 / #21
- [x] P1 production network topology — #18 / #22
- [ ] P1 evaluation evidence alignment — PARTIAL; README denominator wording resolved
- [x] P1 PostgreSQL benchmark evidence — #19 / #23
- [x] P1 refund eligibility concurrency — #13 / #14 (related completed work)
- [ ] P2 repository governance
- [ ] P2 structured compiler contracts
- [ ] P2 tool-owned revalidation strategy
- [ ] P2/P3 frontend request/state correctness
- [ ] P3 graph compile benchmark
- [x] P3 warning cleanup — #27; zero-warning backend baseline
- [ ] P3 RAG semantic evaluation expansion

## Closure evidence — 2026-10-27

All five issues below are CLOSED and their fixing PRs are MERGED. The final
runs for PRs #20–#23 each passed all 13 CI jobs, including security/image scans,
E2E, lifecycle, metrics delivery, and five telemetry runtime scenarios. The
refund row uses a later green regression run containing its PostgreSQL tests;
it does not claim every check in the original #14 run passed.

| Completed control | Closed issue | Merged PR / revision | Green verification run |
| --- | --- | --- | --- |
| OIDC operator customer scope | [#16](https://github.com/negativexq/agentic-customer-service-platform/issues/16) | [#20](https://github.com/negativexq/agentic-customer-service-platform/pull/20) / `e98f680` | [40567530582](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40567530582) |
| Production checkpoint fail-closed | [#17](https://github.com/negativexq/agentic-customer-service-platform/issues/17) | [#21](https://github.com/negativexq/agentic-customer-service-platform/pull/21) / `c9f8cfa` | [40567554493](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40567554493) |
| Production network publication policy | [#18](https://github.com/negativexq/agentic-customer-service-platform/issues/18) | [#22](https://github.com/negativexq/agentic-customer-service-platform/pull/22) / `86a7602` | [40569195854](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40569195854) |
| Real PostgreSQL application benchmark | [#19](https://github.com/negativexq/agentic-customer-service-platform/issues/19) | [#23](https://github.com/negativexq/agentic-customer-service-platform/pull/23) / `0748e44` | [40568973887](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40568973887) |
| Refund eligibility concurrency | [#13](https://github.com/negativexq/agentic-customer-service-platform/issues/13) | [#14](https://github.com/negativexq/agentic-customer-service-platform/pull/14) / `bd4e2b5` | [40570806608](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40570806608) |

Post-merge [main CI run 40570806608](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40570806608)
passed all **13 jobs** on `0748e44`. Its Backend Quality log records
**1190 tests passed, 1 skipped**, Ruff format validation of 405 files, and mypy
success for 340 source files. The local combined run, with PostgreSQL enabled
and local LLM overrides excluded, matched those test counts and was verified
byte-for-byte against merged main. These are backend regression counts, not
historical release-QA or model-evaluation scores.

### Related completed work: refund eligibility concurrency

**Status:** DONE — issue #13 closed by PR #14.

The original race was reproduced on PostgreSQL 16.14 at READ COMMITTED using a
controlled external order-status writer. Creation now refreshes authoritative
order state under `SELECT FOR UPDATE`, retaining the lock through the outer
commit. Same-key replay and active-refund uniqueness remain protected.

- [Implementation](../app/tools/refunds.py) and [invariant scope](refund-concurrency.md).
- [Four PostgreSQL regressions](../tests/test_refund_postgres_concurrency.py):
  writer-first rejection with stale ORM state, lock lifetime through outer commit,
  and same/different-key concurrent requests.
- The invariant covers creation. It does not forbid arbitrary external status
  changes after commit, and this path creates a request rather than a payment refund.

### Durable PostgreSQL benchmark evidence

- [Unmodified JSON report](evidence/postgres-application-benchmark-2026-10-27.json).
- Source: [main CI run 40570806608](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40570806608),
  attempt 1, commit `0748e44cf6b6c147d989052763954f449b2d64ce`.
- Original [artifact 12764019320](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40570806608/artifacts/12764019320):
  `postgres-benchmark-40570806608-1`, retained by CI for 14 days. The Git copy
  remains available after artifact expiry; its bytes were not regenerated.
- Recorded at `2026-10-27T19:41:23.860873+00:00`; schema version `1`.
- JSON SHA-256: `53802e5615e858ba712783514a5b594f89c0ecc8e58629714da50b868727bfd0`.

This snapshot proves the named transaction workload. The closure matrix
separately links completed full PR CI runs; the benchmark JSON does not assert
the outcome of unrelated later jobs.

## Priority model

- **P0** — security, authorization, or correctness blocker.
- **P1** — production hardening or evidence-correctness gap.
- **P2** — maintainability, contract clarity, or release governance.
- **P3** — optimization, cleanup, or future evaluation depth.

Every item has a primary priority. Shared labels such as `P0/P1` indicate that
the item is a fail-closed production control with both blocker and hardening
implications; `P2/P3` indicates maintainability work that should be addressed
before optimization.

## Existing strengths to preserve

The following controls are established architectural strengths and are not
targets for redesign:

- The LLM proposes semantics; it does not grant execution authority.
- `semantic_decision_v3` and the deterministic `DecisionCompiler` separate
  decision, authority, execution, validation, and workflow state.
- `BusinessTargetResolver`, typed tool inputs, and tool-side scope checks keep
  server-owned identifiers and customer scope out of model authority.
- Policy evaluation, explicit confirmation, post-confirmation revalidation,
  and idempotency receipts protect customer-impacting mutations.
- Database uniqueness constraints, locking where relevant, and unknown-write
  outcomes protect against replay and ambiguous business effects.
- Checkpoint serialization and persistent workflow state support pending
  confirmation, suspend/resume, browser refresh, and backend restart recovery.
- RAG grounding validates excerpts and claims, detects numeric conflicts, and
  fails closed on uncertainty.
- Memory has consent, DLP/security filtering, TTL, and tenant-scope controls;
  memory is not an authorization channel.
- Deterministic integration QA, real-LLM QA, and full-stack authenticated
  smoke provide meaningful evidence for the current release baseline.

Do not weaken these boundaries to improve semantic convenience, demo flow, or
test pass rates.

## Roadmap items

### 1. OIDC operator customer-scope authorization

**Priority:** P0
**Category:** Security / authorization
**Status:** DONE — 2026-10-27; issue #16 CLOSED, PR #20 MERGED
**Classification:** Confirmed authorization gap — fixed

#### Confirmed problem and resolution

A valid OIDC operator with `customer_ids=[1]` could read customer 2's run,
conversation, and memory projections in the same tenant, while `/customers/2`
correctly returned 404. `customer_ids` is now an OIDC operator row-level
customer authorization boundary, independently of tenant scope.

Run lists and conversations apply the allowlist before limits in both SQL and
in-memory projection stores. Run detail, tool/RAG/trace projections, audit
lookups, and memory reads enforce it too. Audit access uses the event's
customer even when no matching run projection exists. Missing and unauthorized
detail lookups share the 404 contract. Explicitly trusted static/demo operator
behavior remains unchanged.

#### Completed acceptance and evidence

- [x] Allowed/denied customer access is consistent with direct business routes.
- [x] Tenant isolation and anti-enumeration are preserved.
- [x] Lists filter before limits; detail/audit/memory reads enforce scope.
- [x] Service-level memory retrieval also checks the OIDC allowlist.

[Signed-token HTTP regressions](../tests/test_oidc_operator_ui_scope.py) cover
both projection backends, restricted customers, cross-tenant reads, limit
ordering, and audit/memory behavior. Discovery/JWKS use a mock transport; this
is not a live external-IdP integration claim. Existing authorization and UI
contracts also passed. See the closure matrix for the merged PR and full CI.

### 2. Production checkpoint backend must fail closed

**Priority:** P0/P1
**Category:** Reliability / correctness / deployment safety
**Status:** DONE — 2026-10-27; issue #17 CLOSED, PR #21 MERGED
**Classification:** Confirmed production configuration gap — fixed

#### Confirmed problem and resolution

Production previously accepted `CHECKPOINT_BACKEND=memory` and constructed a
`MemoryCheckpointProvider`. Configuration validation and provider construction
now both require PostgreSQL in production, including when settings are copied
without validation. Supported URLs are `postgresql://` and
`postgresql+psycopg://`. Production Compose explicitly selects `postgres` for
setup and backend services. PostgreSQL initialization errors still abort
startup without a memory fallback.

#### Completed acceptance and evidence

- [x] Production memory configuration is rejected before requests are served.
- [x] Non-PostgreSQL/unsupported driver URLs are rejected in production.
- [x] Valid PostgreSQL provider construction remains supported.
- [x] Development/test memory checkpoints remain explicitly available.
- [x] Production setup/backend settings select the durable backend.

[Configuration/provider regressions](../tests/test_production_checkpoint_configuration.py)
cover environment normalization, backend/URL rejection, accepted PostgreSQL
providers, unvalidated programmatic settings, and development memory support.
[Checkpoint regressions](../tests/test_checkpointing.py) and the green lifecycle
CI provide recovery coverage. Evidence establishes the configuration/factory
startup guard and reference integration behavior, not a live production IdP deployment.

### 3. Harden production network topology and port exposure

**Priority:** P1
**Category:** Security / deployment hardening
**Status:** DONE — 2026-10-27; issue #18 CLOSED, PR #22 MERGED
**Classification:** Confirmed inherited host-publication gap — fixed

#### Confirmed problem and resolution

The production overlay previously retained host publications for PostgreSQL,
Qdrant and Jaeger, while the validator reported PASS. It now resets inherited
host publications for database, vector, tracing, backend, and Collector services.
Service discovery remains on the Compose networks. Frontend TCP/8080 is the
public ingress; optional Prometheus TCP/9090 inspection is loopback-only.

The [topology validator](../scripts/validate_production_topology.py) renders the
metrics profile as well and rejects publications from any service outside the
frontend/Prometheus allowlist, including future unlisted services.

#### Completed acceptance and evidence

- [x] Production internal services have no published host ports.
- [x] Frontend and optional metrics publication policy is explicit and checked.
- [x] Unexpected services/public Prometheus bindings are rejected.
- [x] Development/integration publications and internal service networks remain intact.

[Rendered-Compose regressions](../tests/test_production_network_topology.py)
verify these rules. Existing overlay-value tests handle the Compose `!reset`
tag; actual merge behavior is checked with Docker Compose. Green lifecycle,
E2E, and metrics CI cover the reference integration topology.
[Approved network policy](production-network-topology.md) documents the boundary;
this is Compose configuration evidence, not a deployed firewall/VPC audit.

### 4. Align evaluation and README evidence semantics

**Priority:** P1
**Category:** Governance / evidence correctness
**Status:** PARTIAL — README denominator wording resolved; artifact/output reconciliation remains open
**Classification:** Confirmed documentation/evidence ambiguity
**Estimated effort:** S

#### Completed scope and remaining work

- [x] README separates semantic attempts, operational scenarios, resilience runs,
  historical release-QA counts and real-LLM quality outcomes; it no longer labels
  `110/110 deterministic` as an undifferentiated success rate.
- [x] [Evaluation overview](evaluation-overview.md), [source-bound release evidence](release-evidence.md)
  and [artifact retention policy](evaluation-artifact-policy.md) document distinct
  scopes and historical artifact identity.
- [ ] Finish current deterministic-run/output reconciliation and verify generated
  evaluation artifacts versus intentionally absent upload inputs. The PostgreSQL
  artifact below closes item 5 only, not this broader evaluation-evidence audit.

The [current README evidence table](../README.md#evaluation-and-release-evidence)
is already aligned in wording. The original ambiguity below is historical;
remaining acceptance work must not repeat the completed README rewrite.

#### Original problem and remaining audit scope

The initial review cited `110 scenarios` and `96.4% overall` alongside README
wording such as `110/110 deterministic`. Those are historical review inputs,
not new measurements in this update. The README ambiguity is resolved; the
remaining audit must map current runner outputs and upload expectations to
named evidence without conflating historical and current runs.

#### Why it matters

Release reviewers must be able to map every public metric to a real runner
output. Ambiguous metrics reduce trust in otherwise strong safety evidence.

#### Target behavior

Documentation separates scenario count, overall task/quality score, critical
safety gates, safety suites, resilience suites, historical runs, and current
runs. For example:

```text
Deterministic evaluation: 110 scenarios, 96.4% overall task completion
Safety: 40/40
Resilience: 28/28
Critical safety gate: PASS
```

The example is illustrative; public numbers must come from the corresponding
runner/artifact and must not be invented or merged across runs.

#### Scope and implementation direction

- Audit README, evaluation documentation, release QA reports, and CI artifact
  wording.
- Identify the authoritative output for each metric.
- Update labels to include runner, date/scope, and denominator semantics.
- Investigate artifact-upload steps that report missing files.
- Preserve historical findings rather than rewriting them as current passes.

#### Acceptance criteria

- Every public number maps to a real artifact or documented runner output.
- No `x/x` wording implies scenario perfection unless that is exactly what the
  denominator represents.
- Historical and current evidence are visibly separated.
- Missing evaluation artifacts are either fixed or explicitly reported.

#### Tests and evidence required

- Markdown/link validation.
- Evaluation artifact schema/output check.
- CI artifact-upload dry run or equivalent evidence.
- Reviewer can reproduce the README metrics from named outputs.

### 5. Establish real PostgreSQL concurrency/capacity evidence

**Priority:** P1
**Category:** Reliability / performance evidence
**Status:** DONE — 2026-10-27; issue #19 CLOSED, PR #23 MERGED
**Classification:** PostgreSQL application transaction evidence gap — closed

#### Confirmed problem and resolution

CI previously invoked a persistence primitive without `CAPACITY_DATABASE_URL`,
so it silently measured SQLite. The separate
[application benchmark](../scripts/benchmark_postgres_application.py) now
requires PostgreSQL and exercises real refund/idempotency/commit transactions
in its own UUID-named schema. CI explicitly supplies the PostgreSQL URL and
retains version-1 JSON evidence. The older SQLite/INSERT primitive remains
clearly separate in the [capacity report](capacity-report.md).

#### Completed acceptance and evidence

- [x] Missing/non-PostgreSQL configuration fails; there is no fallback in this mode.
- [x] Same-key replay, competing different keys, and independent orders use real transactions.
- [x] Actual effects, receipts, replay identities and error counts are checked.
- [x] Failed invariants return nonzero; failure/bootstrap paths clean up their schema.
- [x] Version, isolation, pool/workers, latency percentiles and throughput are recorded.
- [x] CI artifact provenance and a durable report copy are retained in the closure evidence section.

[Benchmark regressions](../tests/test_postgres_application_benchmark.py) include
actual PostgreSQL transactions, no-fallback behavior, bounded failure reports,
and cleanup after invariant/bootstrap failure. Reproduce with
`CAPACITY_DATABASE_URL=... make capacity-postgres-benchmark`.

The [retained main-branch report](evidence/postgres-application-benchmark-2026-10-27.json)
records PostgreSQL 16.14 / READ COMMITTED,
1000 independent refunds and receipts with 8 workers, and one effect/receipt
in each contention scenario. All invariants passed. It measures application
transactions over the CI database connection, excluding HTTP, LLM, Qdrant and
production-network latency; it is not whole-platform capacity or a production SLO.

### 6. Add structured decision compiler contracts

**Priority:** P2
**Category:** Correctness / maintainability
**Status:** TODO
**Classification:** Architectural debt
**Estimated effort:** M

#### Problem

Some resumable workflow semantics appear to infer fields from human-readable
reason strings, for example parsing `missing refund reason` to derive
`missing_fields=["reason"]`.

#### Why it matters

Presentation copy is not a stable machine contract. A wording change can
silently alter workflow state, clarification behavior, or execution readiness.

#### Target behavior

The compiler emits structured fields directly, including:

- `reason_code`
- `missing_fields`
- `clarification_fields`

Human-readable `reason` remains presentation-only. No workflow or execution
control logic parses it.

#### Scope and implementation direction

- Audit compiler result schemas and all consumers.
- Add typed fields while preserving bounded operator/customer projections.
- Migrate workflow lifecycle, clarification, and projection consumers to the
  structured fields.
- Keep compatibility adapters only at external boundaries that require them.

#### Acceptance criteria

- No execution or workflow transition depends on parsing human-readable reason
  text.
- Missing-field and reason-code values are typed and validated.
- Projection copy can change without changing workflow semantics.
- Existing confirmation, revalidation, policy, and idempotency behavior is
  unchanged.

#### Tests and evidence required

- Compiler contract tests for every decision state.
- Missing-field and clarification regression tests.
- Property/fixture tests with changed presentation wording.
- Operator projection compatibility tests.

### 7. Evaluate a tool-owned revalidation contract

**Priority:** P2
**Category:** Maintainability / correctness
**Status:** TODO
**Classification:** Architectural debt; audit before refactor
**Estimated effort:** M

#### Problem

Revalidation currently knows specific risky tools centrally. As the registry
grows, adding a new Risk-2 tool may require changes in multiple central
switch-like locations.

#### Why it matters

Distributed registration increases the chance that a new confirmation-required
tool is missing business-state revalidation or is classified inconsistently.

#### Target behavior

Each tool has one obvious typed declaration of its revalidation behavior, for
example a `revalidate` strategy on `AgentToolDefinition` or an equivalent
registry-owned abstraction. The execution authority, confirmation binding,
business revalidation, and idempotency boundaries remain centralized where
they are security-critical.

#### Scope and implementation direction

- Inventory current tools, risk classes, and revalidation call sites.
- Map which semantics are genuinely tool-owned and which must remain global.
- Design a typed strategy/metadata contract before refactoring.
- Add a new-tool contract test that fails if required revalidation is omitted.
- Migrate incrementally only if the ownership boundary becomes clearer.

#### Acceptance criteria

- Adding a confirmation-required tool has one discoverable typed declaration
  for revalidation.
- No tool can bypass confirmation, authority, policy, target checks, or
  idempotency by omission.
- Existing tool behavior and exact pending-action binding remain unchanged.
- Registry validation rejects incomplete Risk-2 definitions.

#### Tests and evidence required

- Tool registry completeness tests.
- Revalidation tests for every current mutation tool.
- New-tool fixture proving missing metadata fails closed.
- Replay and altered-target regression coverage.

### 8. Establish repository and release governance

**Priority:** P2
**Category:** Governance / release engineering
**Status:** TODO
**Classification:** Evidence/governance gap
**Estimated effort:** S

#### Problem

At the 2026-10-27 reconciliation, the GitHub branch API still reports
`main.protected=false`; the required-check/force-push governance work remains
open. The earlier unsigned-latest-commit observation is historical: merged
main revision `0748e44` has a valid GitHub-verified signature. That individual
signature does not establish an enforced commit-signing or release-tag policy.

#### Why it matters

Strong tests do not prevent a release from bypassing them if the branch and
release process does not require the relevant checks. This is governance, not
runtime architecture.

#### Target behavior

The normal release path protects `main`, requires critical CI checks, disallows
force pushes, and uses a documented PR/release process. Signed release tags
should be adopted where practical; commit signing policy should be explicit
rather than assumed.

#### Scope and implementation direction

- Audit branch protection and required-check configuration.
- Define the minimal required backend, frontend, E2E, and security/release
  evidence checks.
- Disallow force pushes to the release branch.
- Document PR, release-tag, and signing expectations.
- Keep emergency procedures explicit and auditable.

#### Acceptance criteria

- A normal release cannot bypass the required automated safety/test gates.
- Main branch protection and force-push policy are verifiable.
- Required checks are named and match the current CI jobs.
- Release tags and signing expectations are documented.

#### Tests and evidence required

- Repository settings audit/export.
- Protected-branch negative-path test where supported.
- CI check-name review.
- Release checklist update with no invented status claims.

### 9. Improve frontend operator state ownership and stale-request safety

**Priority:** P2/P3
**Category:** Correctness / maintainability
**Status:** TODO
**Classification:** Architectural debt with a potential stale-state bug
**Estimated effort:** M

#### Problem

`App.tsx` has accumulated authentication, health, runtime configuration,
memory, recent runs, demo runs, route handling, chat, investigation state, and
navigation responsibilities. A request such as `api.memory(customerId).then(setMemory)`
can render stale customer A data after the operator has switched to customer B:

```text
A starts
B starts
B finishes
A finishes -> stale A state may render
```

#### Why it matters

Operator projections are trusted operational evidence. Stale memory or run data
can confuse investigation and scope review even if backend authorization is
correct.

#### Target behavior

Rapid customer/context changes cannot commit a response for an obsolete
selection. Canonical decision, authority, execution, validation, and security
state continues to come from the backend projection; the frontend does not
reconstruct those states from unrelated fields.

#### Scope and implementation direction

- Identify asynchronous requests keyed by customer, conversation, run, or
  route selection.
- Use `AbortController`, request generation/version IDs, or focused hooks to
  cancel or ignore stale responses.
- Extract ownership into focused hooks only where it improves correctness.
- Avoid a framework/query-library migration unless it is justified by the
  request graph and current maintenance burden.

#### Acceptance criteria

- Switching customers or runs during in-flight requests cannot display stale
  data.
- Unmounted views do not commit obsolete responses.
- Loading/error states are scoped to the active request key.
- Backend projection remains the sole authority for operator decision fields.

#### Tests and evidence required

- Race test with delayed A/B responses.
- Rapid route/customer switching test.
- Operator projection contract tests ensuring no frontend inference.
- Refresh/recovery regression remains green.

### 10. Benchmark graph compilation before refactoring

**Priority:** P3
**Category:** Performance / maintainability
**Status:** TODO
**Classification:** Future optimization; measure first
**Estimated effort:** M

#### Problem

`AgentRuntime.run()` appears to rebuild or compile the graph per invocation
because node closures contain request-specific dependencies. A future design
could use an immutable compiled graph plus a per-run dependency/context
container, but the benefit is not yet established.

#### Why it matters

Premature graph reuse could weaken request/session isolation. Conversely, if
compile time is material, it may add avoidable latency under concurrency.

#### Target behavior

Measure graph build/compile time, total request latency, memory impact, and
concurrency behavior before deciding whether to refactor. If compile overhead is
negligible compared with provider latency, document the decision and leave the
architecture unchanged.

#### Scope and implementation direction

- Add measurement at existing runtime boundaries without changing semantics.
- Compare cold and repeated invocation cost.
- Measure provider, RAG, policy, and tool stages separately where telemetry
  already supports it.
- Only then evaluate immutable graph plus per-run context injection.

#### Acceptance criteria

- A benchmark report identifies compile time and its percentage of request
  latency.
- Concurrency and memory impact are measured or explicitly unavailable.
- Any refactor preserves request/session isolation and workflow semantics.
- No optimization is merged solely because the graph is rebuilt.

#### Tests and evidence required

- Benchmark under representative deterministic and real-provider timing
  profiles where available.
- Concurrent-session isolation test.
- Pending workflow and idempotency regression after any refactor.

### 11. Reduce CI warning debt

**Priority:** P3
**Category:** Maintainability / release engineering
**Status:** DONE — warning cleanup, issue #27
**Classification:** Confirmed warning debt — fixed

#### Confirmed problem and resolution

The baseline backend CI emitted 11780 warnings: 11779 calls to deprecated
`datetime.utcnow()` through SQLAlchemy defaults/onupdate, plus one Starlette
TestClient legacy-transport warning. Strict resource checking also exposed
unit tests that relied on garbage collection to remove temporary port fixtures.

Models now use an explicit naive-UTC clock helper, preserving database column
semantics, historical rows, serialized timestamp formats, and memory TTL
comparisons. TestClient uses development-only `httpx2`; production HTTP clients
remain on their existing dependencies. Smoke fixtures are closed explicitly,
including constructor rejection and mocked cleanup paths.

#### Completed acceptance and evidence

- [x] Warning categories and before/after counts are recorded in the
  [backend warning baseline](backend-warning-baseline.md).
- [x] Full local backend suite: **1192 passed, 1 skipped, 0 warnings** with real
  PostgreSQL enabled and `-W error` for every warning category.
- [x] SQLite and PostgreSQL regressions verify insert/update defaults, UTC date
  rollover, reload/JSON formats, historical values, and exact memory TTL expiry.
  PostgreSQL connections deliberately use a non-UTC server timezone.
- [x] Known deprecation, resource, and unraisable-exception warning categories
  fail pytest/CI if reintroduced; no warning suppression was added.
- [x] Lint, format, type checking, and dependency audit passed.

The [timestamp regressions](../tests/test_model_timestamps.py), complete suite,
and linked baseline provide the acceptance evidence. This closes backend test
warning debt; GitHub Actions' own Node notices are a separate tooling concern.

### 12. Extend RAG semantic-quality evaluation

**Priority:** P3
**Category:** Quality / evaluation / maintainability
**Status:** TODO
**Classification:** Future evaluation depth; not a current release blocker
**Estimated effort:** M

#### Problem

Current grounding is strong for citation integrity, excerpt validation,
claim/excerpt coverage, numeric conflict detection, and fail-closed
uncertainty. It is not a universal semantic truth judge.

#### Why it matters

A response can be well-cited yet still use the wrong policy applicability,
language interpretation, effective date, or topic. These limits should be
measured rather than implied away by a grounding score.

#### Target behavior

The evaluation suite measures qualitative contradiction, policy applicability,
multilingual semantic retrieval, false abstention, wrong-topic evidence,
freshness, and effective-date conflicts. Runtime grounding remains fail closed;
the task is to improve evidence about quality, not to lower thresholds globally.

#### Scope and implementation direction

- Add a focused evaluation set for eligibility versus processing time,
  multilingual conditions, unsupported questions, contradictory documents, and
  stale/future policy versions.
- Record retrieval relevance, accepted/rejected evidence, abstention, and
  wrong-topic answer rates.
- Keep customer citations separate from bounded operator evidence.
- Document what grounding validates and what it cannot establish.

#### Acceptance criteria

- The evaluation distinguishes grounding integrity from semantic correctness.
- False abstention and wrong-topic evidence are measurable from real artifacts.
- Freshness/effective-date behavior is explicit where the corpus supports it.
- Unsupported questions remain abstentions rather than being made easier to
  answer.

#### Tests and evidence required

- English and Turkish focused RAG evaluation set.
- Retrieval/grounding result artifacts with evidence IDs and acceptance state.
- Human-reviewed qualitative labels for contradiction/applicability cases.
- No fabricated scores when a metric is not exposed.

## Remaining execution order

Completed items 1, 2, 3, 5 and 11, and the related refund race, are closed with
linked evidence above. Do not reopen them as implementation tasks without a
new regression or a separately defined scope change.

1. Finish the remaining evaluation artifact/output alignment in item 4; preserve
   the already-correct README denominator wording.
2. Harden repository governance (item 8).
3. Structure compiler and revalidation contracts (items 6 and 7).
4. Address frontend stale-request/state ownership boundaries (item 9).
5. Benchmark graph compilation before considering a refactor (item 10).
6. Extend RAG semantic-quality evaluation (item 12).

New agent features, additional tools, and multi-agent expansion remain lower
priority than this remaining hardening backlog. PostgreSQL transaction evidence
does not close graph-performance, model-quality, governance or frontend-state work.

## Review labels and release discipline

This backlog uses the following evidence labels:

- **Confirmed issue** — current behavior or documentation is directly
  established by repository evidence.
- **Potential issue / requires audit** — a plausible boundary gap was observed,
  but intended semantics or current enforcement must be confirmed before
  calling it a vulnerability.
- **Architectural debt** — current behavior may be safe, but ownership or
  contract structure creates future defect risk.
- **Evidence/governance gap** — the implementation may be sound, but public
  claims or release controls are not yet sufficiently precise or enforced.
- **Future optimization** — a measurement or cleanup opportunity that is not a
  current safety blocker.

Each item should be closed with evidence against its acceptance criteria. Do not
upgrade a release label, rewrite historical QA results, or broaden a claim
without a corresponding artifact or reproducible test result.
