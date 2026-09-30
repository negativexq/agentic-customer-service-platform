# T1 — FastAPI Native OpenTelemetry Architecture and Privacy Contract

Date: 2026-09-30

Design branch: `chore/fastapi-native-otel-design`

Status: T1 design deliverable prepared. Runtime implementation has not started.

Related document: [Delivery plan](fastapi-native-otel-delivery-plan.md)

## 1. Evidence and design decisions

This document distinguishes current repository facts from target design decisions. The target rules are not claims about current enforcement. The migration is incomplete until the T2/T3 acceptance criteria are met.

| Confirmed repository fact | Code evidence |
|---|---|
| FastAPI 0.141.1 is locked; plain FastAPI is used | `uv.lock:292`, `pyproject.toml:10` |
| Trace bootstrap runs at module import | `app/main.py:18–20` |
| The application creates an SDK trace provider, batch processor, and gRPC exporter | `app/observability/tracing.py:22–34`, `configure_observability()` |
| Bootstrap has no guard against repeated initialization | `app/observability/tracing.py:22–34` |
| The framework wrapper only gates enablement and calls contrib | `app/observability/middleware.py:6–11`, `instrument_fastapi()` |
| Domain helpers use the global tracer and current span | `app/observability/tracing.py:62–75`, `tracer()` / `span()` |
| The attribute helper checks primitive types but does not redact or allowlist strings | `app/observability/attributes.py:7–17`, `set_safe_attributes()` |
| The domain root span contains raw identity fields | `app/agent/runtime.py:159–170`, `AgentRuntime.run()` |
| The API generates request IDs as UUIDs; conversation IDs come from the request | `app/api/dependencies.py:11–21`, `get_execution_context()` |
| The application generates run IDs as UUIDs | `app/agent/runtime.py:155` |
| The root span adds a bounded error event, then re-raises the exception | `app/agent/runtime.py:275–289` |
| Shutdown flushes and then closes the owned trace provider | `app/observability/tracing.py:37–59` |
| Production code obtains the global meter provider without creating a provider, reader, or exporter | `app/observability/tracing.py:34`, `app/observability/metrics.py:89–90,262,299–302`; production source search |
| Tests inject a separate SDK meter provider into domain instruments | `tests/test_observability.py:84–92`, `telemetry()` |
| The process-local operational summary is independent of OTel export | `app/observability/metrics.py:269–295` |

The expectation that no external provider is injected follows from the repository launchers and configuration. A running deployment was not inspected in this phase.

## 2. Scope decision

**K-01: The first migration release delivers native framework tracing.**

- Target FastAPI 0.142.2.
- Move HTTP server, dependency, endpoint, serialization, and FastAPI BackgroundTasks instrumentation to native FastAPI.
- Retain application domain spans and UI projections.
- Address production metrics export separately in T4.
- Explicitly disable native metrics in T3. Existing domain metrics remain defined; the current export gap remains open.
- Disable native logs. This signal decision does not change standard Python application logging.
- Preserve health semantics and Jaeger's gRPC trace topology.
- Defer probe exclusions to optional follow-up work.

T2 explicitly includes provider lifecycle hardening and resolution of the previously identified exception/identity privacy gaps within the acceptance boundary. Exported attributes may change while domain span names and business behavior remain stable.

## 3. Provider ownership and lifecycle

**K-02: The application creates, shares, and shuts down the trace pipeline.**

| Responsibility | Owner |
|---|---|
| SDK `TracerProvider` / `Resource` | Application bootstrap |
| `BatchSpanProcessor` / OTLP gRPC exporter | Application bootstrap |
| Global trace registration | Application bootstrap, once |
| Native FastAPI `tracer_provider` | The same provider instance used by bootstrap |
| Domain tracer acquisition | Application `tracer()` / `span()` |
| Trace context / incoming traceparent | Standard OTel propagation and native framework |
| Native framework spans | FastAPI |
| Domain span names and outcomes | Domain modules |
| Privacy filtering and export boundary | Application |
| Flush and shutdown | Application lifespan |
| Production metrics pipeline | Application in T4; not created in T3 |

### Bootstrap invariants

- The provider is ready before native app configuration. Moving initialization from module import into lifespan is not required by T1.
- Repeated bootstrap with the same active configuration reuses the owned pipeline without creating another exporter.
- A different active configuration is rejected explicitly with a bounded configuration failure rather than silently creating another pipeline.
- If a real external global provider is already registered during production bootstrap, do not silently adopt it, override it, or attempt another registration. Report the conflict with this repository's ownership contract. External provider adoption requires a separate design.
- Test provider injection must not depend on global registration order. Use process isolation or explicit dependency injection.
- Do not treat a shut-down global SDK provider as reusable. Repeated startup in one process needs an explicit lifecycle model. Do not reset global providers through private OTel APIs.

