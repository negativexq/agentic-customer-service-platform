# T4A — Metrics destination, ownership, and privacy design

Date: 2026-10-01

Branch: `docs/otel-metrics-t4a-design`

Status: Design only. No production provider, metric exporter, native metric configuration, or deployment service is implemented by this document.

Source of truth: [T1 contract](fastapi-native-otel-t1-design.md), [delivery plan](fastapi-native-otel-delivery-plan.md), [T2A lifecycle](fastapi-native-otel-t2a-provider-lifecycle.md), [T2B export privacy](fastapi-native-otel-t2b-export-privacy.md), and [T3 tracing](fastapi-native-otel-t3-native-tracing.md).

## 1. Decision and current facts

The user selected **Collector + Prometheus** for T4: backend metrics travel over OTLP gRPC to a Collector, whose Prometheus exporter is scraped by Prometheus. These are two new services. Traces retain the existing direct Jaeger path.

The following are confirmed at the merged T3 baseline. They describe code and deployment configuration, not an inspected running production environment.

| Fact | Repository evidence |
|---|---|
| FastAPI is 0.142.2; OTel API, SDK, protobuf, and gRPC exporter are 1.44.0 | `uv.lock:281–282,829–884` |
| Trace bootstrap owns one SDK provider, batch processor, and privacy exporter | `app/observability/tracing.py::_build_tracer_provider()` and `ObservabilityLifecycle.configure()`, lines 40–122 |
| Enabled bootstrap binds application metrics to the global API meter provider; it creates no SDK meter provider, metric reader, or metric exporter | `app/observability/tracing.py:107–108`; `app/observability/metrics.py::configure_metrics()`, line 309 |
| Native metrics and native logs are disabled | `app/observability/middleware.py::fastapi_telemetry()`, lines 7–18 |
| Domain instruments exist: 28 counters and 13 histograms | `app/observability/metrics.py::build_metrics()`, lines 89–259 |
| Metric tests explicitly inject an SDK provider and in-memory reader | `tests/test_observability.py::telemetry()`, lines 82–99 |
| No-op application instruments and process-local summaries are separate | `app/observability/metrics.py:262–312` |
| The production privacy exporter protects traces only | `app/observability/export.py::PrivacyOTLPSpanExporter`; trace protobuf imports at lines 19–34 |
| Local Compose enables telemetry and sends traces to Jaeger gRPC | `docker-compose.yml:62–64,137–144` |
| The production overlay retains Jaeger and its health dependency | `docker-compose.prod.yml:74–75,118–134` |
| Collector and Prometheus are absent from current Compose | Complete service definitions in `docker-compose.yml` and `docker-compose.prod.yml` |
| Repository launchers configure no extra Uvicorn workers or backend replicas | `docker-compose.yml` backend `command`, line 53; `Dockerfile` `CMD` |

**Existing gap:** production metrics delivery is not configured. In-memory reader assertions prove test recording, not production delivery. An externally configured global meter provider could change behavior, but the repository does not configure one. This is the T1/T3 gap T4 will close, not a regression introduced by native tracing.

## 2. Target topology and deployment boundary

```text
HTTP request / application workflow
    ├─ native and domain tracing
    │     → existing owned TracerProvider + BatchSpanProcessor
    │     → PrivacyOTLPSpanExporter → OTLP gRPC → Jaeger:4317
    │
    └─ native HTTP and application domain measurements
          → shared application MeterProvider API facade
          → bounded recording adapters → one owned SDK MeterProvider
          → PeriodicExportingMetricReader
          → PrivacyOTLPMetricExporter → OTLP gRPC → otel-collector:4317
          → Collector Prometheus exporter :8889/metrics
          → Prometheus scrape and storage

Process-local operational summaries and UI projections remain independent.
```

The facade is a proposed public API adapter around one SDK provider, not a second recording provider. Native and domain instruments must receive the same facade object and reach the same SDK reader/exporter. The adapter exists to enforce label values before SDK aggregation; supplying an unguarded SDK provider to FastAPI would bypass that protection. This refines the T1 shared-provider design without changing ownership or privacy requirements. Public adapter compatibility is an implementation gate, not proven by this design.

