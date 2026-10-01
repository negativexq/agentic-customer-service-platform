# FastAPI Native OpenTelemetry Delivery Plan

Date: 2026-09-30

The [T1 architecture and privacy contract](fastapi-native-otel-t1-design.md) records the decisions and acceptance criteria for this plan. Implementation has not started. Design branch: `chore/fastapi-native-otel-design`.

**Objective:** Replace framework instrumentation with FastAPI native OpenTelemetry while preserving domain tracing, application metrics, privacy controls, and operational behavior.

**Baseline:** FastAPI **0.141.1**, contrib FastAPI instrumentation **0.65b0**, an application-owned trace provider, and OTLP gRPC trace export to Jaeger. The repository does not configure a production metrics export pipeline.

**Status:** Design and planning only. No dependencies have been updated, no lockfile has been regenerated, and no tests have been run as part of this phase.

## 1. Delivery scope

The work comprises T1–T5, with T2 divided into three sequential packages:

| Package | Deliverable | Prerequisite |
|---|---|---|
| **T1** | Architecture decisions and privacy contract | None |
| **T2A** | Provider lifecycle | T1 |
| **T2B** | Export privacy enforcement | T2A |
| **T2C** | Migration guardrail tests | T2B |
| **T3** | FastAPI 0.142.2 native tracing migration | T2C |
| **T4** | Shared metrics provider and production export | Metrics destination selected before T4 starts |
| **T5** | Runtime validation, documentation, and release evidence | T3; T4 for the full scope |

The tracing migration can ship before T4. Such a release must be described as a native tracing migration; native HTTP metrics and production domain metrics remain incomplete.

### Behavior to preserve

- Domain span names and workflow meaning
- Application ownership of providers and exporters
- Trace export to Jaeger over gRPC port 4317
- Correlation between agent trace IDs and UI projections
- Authorization and response filtering
- Semantics of `/health`, `/ready`, and `/ui/system-health`
- Runtime, checkpoint, database, and telemetry cleanup order

## 2. T1 — Architecture decisions and privacy contract

The detailed decisions are recorded in the [T1 design document](fastapi-native-otel-t1-design.md). The following defines the required design outputs.

### Work items

**T1.1 — Provider ownership**

Target ownership:

- The application creates the trace provider.
- Domain helpers and FastAPI use the same trace provider.
- FastAPI does not automatically create exporters.
- The application flushes and shuts down its provider.
- Any future metrics pipeline follows the same ownership model.

Native configuration policy:

```text
auto_configure = false
logs = false
tracing = OTEL_ENABLED
operation_spans = OTEL_ENABLED
metrics = false in T3; conditional on the approved pipeline after T4
```

**T1.2 — Privacy field matrix**

Define permission, transformation, and export rules for each field group:

| Field group | Required policy |
|---|---|
| Prompts, user messages, request bodies | Exclude from export |
| Model output, retrieved chunks, memory content | Exclude from export |
| Sensitive tool arguments | Exclude from export |
| Authorization, bearer/JWT tokens, credentials | Exclude from export |
| Exception messages and stack traces | Exclude raw content |
| Customer, actor, and conversation identifiers | Record explicit permission or transformation decisions |
| URL paths and queries | Define handling of identifiers and sensitive values |
| Domain categories and outcomes | Allow bounded value catalogs |
| `memory.key` | Define value and length restrictions |
| Resource attributes | Review environment-derived attributes as well |

The current `set_safe_attributes()` helper does not implement redaction or an allowlist. Its name is not evidence of privacy enforcement.

**T1.3 — Metrics scope**

Record one delivery scope:

- **Tracing delivery:** Track the existing metrics export gap separately.
- **Full telemetry delivery:** Add a shared SDK MeterProvider, reader/exporter, and a destination that accepts application metrics.

Do not route application metrics to Jaeger before selecting a suitable metrics destination.

### Outputs

- Provider ownership decision
- Native configuration matrix
- Privacy field matrix
- Metrics scope and destination decision, or an explicit deferral
- Test acceptance criteria

### Acceptance criteria

- Provider creation and shutdown ownership are explicit.
- Permitted identifier fields are listed.
- URL and exception privacy behavior can be tested.
- The boundary between tracing delivery and full telemetry delivery is clear.

## 3. T2 — Sequential preparation packages