### Shutdown invariants

- Preserve cleanup order: runtime → checkpoint → database → telemetry.
- Attempt trace force-flush with the existing 5000 ms timeout; treat a false return as a flush failure.
- Attempt shutdown after a flush exception or false return.
- Do not shut down a provider owned by another component.
- Repeated shutdown is safe; the owned provider is closed once.
- Cleanup logs contain fixed messages and bounded component/error categories, without raw exception text or stack traces.
- A flush timeout does not bound the total provider shutdown time. Validate the graceful shutdown budget separately in T5.

## 4. Native configuration matrix

**K-03: Explicitly override framework defaults.**

| Native setting | T3 enabled | T3 disabled | After T4 |
|---|---|---|---|
| `auto_configure` | `False` | `False` | `False` |
| `logs` | `False` | `False` | `False` until a separate logs design is approved |
| `tracing` | `True` | `False` | `OTEL_ENABLED` |
| `operation_spans` | `True` | `False` | `OTEL_ENABLED` |
| `metrics` | `False` | `False` | Only with an approved metrics pipeline and telemetry enabled |
| `tracer_provider` | Owned recording provider | No export pipeline | Same trace provider |
| `meter_provider` | Not supplied | Not supplied | Shared application SDK provider |
| `logger_provider` | Not supplied | Not supplied | Not supplied |
| `exclude` | None | None | Probe exclusion is a separate decision |

**K-04: `OTEL_ENABLED=false` disables application telemetry recording and export.**

This target contract is stronger than the current helper behavior: the helper currently always uses the global tracer. T2 must prevent application spans/instruments from exporting even when an external provider exists. The flag does not disable process-local operational summaries or domain UI/business projections. It does not guarantee a universal no-op for third-party instrumentation outside application control.

An OTLP endpoint in the environment cannot override this flag or `auto_configure=False`. Environment sampling settings follow standard OTel behavior; design incoming trace ID and parentage tests to account for sampling independently.

## 5. Export privacy contract

**K-05: Deny content and identity fields by default; explicitly allow diagnostic fields.**

These rules cover native framework and domain spans. UI projections and business storage have separate scopes; this policy does not remove legitimate customer/conversation scope data from the UI.

| Field | Target export rule | Current evidence / required change |
|---|---|---|
| Prompts, user/customer messages, request bodies | Prohibited | Do not export local native TelemetryData; inspect domain call sites |
| Model output / reasoning / provider payloads | Prohibited | Retain `llm.structured_decision` without adding content |
| Retrieved chunks / memory content / embeddings | Prohibited | Limit `rag.*` and `memory.*` to metadata, counts, and outcomes |
| Tool arguments / SQL parameters / business free text | Prohibited | Tool names, operations, risk, and status have separate permissions |
| Authorization / cookies / bearer / JWT / credentials | Prohibited | Do not add header capture or copy exporter authentication headers into telemetry |
| `customer.id`, `actor.id`, `conversation.id`, tenant identifiers | Exclude raw and hashed forms | Requires changing current exports from `runtime.py:164–168`; do not introduce pseudonym hashing in this delivery |
| `agent.run_id` | Permit server-generated UUID correlation; prohibit metric labels | `runtime.py:155,162`; validate a 36-character UUID |
| `request.id` | Permit server-generated/validated UUIDs; prohibit metric labels | API generates UUIDs; validate direct runtime/context injection separately |
| `agent.action_id` | Outside the T3 allowlist | Additional evidence of need is required for this business/workflow identifier |
| `checkpoint.thread_id` | Exclude in T3 | `checkpoint.py:248–252` produces a deterministic truncated hash, not an anonymity guarantee |
| `actor.type`, `actor.roles` | Permit only categories/roles defined in code | Use categories for diagnostics rather than identity values |
| `memory.key` | Exclude | Existing explicit attribute at `memory_action.py:92–94` |
| `url.path`, `url.query`, full URLs | Exclude raw values | Native ASGI adds these fields; filter them at the export boundary |
| `http.route` | Permit only a router-derived template | For example `/customers/{customer_id}`; never fall back to the actual path |
| HTTP method/status/protocol, durations/counts | Permit bounded structural values | Metric labels must not contain identifiers or free text |
| Host/client addresses | Exclude in T3 | Avoid client-controlled values and personal/network identity data |
| Domain node/tool/provider/backend/status/reason categories | Permit explicit catalog values | `graph.py:257–281,427–444`, RAG backend attributes |
| Resilience dependency/service identity | Permit fixed internal categories/catalogs | `retry.py:125–133`; prohibit dynamic identities containing URLs, principals, or tenants |
| RAG confidence/count/coverage and numeric diagnostics | Permit finite bounded numbers and booleans | `answer_generator.py:431–445`; no chunk content |
| Exception message/stacktrace/status description/cause chain | Prohibit raw content | Current gaps: default `span()` recording and runtime re-raise |
| Error category/type | Permit a known bounded category/type catalog | Map unknown exception types to `unknown` |
| Resource attributes | Explicit SDK/service diagnostic allowlist | Include environment detector output in review; exclude command lines, paths, host/user identity |

