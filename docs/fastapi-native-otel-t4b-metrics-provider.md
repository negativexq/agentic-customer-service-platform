# T4B — Owned metrics pipeline and export privacy

Date: 2026-10-01

Branch: `refactor/otel-metrics-provider`

Related: [T4A design](fastapi-native-otel-t4a-metrics-design.md), [delivery plan](fastapi-native-otel-delivery-plan.md), [T1 privacy contract](fastapi-native-otel-t1-design.md).

## Implemented scope

This package implements an **opt-in application domain metrics pipeline**. It does not enable native FastAPI HTTP metrics, add Collector/Prometheus services, or prove deployment delivery. T4C owns Collector + Prometheus infrastructure/configuration and Compose wiring. T4D owns application → Collector → Prometheus delivery, scrape/query validation and failure/recovery evidence. Native binding/enablement and HTTP metric guardrails require separate application-side review within T4D before native delivery is claimed. T5 retains runtime shutdown and production Jaeger/TLS evidence.

```text
Application domain instruments
  → configure_metrics(application-owned PrivacyMeterProvider)
  → public recording adapters with bounded instrument/label catalogs
  → one SDK MeterProvider (exemplars explicitly off)
  → one PeriodicExportingMetricReader
  → PrivacyOTLPMetricExporter
  → sanitized OTLP metric protobuf → public gRPC MetricsServiceStub
  → separately configured metric endpoint

Existing native/domain traces → existing privacy trace exporter → Jaeger
Process-local operational summaries and UI projections remain independent.
```

The metric endpoint is mandatory when the metric flag is active; there is no fallback to the trace endpoint. The backend services in current Compose have no metric flag enabled or metric destination configured. The new pipeline is not active by default.

## Configuration and ownership

| Setting | Default / behavior |
|---|---|
| `OTEL_ENABLED` | Existing master switch; false prevents both owned pipelines |
| `OTEL_METRICS_ENABLED` | False; true requires the master switch and valid metric configuration |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Empty; explicit HTTP/HTTPS gRPC authority required, no credentials/query/path |
| `OTEL_METRIC_EXPORT_INTERVAL_MILLIS` | 15,000; accepted range 1,000–300,000 |
| `OTEL_METRIC_EXPORT_TIMEOUT_MILLIS` | 5,000; accepted range 1–30,000 |

Interval/timeout range checks also run at bootstrap for settings constructed without Pydantic validation. Endpoint/interval validation occurs before trace or metric factories. Disabled metrics do not inspect or connect to their endpoint.

`app/observability/tracing.py::ObservabilityLifecycle` owns both signals under the existing RLock. The active configuration fingerprint now includes enabled metric settings. Repeated identical bootstrap reuses the pipelines; configuration conflicts stay bounded and explicit. `get_meter_provider()` returns only the owned facade or a public no-op provider.

No global meter provider is registered, adopted, or closed. This differs deliberately from the old enabled bootstrap's global meter lookup, which configured no production metric reader/exporter and could accidentally use an externally supplied provider. The global trace registration and external trace-provider conflict contract remain unchanged.

`app/observability/metric_provider.py::PrivacyMeterProvider` implements the public MeterProvider API by composition. There is one backing SDK provider, not two SDK collection pipelines. Approved meters and instruments are cached within finite catalogs; unsupported synchronous and observable instruments remain no-op. Unapproved callbacks never reach the SDK.

Factory-created facades start with recording inactive. Domain instruments may be bound during bootstrap, but remain no-op until successful trace registration and ownership publication. A registration/setup failure therefore cannot export measurements through a partial metric pipeline. Partial construction closes the component already created: exporter, reader, or SDK provider. Failure remains a bounded observability configuration error.

Application metrics are no-op before successful bootstrap, when either switch is false, on initialization/registration failure, and after owned shutdown. Existing process-local operational summary functions remain active.

## Privacy and metric semantics

`app/observability/metric_privacy.py::CATALOG` preserves the existing **41 domain instruments**: 28 counters and 13 histograms, with their names, units and descriptions. The two native metric schemas are prepared for later application-side validation; schema presence is not proof of native HTTP recording.

