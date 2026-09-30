# T3 — Native FastAPI tracing migration

Date: 2026-10-01

Branch: `refactor/fastapi-native-otel`

## Implemented boundary

FastAPI is pinned to **0.142.2**, using the plain package. Contrib FastAPI instrumentation and its unused dependencies are removed. Native telemetry receives the provider returned by the existing application bootstrap; it does not create a second export pipeline.

`app/observability/middleware.py::fastapi_telemetry()` now builds the native configuration. `app/main.py` passes that configuration when constructing the application. The module keeps its filename to avoid unnecessary module movement; it no longer instruments or wraps the app.

| Setting | Enabled | Disabled |
|---|---|---|
| tracing | true | false |
| operation_spans | true | false |
| auto_configure | false | false |
| logs | false | false |
| metrics | false | false |
| tracer_provider | Application-owned SDK provider | Application no-op provider |
| meter_provider / logger_provider | Not supplied | Not supplied |
| exclude | Not supplied | Not supplied |

The provider lifecycle, application spans, domain metrics, operational summaries and domain/UI projection model are unchanged. Production still has no metrics export pipeline; T4 owns that existing gap. Health endpoints remain eligible for tracing with unchanged semantics.

```text
HTTP request
  → FastAPI native SERVER span
      → fastapi.dependencies
      → fastapi.endpoint
          → agent.run → domain/workflow spans
      → fastapi.serialization
      → fastapi.background_task (when scheduled)
  → application-owned TracerProvider / BatchSpanProcessor
  → PrivacyOTLPSpanExporter → OTLP gRPC → existing Jaeger topology
```

Operation spans need not be direct parents of every domain span. Tests require one SERVER and a valid descendant graph, allowing intermediary operations. The diagram shows the retained deployment configuration; this package does not establish real Jaeger or deployment delivery evidence.

## Native privacy catalog

The tagged implementation emits the fixed operations `fastapi.dependencies`, `fastapi.endpoint`, `fastapi.serialization` and `fastapi.background_task`, using instrumentation scope `fastapi`. These are now explicitly approved at the final protobuf boundary. Approval requires that scope for native operation names; unknown operation/scope names remain redacted to `unknown`.

Scope version is reconstructed from the installed pinned FastAPI package. Arbitrary supplied versions, scope attributes and schema URLs are not exported. `code.function.name` remains prohibited, even on approved native operations. Domain span names and all existing attribute/resource/event/status filters remain intact. Obsolete contrib scope imports and ASGI send/receive name handling are removed.

Native logs remain disabled because they can capture exception messages and stack traces. Native operation errors still pass through the existing bounded error allowlist; status descriptions are omitted by the privacy serializer. Local FastAPI `TelemetryData` is not read by application processors or exporters.

Source evidence:

- [Tagged public TelemetryConfig and operation behavior](https://github.com/fastapi/fastapi/blob/0.142.2/fastapi/telemetry/_api.py)
- [Tagged native request telemetry and scope](https://github.com/fastapi/fastapi/blob/0.142.2/fastapi/telemetry/_asgi.py)
- [Tagged dependency/endpoint/serialization operations](https://github.com/fastapi/fastapi/blob/0.142.2/fastapi/routing.py)
- [Tagged automatic configuration runtime](https://github.com/fastapi/fastapi/blob/0.142.2/fastapi/telemetry/_runtime.py)

## Active acceptance evidence

`tests/test_observability_native.py` adds eight collected cases. The 25 T2C HTTP cases remain active on native telemetry; no native skip/xfail placeholders are used.

| Criterion | Evidence |
|---|---|
| AC-01/03/04/07/14/18/19 | Existing HTTP guardrails run unchanged in their assertions: one SERVER/domain root, valid parent graph, incoming sampled/unsampled/malformed context, workflow families, API contracts, health and UI correlation |
| AC-02/05 | Nested test routers invoke sync dependency/endpoint once; final export contains exactly one dependency, endpoint and serialization operation. Typed response exclusion is asserted independently |
| AC-06 | Background task checks an Event set only after final ASGI response-body send completes; explicit completion wait precedes flush. Native task/domain spans share the SERVER trace and have a valid ancestor graph |
| AC-08 native | Disabled native route with external SDK provider and OTLP environment records/exports nothing. Existing disabled HTTP chat also preserves local summaries/projections and external ownership |
| AC-09 | Effective runtime configuration, supplied provider identity at native tracer acquisition, one SDK provider/exporter/batch processor, no native log/meter instruments. Contrib packages are absent |
| AC-11/12/13 | Real native framework privacy tests retain header/path/query/body/exception sentinels and final wire scanning. Dependency, endpoint, serialization and background chained failures retain ERROR status without private exception/status text |
| AC-17 | Native metrics disabled, instrument acquisition forbidden in the configuration test; domain metrics still record through the injected test reader in the retained HTTP/domain tests |
| T2A lifecycle | Retained lifecycle suite plus HTTP registration/disabled/shutdown/failing-flush cases run with native telemetry |
| Native catalog | Additional privacy test preserves exactly the fixed operations, rejects unknown operations and spoofed scopes, and strips function/content/scope sentinels |

The native configuration test spies on a tagged FastAPI runtime function solely to observe effective settings and execute the original implementation. It does not reset private OTel globals or modify production exporter factories. The HTTP harness uses public OTel provider registry seams, real batching/privacy serialization and a captured gRPC boundary; T2B's real local gRPC transport test remains active.

The wire decoder now preserves actual protobuf status messages, correcting the non-independent decoded-description assertion identified in T2C review. Native exception tests also inspect protobuf status messages directly.

## Dependency and scope review

The controlled lock operation changes FastAPI 0.141.1 to 0.142.2 and removes only `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-asgi`, `opentelemetry-instrumentation`, `opentelemetry-util-http`, `asgiref` and `wrapt`. No other resolved package version changes; Starlette, Pydantic, httpx and OTel SDK remain locked at their previous versions. FastAPI now depends directly on the existing OTel API.

No Docker/Compose, endpoint/transport, lifecycle owner, metrics export, application domain call site or UI projection implementation is changed. Existing unrelated screenshots/assets/scripts are excluded. Historical T1/T2 records describe their original baseline and are not rewritten as native evidence.

## Validation

| Check | Command | Result |
|---|---|---|
| Focused observability/lifecycle/health | `.venv/bin/pytest -o addopts='' -q tests/test_observability_native.py tests/test_observability_http_guardrails.py tests/test_observability_privacy.py tests/test_observability_transport.py tests/test_observability.py tests/test_observability_lifecycle.py tests/test_health.py` | 146 passed, 480 warnings |
| Full backend | `.venv/bin/pytest -o addopts='' -q` | 967 passed, 11,500 warnings |
| Types | `.venv/bin/mypy app tests evaluation scripts` | No issues in 319 source files |
| Lint | `.venv/bin/ruff check .` | Passed |
| Format | `.venv/bin/ruff format --check .` | 377 files already formatted |
| Whitespace | `git diff --check` | Clean |

Nine new collected cases are active: eight native acceptance cases and one native privacy catalog case. All existing backend cases still pass; no tests were deleted or marked skipped/xfail for this migration.

## Limitations and next packages

- The native operation/scope catalog is deliberately version-coupled to FastAPI 0.142.2. Future upgrades require active operation/privacy tests and catalog review.
- Existing Starlette TestClient/httpx and datetime deprecation warnings remain; this package does not change those dependencies or APIs.
- Known dependency-audit findings in the unchanged PyJWT/urllib3 lock entries are outside this migration.
- Real deployment smoke, SIGTERM budget and Jaeger delivery remain T5. Production metrics export remains T4.
- Commit/push/merge and closure review are separate actions.