```text
T2A — Provider lifecycle
    ↓
T2B — Export privacy enforcement
    ↓
T2C — Migration guardrail tests
    ↓
T3 — FastAPI native OTel migration
```

### Existing files in scope

- `app/observability/tracing.py`
- `app/observability/attributes.py`
- `app/main.py`
- `tests/test_observability.py`
- `tests/test_health.py`
- `tests/conftest.py`

Add separate HTTP telemetry or privacy test files where needed. T2A establishes test isolation for OTel's once-only global provider registration. T2A and T2B include focused tests for their own changes; basic validation is not deferred to T2C.

### T2A — Provider lifecycle

**Scope:** Trace provider creation, registration, sharing, and shutdown ownership. Retain the current FastAPI version and contrib instrumentation.

- Repeated bootstrap with the same active configuration must not create another provider or exporter.
- Handle configuration conflicts and an existing external provider according to the T1 contract.
- Shut down the provider the application actually owns and uses.
- Define behavior for flush failure or exception, repeated shutdown, restart, and repeated lifespan execution.
- Isolate global registration tests through separate processes or explicit injection.
- Preserve the distinction between disabled application telemetry and process-local summaries/projections.

**Files:** Primarily `app/observability/tracing.py`; `app/main.py` and metrics disabled-mode wiring if needed; lifecycle tests and fixtures.

**Exit criteria:** No duplicate pipeline; shutdown attempted after flush failure; external providers remain owned by their creator; cleanup order preserved; focused lifecycle tests pass. Evidence covers the application portion of T1 AC-08, AC-10, AC-15, and the SDK portion of AC-16.

### T2B — Export privacy enforcement

**Accepted transport ownership update:** T2B uses an application-owned `PrivacyOTLPSpanExporter` built on public protobuf/gRPC APIs. It retains the application provider, SDK batch processor, OTLP gRPC and Jaeger endpoint; it replaces stock exporter delegation to avoid discouraged SDK span/resource reconstruction. Transport parity and bounded failure diagnostics are T2B acceptance requirements. See [T2B export privacy](fastapi-native-otel-t2b-export-privacy.md).

**Prerequisite:** T2A provides a single owned pipeline and a defined injection boundary for tests.

- Select and implement an SDK-compatible filtering or projection mechanism at the final export boundary.
- Cover attributes, events, status descriptions, span/event names, and resources together.
- Apply the T1 policy to raw identifiers, URL paths/queries, Authorization, and content fields.
- Disable automatic raw exception recording in application helpers while preserving bounded diagnostic outcomes.
- Apply the same export rules to direct `set_attribute()` calls and framework-generated fields.
- Preserve server-generated run/request UUID correlation and the domain trace structure.

**Files:** `app/observability/attributes.py`, `app/observability/tracing.py`, a new export-filter module if required, limited domain call-site changes, and focused privacy tests.

**Exit criteria:** Demonstrate sanitized payloads at the actual exporter boundary; private sentinels are absent from every export field; bounded errors and correlation work; domain span names are preserved. Validate AC-11, AC-12, and AC-13 against the current framework/domain pipeline.

### T2C — Migration guardrail tests

**Prerequisite:** T2A and T2B are implemented and their focused tests pass.

- Establish the current HTTP/domain trace relationship, incoming traceparent behavior, and single SERVER span baseline.
- Protect read, write, confirmation, RAG, memory, and retry scenarios through HTTP as well as direct runtime execution.
- Test API validation, authorization, response exclusions, health semantics, and projection trace correlation.
- Validate lifecycle and privacy enforcement in combined HTTP scenarios.
- Prepare the assertions and test scenarios for native operation spans, nested router wrapping, and native configuration that will be executed in T3.

**Files:** Tests, test-only helpers, and necessary independent fixture corrections. This package does not add production behavior.

**Exit criteria:** Applicable guardrails pass on FastAPI 0.141.1/contrib; native-only criteria are explicit in the T3 handoff. Skipped or expected-failure native tests do not establish migration success. Contrib ASGI send/receive span counts are not target requirements.

Do not install FastAPI 0.142.2 in T2C. Native operation/configuration guarantees remain unverified until active tests run in T3.

## 4. T3 — FastAPI native tracing migration

### File-level change plan