| Setting | Decision or proposed default |
|---|---|
| Metrics destination/protocol | User-approved Collector + Prometheus / OTLP gRPC |
| Backend metric endpoint | Proposed `http://otel-collector:4317`, separate from Jaeger |
| Collector receiver | Private Docker network; explicit container listener `0.0.0.0:4317`; no host port publication |
| Prometheus exporter | Collector private `:8889/metrics`; Prometheus scrapes it internally |
| Prometheus UI | If exposed locally, bind host port to `127.0.0.1:9090` |
| Access | Proposed private service network; no public unauthenticated metrics endpoint |
| Retention | Proposed 15 days with persistent Prometheus storage; production retention/storage approval remains required before deployment |
| Images | Select supported Collector/Prometheus releases and pin tags plus digests in the deployment package; no image version selected here |
| TLS/authentication | Private local network may use plaintext; crossing a trust boundary requires explicit TLS/authentication configuration and secret management |

Jaeger can continue to use container port 4317; distinct Docker service names avoid a conflict. Do not publish Collector port 4317 over the existing Jaeger host mapping. Jaeger 4318 exposure does not require switching trace transport. Do not introduce a Collector trace pipeline, Grafana, or trace rerouting in T4.

Collector/Prometheus outages must not change business responses or `/health` and `/ready` semantics. Service healthchecks and deployment startup ordering can protect infrastructure startup without making metrics availability an application readiness requirement.

### Single-producer acceptance boundary

The first delivery targets one backend metrics-producing process. This matches the configured launchers, but external worker/replica configuration has not been inspected. Multiple cumulative producers with identical resource/label sets can collide in one Collector Prometheus export stream. Adding Collector does not establish correct replica aggregation.

T4 deployment acceptance must verify a single producer. Multiworker/multireplica support remains gated on a reviewed aggregation or bounded producer-dimension design. Do not add raw host/pod identity, customer IDs, or arbitrary instance UUIDs as a shortcut. Deployment validation is necessary: one process cannot reliably discover an external replica topology.

Prometheus scrape metadata must also be reviewed. Use fixed application job/instance categories rather than retaining infrastructure addresses as application series labels. Disable automatic promotion of arbitrary resource attributes. Collector self-telemetry is a separate catalog and must not be silently included as application telemetry.

## 3. Configuration and provider lifecycle

Proposed settings, not existing repository settings:

| Setting | Proposed rule |
|---|---|
| `OTEL_METRICS_ENABLED` | Default false; effective enablement is `OTEL_ENABLED && OTEL_METRICS_ENABLED` |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Required when metrics are enabled; no fallback to the generic endpoint that currently points to Jaeger |
| Export interval | Proposed 15,000 ms; validated positive, bounded configuration |
| Export timeout | Proposed 5,000 ms; validated positive, bounded configuration |
| Resource service name | Same approved service category as traces |

`auto_configure=False` and `logs=False` remain explicit. Native `metrics=True` is allowed only after successful metric pipeline initialization and with the supplied owned provider facade. Tracing and operation spans retain `OTEL_ENABLED`. Setting only the metric flag cannot activate telemetry when the master flag is false.

Ownership requirements:

1. Extend the existing lifecycle lock and active configuration fingerprint to include the metrics configuration. Repeated bootstrap reuses one trace pipeline and, when enabled, one SDK metric provider, reader, and exporter.
2. Bind application instruments explicitly through `configure_metrics()` and supply that same provider facade to native FastAPI. Do not register or adopt a global meter provider. An external global meter provider remains untouched, unused by this application's instruments, and unclosed. This explicit injection avoids introducing a second global registration requirement; T2A's external **trace** provider conflict remains unchanged.
3. Validate configuration before factories run. Allocate and validate requested components before publishing application ownership. Failed metric initialization must not leave a reader thread/exporter alive or domain instruments attached to a partial pipeline.
4. Preserve bounded initialization failures. Global trace registration cannot be rolled back through public APIs; failure after registration must retain T2A's terminal-state handling rather than attempt a reset.
5. Application metrics are no-op before successful initialization, when either enabling condition is false, on initialization/registration failure, and after shutdown. Process-local operational summaries remain active.
6. On shutdown, detach recording and close owned components once. Attempt metric and trace flush independently, and attempt both provider shutdowns even if one flush fails or raises. Retain runtime → checkpoint → database → telemetry cleanup order.
7. Keep the existing trace flush budget. Define an explicit metric flush budget and account for both in the runtime shutdown budget; flush timeouts alone do not prove a total shutdown bound. Restart after owned shutdown still requires a new process.

Do not use private OTel global resets, private provider state, SDK encoder helpers, or exporter monkeypatches. The production facade must implement the public API surface it exposes; unknown instruments must return supported no-op implementations rather than cache arbitrary names. Tests must prove that facade shutdown delegates to its single owned SDK provider once.

## 4. Metric privacy before aggregation

Trace privacy catalogs cannot be copied blindly: metric labels use different keys, and trace-safe UUID correlation is prohibited as metric dimensions.

Confirmed label risks to address before export is enabled:

