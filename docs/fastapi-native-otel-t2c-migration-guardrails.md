# T2C — Migration guardrail tests and T3 handoff

Date: 2026-10-01

Branch: `test/otel-migration-guardrails`

T2C adds tests and test-only helpers against the current FastAPI 0.141.1/contrib pipeline. It does not change production code, dependencies, native configuration, metrics export or deployment topology. The [T1 contract](fastapi-native-otel-t1-design.md) remains authoritative.

## Harness and evidence boundary

`tests/otel_http_harness.py::HTTPHarness.start()` assembles an isolated FastAPI instance with the production nested API routers, validation exception handler, contrib instrumentation wrapper and production lifespan. Lifespan constructs a real AgentRuntime with a deterministic decision provider, real memory checkpoint provider and SQLite test session. A test-only transparent ASGI probe records the loop thread; the database dependency and runtime entry record worker threads.

The real observability builder, SDK provider, batch processor and PrivacyOTLPSpanExporter execute. Public registry getter/setter injection isolates once-only OTel registration; no private OTel reset is used. Only the gRPC channel/stub boundary is replaced by the existing protobuf capture. Provider/exporter factory counters establish one pipeline across bootstrap and lifespan validation. An injected in-memory MeterProvider tests domain instruments; it is not a new production metrics pipeline.

This verifies local HTTP → runtime → sanitized OTLP request behavior and lifespan cleanup. It does not claim production Jaeger delivery, Docker smoke, SIGTERM timing or native behavior. T2B supplies the separate real local gRPC transport test; T5 owns deployment evidence.

## Current acceptance coverage

All tests below are in `tests/test_observability_http_guardrails.py`.

| T1 criterion | Current evidence |
|---|---|
| AC-01 | `test_chat_one_server_sync_dependency_context_and_ui_projection`: one SERVER span, one domain root, unique span IDs and one runtime call through the nested production router |
| AC-03 | `request_graph()`: every exported non-SERVER span in the request trace descends from the HTTP SERVER span; intermediary spans are allowed. Sync database/runtime execute outside the ASGI loop thread |
| AC-04 | `test_incoming_traceparent_preserves_http_domain_parentage`: fixed incoming trace and remote parent IDs, sampled flags and domain graph. Separate unsampled and malformed-parent tests protect sampling/projection and fresh context behavior |
| AC-07 | Read/tool, write/confirmation/revalidation, RAG, memory remember/forget and transient LLM retry execute through HTTP. Existing direct-runtime catalog/metric tests remain active |
| AC-14 | Oversized/missing/malformed body and missing/forbidden/cross-customer auth preserve 422/401/403/404. Runtime is not invoked; one sanitized SERVER span remains. Response exclusions are asserted on real read/confirmation responses |
| AC-18 | Health, readiness and UI system health keep healthy/failure semantics; probes remain eligible for tracing. Database failure keeps liveness available and readiness at 503 |
| AC-19 | Runtime/UI trace ID equals HTTP/domain trace ID; request UUID matches dependency output. UI endpoint returns the projection. Fixed stage IDs and canonical event mapping are checked |
| Combined privacy | Final serialized protobuf and rendered protobuf JSON are scanned. Escaped/chained domain/framework failure retains bounded error diagnostics without cause/message/status text |
| Combined lifecycle | Disabled HTTP with an external provider and OTLP environment exports nothing, leaves external ownership intact, and retains summaries/projections. Registration failure prevents startup. Normal/false/exception flush shutdown drains the last request and closes resources in order |
| Context isolation | Two independent chat requests produce distinct HTTP trace IDs and request UUIDs while reusing one owned provider/exporter |

Assertions target one SERVER and the domain parent graph. They do not freeze contrib ASGI send/receive counts, demand a direct HTTP → agent.run edge, or treat framework metrics collected by the injected test reader as production export.

## T3 native-only handoff

These are implementation scenarios for T3, not passing tests in T2C. No skip/xfail placeholders are used to imply native success.