| File | Planned change |
|---|---|
| `pyproject.toml` | Apply the FastAPI 0.142.2 dependency policy and remove the contrib FastAPI dependency |
| `uv.lock` | Resolve dependencies under controlled scope during implementation only |
| `app/main.py` | Configure native `telemetry` and connect the shared provider |
| `app/observability/middleware.py` | Remove the contrib call; retain a native configuration helper only if useful |
| `app/observability/tracing.py` | Expose the shared provider and its lifecycle |
| Tests | Add native HTTP, operation span, context, and privacy assertions |

This architecture does not require `fastapi[standard]`. Retain the application-owned gRPC exporter.

### Work items

1. Upgrade to FastAPI 0.142.2.
2. Explicitly disable native auto-configuration and logs.
3. Bind native tracing to `OTEL_ENABLED`.
4. Supply the same provider to native and domain spans.
5. Remove the `FastAPIInstrumentor` call.
6. Preserve domain span helpers and call sites, subject to the T2 privacy contract.
7. Remove the contrib dependency.
8. Review unrelated dependency changes in the lockfile.
9. Collect test results and runtime evidence.

### Expected trace structure

```text
HTTP SERVER span
    ├─ FastAPI dependency operations
    ├─ fastapi.endpoint
    │      └─ agent.run
    │             └─ domain/workflow spans
    └─ FastAPI response serialization
```

`agent.run` must share the HTTP trace ID and descend from the HTTP span. It need not be a direct child.

### Acceptance criteria

- Exactly one HTTP SERVER span per request
- No repeated endpoint wrapping from nested router inclusion
- Domain spans share the HTTP trace ID
- All required domain span families remain present
- UI projections retain trace ID correlation
- No automatically added exporter or native log pipeline
- Native spans are absent when telemetry is disabled
- API response filtering, validation, and authorization behavior are preserved

## 5. T4 — Shared metrics provider and production export

**This package closes the existing metrics export gap.**

### T4A destination decision and implementation handoff

After the T3 merge, the user selected **Collector + Prometheus**: backend metrics → OTLP gRPC Collector → Prometheus. Traces retain their direct Jaeger path. The [T4A metrics design](fastapi-native-otel-t4a-metrics-design.md) records the design baseline, bounded recording and final export privacy requirements, provider ownership, and remaining deployment decisions. T4A is a design deliverable; the subsequent T4B implementation and validation are recorded separately below. Production end-to-end delivery remains unverified.

Proposed delivery sequence:

1. **T4A:** destination and architecture design.
2. **T4B:** owned metrics provider/reader/exporter, bounded recording, privacy, and lifecycle tests; metrics remain opt-in.
3. **T4C:** pinned Collector + Prometheus infrastructure/configuration and Compose wiring.
4. **T4D:** application → Collector → Prometheus end-to-end delivery, scrape/query validation, and deployment-level failure/recovery evidence.

Native FastAPI metric provider binding/enablement and HTTP metric guardrails remain unimplemented. They require application-side review and validation in T4D before native HTTP metric delivery can be claimed. Infrastructure configuration alone must not activate native metrics or claim application delivery. T5 remains final runtime/deployment and release evidence.

The [T4B implementation record](fastapi-native-otel-t4b-metrics-provider.md) documents the opt-in domain provider/exporter, privacy and lifecycle evidence. Native metrics and infrastructure remain subsequent packages; T4B alone does not close T4.

The [T4C infrastructure record](fastapi-native-otel-t4c-metrics-infrastructure.md) describes the optional metrics profile, pinned Collector/Prometheus configuration, network/access and resource/retention limits. Infrastructure readiness does not establish application delivery or enable native metrics; those acceptance checks remain T4D.

Initial delivery requires a verified single backend metrics producer. Multiple workers/replicas require a separate aggregation or approved producer-dimension design. Production retention/access settings and service image pins remain open until the deployment package. T5 retains release, Jaeger/TLS, and runtime shutdown evidence.

### Files in scope

- `app/observability/metrics.py`
- `app/observability/tracing.py`
- `app/core/config.py`
- `app/main.py`
- `.env.example`
- Compose files, only if required by the destination/topology decision
- Metrics and shutdown tests

### Work items

1. Create an application-owned SDK `MeterProvider`.
2. Configure its reader and exporter.
3. Bind domain instruments to that provider.
4. Supply the same provider to native HTTP metrics.
5. Add metrics destination and endpoint settings.
6. Keep resources consistent across signals.
7. Retain application ownership of flush and shutdown.
8. Verify actual export beyond the test reader.