### Size and value limits

- UUID correlation fields: canonical UUID format, 36 characters.
- Permitted categorical strings: catalog membership and at most 128 characters. Drop overlong/unknown values or use a defined `unknown`; truncation must not retain sensitive content.
- Permitted category sequences: at most 20 elements, each subject to the same value rules.
- Route templates: registered route formats only, at most 256 characters. Do not export actual paths for unmatched requests.
- Numeric fields: finite and within known domain bounds. Do not encode identifiers as numeric metric labels.
- Span/event names: never derived from user input. `agent.<node>` comes from the graph catalog; event names are fixed diagnostics.

These limits are target design choices and are not fully enforced by current code.

### Filtering and exception handling

- Call-site controls and helper allowlists alone cannot cover framework or direct span attributes/events.
- The application-owned final export boundary enforces the contract across all fields of span payloads passing through the BatchSpanProcessor/exporter.
- T2 selects an SDK-compatible mechanism: safe projection of the immutable export representation, including events/status/resources, or equivalent filtering. Do not mutate private OTel fields.
- Do not assume native span hooks exist. The inspected `telemetry` API does not provide a universal attribute-redaction callback.
- Application helpers must not automatically record raw exceptions. Preserve bounded error categories, statuses, and events.
- Do not copy requests, bodies, dependency values, or validation input from native `get_telemetry_data()` into exports.
- Preserve domain span names and trace relationships. Document privacy-related attribute changes in the T2/T3 release notes.

## 6. Metrics scope and destination

**K-06: Disable native metrics in T3; deliver production export in T4.**

- Preserve the current application metric catalog and process-local summary.
- Do not add an OTLPMetricExporter, reader, or backend in T3.
- Report the current export gap as an existing issue, not a native migration regression.
- In T4, native FastAPI and domain instruments share an application-owned SDK MeterProvider.
- Jaeger's trace receiver is not a destination for arbitrary application metrics.
- A metrics destination has not been selected. It does not block the T1 tracing design. Before T4 starts, record the destination, protocol, endpoint, access requirements, and retention policy.
- This document does not select a Collector, Prometheus, or another metrics backend by default.
- Do not claim production metrics work without reader/exporter and flush/shutdown evidence from T4.

## 7. T2A/T2B/T2C and T3 acceptance criteria

This section specifies tests. No tests have been written or run in this phase.

Delivery order: **T2A Provider lifecycle → T2B Export privacy enforcement → T2C Migration guardrail tests → T3 Native migration**. T2A/T2B include focused tests; T2C delivers combined HTTP regression coverage.

| Package | Acceptance ownership |
|---|---|
| T2A | Application-disabled portion of AC-08; AC-10, AC-15, SDK lifecycle portion of AC-16 |
| T2B | AC-11/12/13 on the current contrib/domain export pipeline; preserved UUID correlation |
| T2C | Current pipeline baseline for AC-01/03/04/07/14/18/19; lifecycle/privacy HTTP integration; native-only test handoff to T3 |
| T3 | Native behavior in AC-02/05/06/09/17; native-disabled portion of AC-08; repeat applicable guardrails on the native pipeline |
| T5 | Runtime SIGTERM and exporter delivery evidence for AC-16 |

Do not report native-only assertions as passing on FastAPI 0.141.1. T2C completes the native scenario plan/handoff; active native test success belongs to T3. Skip/xfail results do not establish migration success.