| Call site | Current input | Required target behavior |
|---|---|---|
| `app/agent/graph.py:461–464` | Escalation priority comes from tool arguments | Approved priority membership only; otherwise fixed `unknown` |
| `app/agent/nodes/memory_action.py:69–72` | Rejection `reason` uses service result strings | Approved reason category; no free text |
| `app/memory/policy.py:189` | DLP reason includes joined detected-type details | Map recognized `dlp_restricted_content:` results to fixed `dlp_restricted_content`; discard suffix |
| `app/agent/nodes/understand_request.py`, `retrieve_knowledge.py`, `execute_tool.py` | Resilience service identity derives from provider/retriever/tool names | Explicit known service catalog, not arbitrary class/name strings |
| `app/observability/metrics.py::record_tenant_scope()` | Decision/status strings accepted directly | Known outcome membership; never tenant identity |

These inputs are not evidence of raw content currently being exported. They show that metric recording presently lacks a boundary enforcing bounded values. Preserve domain business/UI data; cleanup should affect metric categories only.

The recording adapter must enforce an instrument-specific schema:

- Approve the two scopes `agentic-customer-service-platform` and `fastapi`, then exact instrument names/types/units. Reject unknown names/scopes before SDK caching.
- Allow only documented keys per instrument. Use exact enum/catalog membership for tool, provider, backend, node, status, policy outcome, risk, principal, dependency, service, priority, and error categories.
- Limit category strings to T1 bounds, but **do not truncate sensitive free text into an accepted label**. Unknown values map to a fixed category where defined; otherwise drop the measurement. Define that behavior per field in implementation tests.
- Prohibit all identities, correlation UUIDs, memory keys, thread/action IDs, URLs, addresses, credentials, content, and raw exception descriptions as labels. Hashing does not make them acceptable.
- Reject non-finite measurements. Enforce semantic input ranges: nonnegative counter increments/durations/counts, confidence/coverage in [0,1], and signed native active-request increments. Do not clamp legitimate cumulative totals to a per-measurement maximum.
- For HTTP duration, permit registered `http.route` templates, bounded method/protocol/scheme/status, and approved `error.type` categories. Reject arbitrary route-like strings even if short. Missing routes on unmatched requests are acceptable.
- For native active requests, use the same approved label transformation on increment and decrement; preserve signed values. Route/status are not active-request dimensions.