Keep the process-local operational summary separate.

### Acceptance criteria

- Native HTTP and domain measurements are collected from the same provider.
- A production reader/exporter exists and delivery is demonstrated.
- Metric labels comply with the privacy contract.
- Native metric names are reflected in dashboard expectations.
- Metrics shutdown works alongside trace shutdown.
- Metrics export failures do not change business execution results.

## 6. T5 — Runtime validation, documentation, and release

### Runtime validation

| Scenario | Expected result |
|---|---|
| Normal `/agent/chat` request | One server span, operation spans, and domain trace |
| Invalid body or oversized message | Existing 422 behavior; private input excluded from export |
| Authentication failure | Existing status/challenge; tokens excluded from export |
| Unhandled exception | Bounded telemetry without raw exception content |
| Jaeger unavailable | Business behavior and health semantics preserved |
| Graceful shutdown | Final telemetry flushed and cleanup completed |
| `OTEL_ENABLED=false` | No new native pipeline or export |
| Production overlay | Existing gRPC trace path works |
| Metrics enabled | The actual destination receives measurements |

### Documentation outputs

- Updated observability architecture
- Provider and exporter ownership
- Native settings and disabled-mode behavior
- Metrics destination and remaining limitations
- Privacy contract
- Shutdown behavior
- Changes to framework span and metric names
- Rollback procedure

Health endpoint exclusions are optional follow-up work.

### Release evidence

- Test results
- Example trace tree
- Matching HTTP/domain trace IDs
- Duplicate server span check
- Privacy scan of sanitized exports
- Shutdown delivery evidence
- Metrics export evidence for the full delivery scope
- Dependency and lockfile change summary

## 7. Acceptance matrix

| Requirement | Delivery package |
|---|---|
| One HTTP server span | T3 |
| No repeated router wrapping | T3 |
| Domain context propagation | T3 |
| Operation spans present | T3 |
| Domain spans preserved | T2C–T3 |
| Private content excluded from export | T2B, T2C, T3 |
| Authorization/JWT excluded from export | T2B, T2C, T3 |
| Bounded exception telemetry | T2B, T2C, T3 |
| Reliable provider initialization | T2A |
| Graceful shutdown | T2A, T2C, T5 |
| Production domain metrics | T4–T5 |
| Native HTTP metrics | T4–T5 |
| Health and API semantics preserved | T3–T5 |
| Compose trace topology preserved | T5 |

## 8. Pull request organization

| PR | Contents | Review focus |
|---|---|---|
| **PR 1** | Architecture decisions, privacy contract, and acceptance plan | Scope and contracts |
| **PR 2A** | T2A provider lifecycle and focused tests | Ownership, initialization, flush/shutdown |
| **PR 2B** | T2B export privacy enforcement and focused tests | Final payload, identifier and exception privacy |
| **PR 2C** | T2C migration guardrail tests | HTTP/domain context, API/projection regression, T3 handoff |
| **PR 3** | FastAPI upgrade, native tracing, and contrib removal | HTTP/domain boundary and API compatibility |
| **PR 4** | Shared metrics pipeline | Destination, export, labels, shutdown |
| **PR 5** | Runtime evidence and documentation | Release readiness |

Keep the FastAPI upgrade and native instrumentation transition in the same PR so native defaults cannot become active in an uncontrolled intermediate state.

## 9. Rollback plan

- Keep T3 reversible as a single change to dependencies and app configuration.
- Restore the previous dependencies, lockfile, and contrib instrumentation together.
- Restart with a clean process because instrumentation can patch process-global behavior.
- No business-data migration is expected: domain span names and projection schemas are preserved.
- Keep T4 separate so reverting the metrics pipeline does not require reverting tracing.

## 10. Definition of done

**Tracing migration complete:** T1, T2A, T2B, T2C, T3, and tracing-related T5 criteria are met.

**Full native OTel delivery complete:** All tracing criteria plus T4 and actual metrics export evidence are met.

T1 defines identifier permissions, URL/exception privacy policy, and the initial tracing scope. Metrics destination selection is deferred until T4 starts. The next delivery is T2A, followed by T2B and T2C before T3. This documentation update does not start runtime implementation.