| ID | Scenario | Acceptance result | Evidence |
|---|---|---|---|
| AC-01 | Enabled `/agent/chat` | One SERVER span and one trace per request | In-memory exporter; span kind and trace ID counts |
| AC-02 | Nested router inclusion | Endpoint executes once and its endpoint operation span appears once | Fake runtime invocation count and span parent graph |
| AC-03 | Sync endpoint and dependency | `agent.run` descends from the HTTP span; related domain spans share its trace ID | Parent ID graph; direct child not required |
| AC-04 | Incoming traceparent | HTTP and domain trace IDs follow incoming context | Fixed traceparent; valid parent/span assertions |
| AC-05 | Normal validated response | Dependency, endpoint, and serialization operations are present | Route-specific assertions; do not require operations that did not run |
| AC-06 | BackgroundTasks test route | Task retains the trace ID and runs after the response | Test-only route; deterministic task/order evidence |
| AC-07 | Read/write/confirmation/RAG/memory/retry | Domain span names and bounded outcomes are preserved | Existing observability scenarios and HTTP integration |
| AC-08 | Disabled + OTLP environment + external provider | No application/native recording or export; local summary/projection works | Isolated process or explicit injected providers |
| AC-09 | Auto-configuration disabled | One owned processor/exporter; FastAPI adds no HTTP exporter/log provider | Factory spies and provider lifecycle counters |
| AC-10 | Repeated bootstrap | No second provider/exporter; configuration conflicts have explicit bounded results | Provider factory and shutdown spies |
| AC-11 | Privacy sentinels | Prohibited content absent from attributes/events/status/resources/span names/exported logs | Recursive scan of the final exported representation |
| AC-12 | Query/path/Authorization/cookie | Sensitive query/path/header values absent; template route retained | Identifier routes, unmatched paths, query tokens, bearer/JWT sentinels |
| AC-13 | Escaped/chained runtime and framework exceptions | Bounded error/status retained; message/stack/cause text absent | Escaped domain exception and unhandled API failure |
| AC-14 | Oversized/malformed body and auth failure | Existing 422/401/403/404 contracts and response exclusions preserved | API response assertions and telemetry scan |
| AC-15 | Flush exception/false and repeated shutdown | Shutdown attempted; owned provider closes once; external provider remains open | Lifecycle doubles with injected failures |
| AC-16 | Real SDK flush/shutdown | Final spans reach the exporter; cleanup order preserved | SDK and in-memory exporter; runtime SIGTERM evidence in T5 |
| AC-17 | T3 metrics scope | No new native HTTP measurement pipeline; domain catalog injection tests work | Native metrics disabled assertion and existing metric reader tests |
| AC-18 | Health endpoints | Liveness/readiness/status/response contracts preserved | `tests/test_health.py`; probe trace eligibility without exclusion |
| AC-19 | Domain projection | Projection trace ID matches domain/HTTP trace ID; stage model preserved | UI projection integration assertions |

Privacy assertions must cover URL-encoded forms, JSON serialization, event text, and chained exception representations, not only exact string equality. Legitimate body/response content in API behavior is separate from the export scan.

## 8. Sources and validation limits

Repository privacy sources:

- `docs/security.md:36–38,68–77`: authentication identity/token restrictions.
- `docs/reliability.md:58–63`: message/provider/customer content exclusions.
- `docs/memory-privacy.md:56–61`: memory metric privacy.
- `docs/multi-tenancy.md:40–44`: bounded tenant telemetry outcomes.
- `tests/test_observability.py:148,182,384,413,444`: examples of domain attribute privacy; insufficient evidence for all export fields.

Tagged upstream API evidence:

- [FastAPI 0.142.2 TelemetryConfig and operation spans](https://github.com/fastapi/fastapi/blob/0.142.2/fastapi/telemetry/_api.py): supplied providers, local TelemetryData, and exception recording options.
- [FastAPI 0.142.2 NativeTelemetry](https://github.com/fastapi/fastapi/blob/0.142.2/fastapi/telemetry/_asgi.py): standard context propagation, URL attributes, exception logs, and legacy middleware detection.
- [FastAPI 0.142.2 automatic runtime setup](https://github.com/fastapi/fastapi/blob/0.142.2/fastapi/telemetry/_runtime.py): auto-configuration and exporter ownership.

This API is not yet installed in the repository. API suitability was assessed through upstream source inspection. Repository runtime compatibility remains subject to the acceptance criteria and T5 validation.

## 9. T1 completion and handoff

This document provides the T1 decisions for provider ownership, native settings, privacy, metrics scope, and test acceptance. Update the design before T2 if these decisions change.

The next package is T2A: provider lifecycle and focused tests. T2B then enforces final export privacy, T2C establishes migration guardrails, and T3 performs the native migration. This documentation update does not authorize runtime implementation; implementation has not started.

The outstanding external decision for T4 is the metrics destination. T3 still requires validation of the final export filter mechanism, process/lifespan provider isolation, and acceptance test results. Do not declare T3 complete before resolving those items.