| Criterion | Fixture/scenario to add in T3 | Required active assertions |
|---|---|---|
| AC-02 | Include a test router through two nested prefixes; sync endpoint/runtime invocation counter | One endpoint invocation and one native endpoint operation; one SERVER. No duplicate wrapping when routers are included |
| AC-05 | Test route with sync dependency, typed response model, field exclusion and endpoint counter | Dependency, endpoint and response serialization operations appear for work that ran; operation IDs are unique and descend from the SERVER. Validate the response independently |
| AC-06 | Test-only BackgroundTasks route with task Event and ASGI response-send order probe | Final response body precedes task execution; task retains the request trace ID. Wait deterministically for the task and flush before examining the final request; do not assume a particular direct parent |
| AC-08 native | Repeat the disabled HTTP fixture with OTLP environment and external provider | Native tracing/operation spans disabled; no application export or transport/provider creation; summary and UI projection remain functional |
| AC-09 | Instrument provider/exporter/processor factories and native auto-configuration entry points | `auto_configure=False`, one owned recording provider, one processor/exporter, same supplied tracer_provider; no native-created exporter or logger provider |
| AC-17 | Explicit test meter reader, same read/retry/memory scenarios | Native `metrics=False`, no native HTTP measurement pipeline, application metric instruments still record with injection. Do not introduce a production reader/exporter |
| Native logs/privacy | Chained endpoint/dependency/serialization/background failure with distinct sentinels | `logs=False`; no logger_provider supplied or native log pipeline created. Final protobuf attributes/events/names/status/resources/scopes contain no prohibited raw or encoded content |
| Regression repeat | Reuse current `request_graph()`, final-payload scans and real API scenarios | Applicable AC-01/03/04/07/11/12/13/14/18/19 remain green under native instrumentation |

T3 must use the T1 enabled configuration: tracing/operation spans enabled, auto-configuration/logs/metrics disabled, owned tracer_provider supplied, no meter/logger provider and no new exclusions. In disabled mode tracing and operation spans are also disabled. Validate these effective settings, not just constructor arguments.

Before asserting native operation spans at the final export boundary, inspect the chosen FastAPI version's actual scope/name/attribute output and explicitly approve bounded native entries in the T2B catalogs. The current filter maps unknown names/scopes to `unknown`; inspecting raw SDK spans alone could conceal lost operation diagnostics. Do not relax the privacy allowlist to retain arbitrary function names, paths or exception content.

The native upgrade, contrib removal, native operation tests and catalog adjustment remain T3. Production metric export remains T4; deployment delivery/SIGTERM evidence remains T5.

## Validation

| Check | Command | Result |
|---|---|---|
| Focused HTTP, privacy, transport, observability, lifecycle and health suite | `.venv/bin/pytest -o addopts='' -q tests/test_observability_http_guardrails.py tests/test_observability_privacy.py tests/test_observability_transport.py tests/test_observability.py tests/test_observability_lifecycle.py tests/test_health.py` | 137 passed, 408 warnings |
| Full backend suite | `.venv/bin/pytest -o addopts='' -q` | 958 passed, 11,428 warnings |
| Type checking | `.venv/bin/mypy app tests evaluation scripts` | No issues in 318 source files |
| Lint | `.venv/bin/ruff check .` | Passed |
| Formatting | `.venv/bin/ruff format --check .` | 376 files already formatted |
| Tracked diff whitespace | `git diff --check` | Clean |

The new HTTP guardrail file contributes 25 passing cases. These results establish the current contrib instrumentation baseline; they do not establish native FastAPI operation-span or configuration behavior. The native-only acceptance scenarios above remain required in T3.

## Scope and git handling

Intended files:

- `tests/otel_http_harness.py`
- `tests/test_observability_http_guardrails.py`
- `docs/fastapi-native-otel-t2c-migration-guardrails.md`

No existing test or production file needs modification. Existing unrelated screenshot/demo/security/approval/script workspace changes remain excluded. Commit/push/PR/merge are separate actions.