Recording adapters enforce privacy before SDK aggregation:

- Instrument/type/scope membership is exact. Requested descriptions, versions, schema URLs, units and arbitrary scope attributes do not enter SDK metadata; fixed catalog metadata is used.
- Each instrument has its own label keys and category memberships. Unknown categorical values become fixed `unknown`; unknown keys are removed. Invalid typed labels/routes drop the measurement.
- No raw or hashed identity, run/request UUID, URL/path/query, host/client address, token/header, business content, SQL parameter, or exception text is approved as a dimension.
- DLP reason strings with the recognized `dlp_restricted_content:` prefix become `dlp_restricted_content`; the suffix is discarded. Business results/projections are not modified.
- Non-finite/invalid measurements are no-op. Domain counters and diagnostic counts require nonnegative integers; durations are nonnegative; confidence/coverage stay in [0,1]. Native active-request increments permit +1/-1.
- Registered route templates alone are accepted. Native active-request label transformations are identical on increment and decrement.

`app/observability/metric_export.py::build_metric_request()` is a second, final boundary. It reads documented public SDK point fields and writes generated metric protobuf directly; it does not call private SDK encoders, reconstruct private internals, or mutate source data.

The final serializer:

- Reconstructs approved service/SDK resource attributes, instrument descriptions/units and approved scope names/versions. Drops detector/environment resource data, scope attributes and schema URLs.
- Accepts cumulative Sum with the instrument's correct monotonicity and cumulative explicit Histogram. Preserves point timestamps, scalar numeric types, counts/sum/buckets/bounds/min/max.
- Validates numeric ranges, timing, histogram consistency and bounded ratio aggregates; drops unknown data types or invalid points.
- Drops points with labels that were not already validated. It does not relabel independently aggregated series. Duplicate series identities in one projected batch are dropped together instead of merging cumulative values across resources.
- Omits every exemplar. `AlwaysOffExemplarFilter` is also explicitly supplied to the backing SDK so discarded attributes cannot survive in an exemplar reservoir.

Only public OTel SDK/API imports and generated protobuf/gRPC modules are used. No private global resets or stock exporter monkeypatches exist in production.

## Transport and shutdown

`PrivacyOTLPMetricExporter` owns its gRPC channel and serialization. It follows the accepted T2B trace transport pattern without changing `app/observability/export.py`:

- Metric-specific TLS/header/compression settings take precedence over generic OTLP transport settings; trace-specific settings are not read. HTTPS cannot be downgraded by an insecure environment flag.
- Reader/per-call timeout and configured metric timeout bound the RPC deadline. Retryable gRPC status categories, RetryInfo, channel reconnection and at most six attempts are handled explicitly.
- Retries reuse one sanitized request. Partial rejection returns failure without logging the backend message. Encoding/config/RPC failures return failure with fixed logs; no raw fallback is used.
- Export is serialized, flush waits for an active export, and shutdown interrupts retry waiting and closes the channel once.

The lifecycle detaches both providers and stops metric recording before flush. Trace and metric flush/shutdown are attempted independently; a trace failure does not skip metric cleanup. Retained metric instruments also become no-op after shutdown. Restart still requires a new process.

The existing trace flush timeout is retained; the same shutdown-call budget is supplied separately to metric flush and metric shutdown. This is **not a proof of a total wall-clock bound**. Reader collection, thread join, trace shutdown and both signal budgets must be checked against deployment grace time in T5.