The existing trace catalogs, including `app/observability/privacy.py::SERVICE_IDENTITIES`, can supply reviewed category sets where their meanings match. They do not authorize new metric keys automatically. SDK Views may restrict attribute keys and instrument aggregation as defense in depth; they do not enforce value membership. [Public View API](https://opentelemetry-python.readthedocs.io/en/stable/sdk/metrics.view.html), [matching 1.44.0 View source](https://github.com/open-telemetry/opentelemetry-python/blob/v1.44.0/opentelemetry-sdk/src/opentelemetry/sdk/metrics/_internal/view.py).

Filtering only after aggregation is insufficient: arbitrary values can already create unbounded SDK series, and removing labels can produce duplicate series with identical identities. The final exporter should drop invalid points that escaped recording validation, not merge or relabel independently aggregated cumulative series.

## 5. Final export boundary and supported API choice

Proposed mechanism: a public `MetricExporter` implementation, `PrivacyOTLPMetricExporter`, reads public SDK `MetricsData` fields and constructs generated OTLP metric protobuf messages directly. It sends only that sanitized request through the public gRPC `MetricsServiceStub`. This follows T2B's accepted transport ownership pattern without changing the trace exporter.

The public metric exporter/data model is documented; implementation must import through public SDK modules, not `_internal` encoders. [Metric export API](https://opentelemetry-python.readthedocs.io/en/stable/sdk/metrics.export.html), [matching 1.44.0 point model](https://github.com/open-telemetry/opentelemetry-python/blob/v1.44.0/opentelemetry-sdk/src/opentelemetry/sdk/metrics/_internal/point.py). The upstream source file's internal location does not authorize application imports from it.

Reconstructing public metric data and delegating to the stock OTLP exporter is a possible data-model approach, but its default transport logging conflicts with this project's bounded failure logging: the locked transport logs endpoint/RPC details and may include exception information. Do not inherit its private mixin or suppress this through monkeypatching. [Locked 1.44.0 gRPC transport, failure logging](https://github.com/open-telemetry/opentelemetry-python/blob/v1.44.0/exporter/opentelemetry-exporter-otlp-proto-grpc/src/opentelemetry/exporter/otlp/proto/grpc/exporter.py#L513-L534).

Required final projection:

- Permit only cataloged metric names; reconstruct descriptions, units, scope name/version, and resource values from fixed approved metadata.
- Reconstruct safe `service.name` and fixed SDK diagnostics consistently with traces. Discard arbitrary detector/environment resource attributes, scope attributes, and schema URLs.
- Start with cumulative monotonic Sum for domain counters, cumulative nonmonotonic Sum for native active requests, and explicit Histogram for domain/native durations and diagnostics. Preserve timestamps, temporality, numeric type, monotonicity, and histogram counts/sum/bounds/min/max. Unknown data types or instrument/type mismatches fail closed.
- Validate finite aggregates, integer ranges, ordered finite bucket boundaries, and count/bucket consistency. Do not serialize unsupported recursive objects.
- Explicitly configure the public `AlwaysOffExemplarFilter` on the SDK provider and omit exemplars from final protobuf. Filtered attributes can be retained in exemplars; trace correlation is not approved as a metric dimension or exemplar channel in this delivery. [Exemplar specification](https://opentelemetry.io/docs/specs/otel/metrics/sdk/#exemplar), [locked public SDK exports](https://github.com/open-telemetry/opentelemetry-python/blob/v1.44.0/opentelemetry-sdk/src/opentelemetry/sdk/metrics/__init__.py).
- Preserve reader collection semantics and report export success/failure correctly. Handle OTLP partial rejection boundedly. Preserve gRPC retry/deadline/TLS/headers/compression and shutdown semantics through explicit tests; never log raw RPC details, endpoints, credentials, or exception text.

No raw fallback exporter, console dump, or parallel metric export path is allowed. Resource/scope reconstruction applies to every batch, including data from native FastAPI. Collector filtering is additional protection, not the application's privacy boundary.

## 6. Instrument and backend compatibility

Retain the existing 41 domain instrument names and units. Do not consolidate legacy duplicate retry counter names in this migration. `checkpoint_write_duration_seconds` currently records checkpoint initialization/setup timing (`app/persistence/checkpoint.py:180–193`); retain the name, but do not describe it as proof of measuring every checkpoint write.

Native 0.142.2 metrics are:

| Instrument | Type/unit | Relevant dimensions |
|---|---|---|
| `http.server.request.duration` | Histogram / seconds | Method, scheme, protocol, registered route, response status, bounded error type |
| `http.server.active_requests` | UpDownCounter / requests | Method and scheme; no route/status |

Verified in installed FastAPI `telemetry/_asgi.py::_instruments()` and request completion, lines 200–215,263–277,347–374. The source records duration while the request span is current, reinforcing the exemplar decision. [Tagged FastAPI source](https://github.com/fastapi/fastapi/blob/0.142.2/fastapi/telemetry/_asgi.py).

Health/readiness/UI probes remain eligible for metrics with unchanged semantics. Exclusions are optional observability polish, not required for T4 closure. No domain trace stage or UI correlation change is required.

Collector translates OTLP names and units to Prometheus families. Pin its translation behavior and verify actual counter/histogram names in integration tests before documenting queries. The Prometheus exporter can expose resource/scope metadata; keep resource promotion off and review all emitted metadata families. Its moving documentation is capability evidence, not a selected deployment configuration. [Official Collector Prometheus exporter documentation](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/prometheusexporter).

Pin image versions and validate the exact receiver/exporter configuration against those releases. Bound batching, memory, and any retry queues. Do not enable raw debug exporters or unreviewed persistent telemetry queues. Prometheus retention does not imply Collector delivery durability.

## 7. Delivery sequence and implementation files

| Package | Deliverable | Closure condition |
|---|---|---|
| T4A — this branch | Destination, privacy, ownership, and deployment design | User destination recorded; remaining deployment decisions explicit |
| T4B — suggested `refactor/otel-metrics-provider` | Bounded recording facade, SDK provider/reader, privacy protobuf exporter, lifecycle/settings | Public API compatibility and transport/privacy/lifecycle tests pass; metrics remain opt-in |
| T4C — suggested `infra/otel-metrics-collector-prometheus` | Collector + Prometheus infrastructure/configuration, Compose wiring, operational docs | Pinned services/configuration validated; access/retention and single producer requirements explicit; no application delivery claim |
| T4D — suggested `feat/otel-metrics-delivery` | Application → Collector → Prometheus end-to-end delivery and scrape/query validation | Real measurements visible with approved names/labels; failure/recovery evidence; application/native binding guardrails pass before native delivery is claimed |
| T5 — existing package | Deployment evidence and release validation | Real Jaeger/TLS, SIGTERM and production delivery evidence; no claims of completion here |

This split follows the authoritative delivery plan and the T4B closure handoff. It is not completed work. T4C owns infrastructure only; T4D owns application delivery/query acceptance and failure/recovery validation. Native provider binding/enablement and HTTP guardrails remain an explicitly reviewed application-side prerequisite in T4D. Infrastructure must not silently enable native metrics. T5 retains final runtime/deployment evidence.

Expected production files in later packages:

- `app/observability/metrics.py`: provider binding and guarded recording; preserve summaries.
- New focused metric privacy/export modules: catalog, facade, public exporter and protobuf projection. Keep trace `export.py` behavior unchanged; avoid a transport refactor unless separately justified.
- `app/observability/tracing.py`: shared lifecycle ownership, fingerprint, failure cleanup, both-signal shutdown.
- `app/observability/middleware.py` and `app/main.py`: explicit native metric provider and effective flag.
- `app/core/config.py` and `.env.example`: separate metric settings.
- Narrow metric call-site category cleanup where required; no business/projection rewrite.
- Later Compose and new Collector/Prometheus configuration files, documentation, and tests.

Existing locked OTel/protobuf/gRPC packages provide the planned interfaces. No dependency change is assumed; any required change discovered during implementation needs a separate justification. FastAPI stays 0.142.2.

## 8. Required evidence before T4 closure

1. Deterministic overlapping bootstrap proves exactly one SDK meter provider, reader, and exporter; native and domain calls share one facade/backing provider. Test partial initialization cleanup, conflicting configuration, and repeated shutdown without private global resets.
2. Both flags, disabled startup, external global providers, initialization failure, and post-shutdown produce no application metric export. Business HTTP, summaries, and projections remain functional. Trace ownership/conflict behavior remains unchanged.
3. Real SDK measurements prove all 41 domain instruments retain expected values/units and the two native metrics record. Active requests return to zero; duration histograms retain valid bucket/count/sum semantics. No native logs or auto-configuration are introduced.
4. Recording tests demonstrate bounded series for repeated unknown/private labels, unknown instruments/scopes, and route-like secrets. Test unknown behavior per field, dynamic DLP reason normalization, invalid booleans/numbers, NaN/Inf, and signed active measurements.
5. Decode the final `ExportMetricsServiceRequest`, scan all metadata, labels, resource/scope fields, points and exemplars for raw, URL-encoded and JSON-serialized privacy sentinels. Include identity, UUIDs, tokens/cookies, body/prompt/model/tool/memory data, and exception/status text across multiple metrics in one batch.
6. Exercise real local gRPC delivery, failure/partial rejection, bounded transport logs, deadlines/retries, TLS/auth headers, compression, flush/shutdown races, and no duplicate/raw export. Reader injection alone is insufficient.
7. Query actual Prometheus samples after real backend requests through Collector. Require expected native and representative domain families/values; a reachable empty `/metrics` endpoint is not delivery evidence. Inspect target/scope metadata and translated names.
8. Simulate unavailable Collector without changing HTTP/business results or health semantics. Verify queue/memory bounds and shutdown of both pipelines. Run existing trace privacy, lifecycle, native HTTP/domain/projection suites unchanged in their contracts.
9. Run focused and full backend validation, mypy, Ruff/format, lock validation if relevant, and whitespace checks during implementation. Record exact commands/results; no implementation validation is claimed by this document.

## 9. Risks and remaining decisions

| Area | Risk | Evidence / mitigation |
|---|---|---|
| Privacy and cardinality | HIGH before implementation | Current instruments accept dynamic labels; require pre-aggregation checks plus final protobuf boundary |
| Public API adapter | MEDIUM | Proposed facade not implemented; prove native/domain public instrument behavior and identity before binding |
| Metric transport | MEDIUM | Application owns serialization/transport; parity and real gRPC tests required |
| Lifecycle | MEDIUM | Reader thread and second signal add partial-init/shutdown paths; extend T2A guarantees |
| Runtime topology | MEDIUM | Two new services/configurations/storage; pin and test actual releases |
| Multiple producers | HIGH outside initial scope | Same series identities can collide; deployment gate required before scaling |
| Trace/business compatibility | LOW by intended scope | Existing trace path, projections, native logs settings, health semantics remain unchanged; regressions still required |

Open deployment decisions: approve production retention/storage sizing and access/TLS policy, select pinned service images, validate translated series naming, and confirm actual producer topology. None is silently presented as user-approved.

**Recommendation: GO WITH CONDITIONS for T4 implementation.** The selected destination fits a separate metrics path. Proceed with T4B only under the privacy, ownership, adapter, and single-producer gates above. T4A closes no production export or delivery acceptance criterion.