**SDK limitation:** the locked PeriodicExportingMetricReader does not propagate `MetricExportResult.FAILURE` into a delivery guarantee from `force_flush()`. A successful flush return means the SDK flush path completed, not that the destination accepted data. The exporter reports/logs bounded failure; actual delivery requires receiver/query evidence. The local gRPC test checks received protobuf, rather than relying on flush alone. See [locked reader implementation](https://github.com/open-telemetry/opentelemetry-python/blob/v1.44.0/opentelemetry-sdk/src/opentelemetry/sdk/metrics/_internal/export/__init__.py).

## Test isolation and evidence

Four new test files contain **109 collected cases**, including six cases added for final closure evidence:

| File | Cases | Evidence |
|---|---|---|
| `tests/test_observability_metrics_privacy.py` | 63 | All 41 domain instruments; bounded pre-aggregation series; typed/finite checks; final protobuf metadata/content/resource/scope/exemplar scans; encoded/serialized sentinels; cumulative/histogram semantics; duplicate producers |
| `tests/test_observability_metrics_transport.py` | 22 | Public transport retry/deadline/failure/TLS/header/compression contracts, bounded retry count, flush/shutdown interruption, real local gRPC reader/exporter delivery |
| `tests/test_observability_metrics_lifecycle.py` | 23 | Overlapping bootstrap, both flags, external global meter independence, configuration conflicts, partial construction/registration cleanup, both-signal shutdown |
| `tests/test_observability_metrics_http.py` | 1 | Real enabled domain pipeline against an unavailable endpoint, concurrent HTTP success during blocked failure return, traces/projections/summaries, sanitized final payload and bounded logs |

The concurrency test uses a Barrier to start both callers, an Event to hold the **real exporter factory** open, and observation of failed nonblocking acquisition of the production lifecycle lock to prove actual contention. The production RLock still serializes construction. Separate counters prove exactly **one SDK meter provider factory, one reader factory and one exporter factory**. Both callers observe the identical owned facade, backed by one real SDK provider. Repeated bootstrap creates no additional pipeline.

Tests use explicit provider injection and public provider registry seams, never private OTel resets. Existing HTTP scenarios explicitly inject their in-memory test meter after successful enabled bootstrap; they no longer rely on production adopting a global provider. The enabled metrics scenario instead uses the real owned SDK provider, periodic reader, privacy exporter and gRPC channel. Only the existing trace capture seam remains. HTTP/domain assertions and disabled-mode checks are unchanged. New route tests restore the registered route catalog using public application functions.

Native instruments are measured directly through the facade only to prove adapter compatibility; these are not manufactured HTTP requests or claimed FastAPI native metric acceptance. Native HTTP requests with metrics enabled remain application-side work in T4D; T4B does not enable them.

### Final closure evidence

- `test_direct_sdk_writes_cannot_bypass_final_otlp_privacy` writes directly to real SDK instruments without the facade. Unsafe Sum/Histogram series exist in the collected input, then disappear from the serialized-and-decoded OTLP protobuf; safe series survive. A real ObservableGauge is collected but omitted at the final boundary. Raw, URL-encoded and JSON-serialized content and unsafe scope metadata are checked in the final representation.
- `test_enabled_domain_metrics_unavailable_endpoint_does_not_block_http_or_traces` reserves a local TCP port without listening and uses the actual metrics RPC. An Event holds the first real failure return while a second business request completes successfully. Domain metrics, trace graph, projection correlation, health/readiness and process-local summaries remain functional. RPC calls occur outside request threads; final payloads and logs contain no endpoint, credential or business-content sentinel. Failure remains bounded by the configured RPC deadline/retry policy; the deliberate test gate is released independently. No destination delivery is claimed from successful SDK flush completion.
- `test_exporter_stub_construction_failure_closes_allocated_channel_and_trace` fails stub construction after channel allocation and proves exactly one channel close, trace cleanup, no reader/export and safe repeated cleanup.
- `test_metric_enabled_failure_after_trace_registration_is_terminal_and_cleans_once` installs the owned trace provider through the public registry test seam, then raises during registration. The inactive metric pipeline cannot record/export; both signals close once, the registered trace pointer is preserved, and repeated enabled bootstrap deterministically requires a new process. No global reset is attempted.

These additional cases exposed no production defect. Final closure work changes tests and documentation only; production metrics and trace transport behavior were not redesigned.

The first PR CI run exposed a test capture defect on Linux: after `UNAVAILABLE`, the exporter recreates its channel/stub, but the test observed only the original stub and inspected a stale first-request payload. The capture now observes every real stub, including reconnections. The expected cumulative count remains two; no production code or acceptance assertion was relaxed.

## Validation

| Check | Command | Result |
|---|---|---|
| Metrics focused | `.venv/bin/pytest -o addopts='' -q tests/test_observability_metrics_privacy.py tests/test_observability_metrics_transport.py tests/test_observability_metrics_lifecycle.py tests/test_observability_metrics_http.py` | 109 passed, 10 warnings |
| Focused metrics + retained observability/native/HTTP/lifecycle/health | `.venv/bin/pytest -o addopts='' -q tests/test_observability_metrics_privacy.py tests/test_observability_metrics_transport.py tests/test_observability_metrics_lifecycle.py tests/test_observability_metrics_http.py tests/test_observability_native.py tests/test_observability_http_guardrails.py tests/test_observability_privacy.py tests/test_observability_transport.py tests/test_observability.py tests/test_observability_lifecycle.py tests/test_health.py` | 255 passed, 489 warnings |
| Full backend | `.venv/bin/pytest -o addopts='' -q` | 1076 passed, 11,509 warnings |
| Types | `.venv/bin/mypy app tests evaluation scripts` | No issues in 326 source files |
| Lint | `.venv/bin/ruff check .` | Passed |
| Format | `.venv/bin/ruff format --check .` | 387 files already formatted |
| Whitespace | `git diff --check` | Clean |

No Collector, Prometheus, real deployment, or production metrics delivery is claimed by these unit/local transport checks. No lockfile regeneration is needed or performed; dependencies and FastAPI remain unchanged.

## Files and remaining boundaries

Production changes: `.env.example`, `app/core/config.py`, `app/observability/tracing.py`, and new `metric_privacy.py`, `metric_provider.py`, `metric_export.py` under `app/observability/`.

Tests: four new metric suites and narrow explicit injection/real metrics endpoint support in `tests/otel_http_harness.py`. This branch also carries the uncommitted T4A design and delivery-plan handoff documentation from the preceding docs phase.

No FastAPI/version/dependency/lockfile, Docker/Compose, native configuration, trace privacy exporter, domain business module, projection or screenshot change is part of T4B. No commit/push is performed in this phase.

Remaining: Collector + Prometheus infrastructure/configuration and Compose wiring (T4C); application → Collector → Prometheus delivery/query validation, approved series names/labels, failure/recovery evidence and the native binding/HTTP guardrail prerequisite (T4D); final runtime/deployment, shutdown/TLS and release evidence (T5). Multiple producers are not supported by this package's deployment acceptance boundary.

## Repeated T4B closure review

**T4B READY TO CLOSE.** The additional final-export, HTTP failure-isolation and cleanup tests pass alongside the retained observability and full backend suites. No closure blocker was found.

- Ownership remains one SDK MeterProvider, one periodic reader and one privacy exporter, proven under controlled overlapping bootstrap; external meter providers are never adopted, registered or closed.
- Metrics require both `OTEL_ENABLED` and `OTEL_METRICS_ENABLED`; endpoint/environment settings alone cannot enable the pipeline. Before initialization, disabled, failed and closed states use application no-op instruments. Process-local summaries remain active.
- Recording-time catalogs bound labels before aggregation. Final serialized OTLP rejects unsafe direct SDK Sum/Histogram series, unsupported Gauge data, resource/scope identity and exemplars. Sum and Histogram are active/tested; native UpDownCounter adapter support is tested but not active in HTTP; observable/other unsupported types remain outside the approved export contract.
- Transport failure remains isolated from HTTP and tracing. Existing transport tests cover deadline/retry, headers/TLS/compression, partial failures, flush and shutdown. The new HTTP test exercises an actual unavailable endpoint without replacing the metric reader or exporter.
- Native FastAPI metrics remain `False`; the trace exporter, native tracing configuration, dependencies, lockfile and Docker/Compose are unchanged. No raw fallback or second metrics pipeline was found.
- Performance risk remains LOW for approved application instruments: bounded catalogs and pre-aggregation dimensions, linear final projection and one sanitized request reused for retry. Arbitrary direct SDK aggregation is not made cardinality-safe by the final filter; external/unapproved SDK producers are outside the owned application recording contract.
- Nothing is staged. Pre-existing screenshot edits and unrelated untracked demo/security/script artifacts remain excluded; their presence is not T4B scope. Runtime delivery, total deployment shutdown budget and multiple-producer acceptance remain explicit subsequent work.
