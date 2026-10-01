# T4D — Native binding and application metrics delivery

Branch: `feat/otel-metrics-delivery`. Base: merged T4C, `c5929c1`.

This package binds FastAPI native HTTP metrics to the existing application-owned metrics facade and verifies local application → Collector → Prometheus delivery. Source of truth: [T1](fastapi-native-otel-t1-design.md), [delivery plan](fastapi-native-otel-delivery-plan.md), [T4A design](fastapi-native-otel-t4a-metrics-design.md), [T4B provider](fastapi-native-otel-t4b-metrics-provider.md), and [T4C infrastructure](fastapi-native-otel-t4c-metrics-infrastructure.md).

## Ownership and enablement

```text
native HTTP measurements + application/domain measurements
  → same PrivacyMeterProvider facade
  → one SDK MeterProvider / PeriodicExportingMetricReader
  → PrivacyOTLPMetricExporter → OTLP gRPC otel-collector:4317
  → memory_limiter → resource cleanup → batch → Prometheus exporter:8889
  → Prometheus scrape/storage/query

native/domain traces → existing owned trace pipeline → Jaeger:4317
```

`app/main.py` supplies `get_meter_provider()` to `fastapi_telemetry()`. Native metrics are enabled only when both `OTEL_ENABLED` and `OTEL_METRICS_ENABLED` are true and the supplied object is an active, open `PrivacyMeterProvider`. A no-op, external SDK provider, inactive facade, or closed facade cannot enable native metrics. No global meter registration/adoption is introduced. `auto_configure=False` and `logs=False` remain explicit. The trace provider/exporter/processor and their transport are unchanged.

Application domain instrument binding is still owned by T4B bootstrap. Native instruments use that same facade and its bounded recording guard; all final measurements pass through the existing privacy protobuf exporter. Initialization failure and terminal shutdown retain T4B semantics. Cached native instruments become no-op when their facade stops recording. Telemetry-disabled mode remains separate from process-local operational summaries and UI projections.

The trace SDK can resolve a global meter for its own SDK diagnostics while constructing a tracer. Tests permit that existing SDK behavior while rejecting native/application instrument creation through an external global meter. This does not install or adopt an application metrics pipeline.

Defaults are unchanged: metrics profile activation alone does not enable application/native recording. Effective enablement remains opt-in. No dependency, lockfile, Dockerfile, Compose topology, Collector config, or Prometheus config change belongs to T4D.

## Actual local metric translation

Verified with FastAPI 0.142.2 and the exact T4C digest-pinned Collector 0.161.0 / Prometheus 3.15.0 images on Docker Desktop linux/arm64:

| OTel instrument | Prometheus family | Representative labels |
|---|---|---|
| `agent_runs_total` | `agent_runs_total` | No application labels at the current call site |
| `tool_calls_total` | `tool_calls_total` | `tool_name=get_order`, `status=executed` |
| `policy_decisions_total` | `policy_decisions_total` | `policy_outcome=allow`, `risk_level=0` |
| `agent_run_duration_seconds` | `agent_run_duration_seconds_bucket`, `_count`, `_sum` | `status=ok` |
| `http.server.request.duration` | `http_server_request_duration_seconds_bucket`, `_count`, `_sum` | Bounded method, route template, status, protocol and scheme |
| `http.server.active_requests` | `http_server_active_requests` | Bounded method and scheme; no route/status |

`agent_runs_total` is recorded without attributes in `AgentRuntime.run()`; the catalog's permission for a status label does not mean the call site emits one. This package preserves that behavior. Histogram buckets/count/sum are retained. Native active requests are a cumulative nonmonotonic Sum and return to zero after completed requests.

Prometheus adds its fixed scrape `job=application-domain-metrics` and `instance=otel-collector:8889`. Collector resource cleanup removes resource-derived labels/metadata before exposition; the backend-to-Collector safe OTLP resource remains unchanged. This acceptance supports one backend process only. Multi-worker/replica cumulative aggregation remains out of scope.

Example queries verified by the smoke runner:

```promql
agent_runs_total
tool_calls_total{tool_name="get_order",status="executed"}
policy_decisions_total{policy_outcome="allow",risk_level="0"}
agent_run_duration_seconds_count{status="ok"}
http_server_request_duration_seconds_count{http_route="/agent/chat",http_request_method="POST",http_response_status_code="200"}
http_server_active_requests
```

## Deterministic tests

`tests/test_observability_native_metrics.py` uses the existing real FastAPI/lifespan/router/business harness and a real local gRPC metrics service. Captures are decoded final `ExportMetricsServiceRequest` protobufs from `PrivacyOTLPMetricExporter`, not fabricated native instruments or an alternate raw exporter.

Coverage includes:

- One SDK meter provider, reader and exporter factory invocation; native and domain measurements share the owned facade. Trace ownership remains one provider/exporter/processor.
- Successful chat: domain counters/histogram plus actual native HTTP duration and active-request measurements; HTTP/domain trace graph remains intact.
- Async Event-gated overlapping requests: final OTLP observes active count two, then one, then zero as handlers are released individually. Cancellation and escaped error also leave every active label set at zero. No sleeps or scheduling assumptions prove that contract.
- Authentication failure, invalid body, unknown route and escaped endpoint exception: actual HTTP statuses retained, nonempty final native measurements, private content absent, no exemplars or unsafe scope/resource fields.
- Repeated unknown raw paths aggregate into one bounded label set; known order IDs retain only the registered `/orders/{order_id}` template.
- Master/metric disabled combinations with endpoint environment variables and an external global provider: no owned/native export, business requests succeed, external controls remain usable after cleanup.
- No-op/external/inactive/closed providers cannot enable native metrics.

`tests/test_observability_metrics_http.py` retains its controlled real-unavailable-endpoint failure test. The second business request completes while export has not returned, proving request execution does not wait for export success. Its prior domain-only assertion now requires both native instruments because T4D deliberately enables them in the enabled pipeline. Other T2/T3/T4B contracts are unchanged. The security closure adds tests/test_safe_access_logging.py; metric provider/exporter and trace transport behavior were not redesigned.

`tests/test_otel_metrics_delivery_smoke.py` guards the disposable project namespace, explicit internal destination, ephemeral ports, and substring/encoded privacy scans.

## Real infrastructure delivery and recovery

Run from the repository root:

```sh
python3 -m scripts.otel_metrics_delivery_smoke --project t4d-metrics-local-validation
```

The runner uses a disposable `t4d-metrics-*` project, existing integration fixture/provider configuration, dynamic host ports, exact pinned infrastructure, one backend worker/replica, and explicit metrics opt-in. It starts backend/dependencies/metrics services; it does not require the frontend or an external LLM. Only this isolated project's volumes are removed on exit.

The runner verifies:

1. No domain counter series before the first business request.
2. A real authenticated order-read request executes the tool and persists a projection with a trace ID.
3. Actual Prometheus families, labels and values: agent/tool/policy counters and duration count equal one, native chat duration count equals one, active requests return to zero.
4. Raw Collector samples and Prometheus queries independently verify request counter/count deltas of one from pre-request baselines. Duration sums increase finitely, buckets are monotonic, and the +Inf bucket equals count. Both captures are nonempty and exclude representative message, identity, conversation, bearer, query, encoded and serialized sentinels. The Prometheus series API is also scanned. Resource/scope metadata families are absent from raw exposition.
5. The projection's trace ID is retrievable through the existing direct Jaeger path with `agent.run` and `tool.execute` spans.
6. Collector stop: a second request still succeeds, health/readiness and operational count remain correct, metrics export reports a bounded failure, and Jaeger still receives the request trace.
7. Collector restart: a third request succeeds, Prometheus sees cumulative domain counter/histogram count three and native chat count three, and Jaeger receives the recovery trace.

The cumulative counter/histogram assertions establish recovery in this controlled scenario. They do not claim durable replay of every measurement during an outage; Collector has no persistent queue. The runner uses deadline-bounded polling because export/scrape delivery is asynchronous. Factory concurrency and request-thread independence use the deterministic T4B/native tests.

The closure review exposed existing raw query text in Uvicorn access logs. The approved closure fix now suppresses raw Uvicorn access records and emits bounded method, registered route template (or fixed unmatched), numeric status and duration through SafeAccessLogMiddleware. Uvicorn exception records retain a fixed failure message without exception/stack/cause text. Filters are idempotent and use public Python logging APIs; request exceptions still propagate normally. Persistent tests protect both boundaries. The smoke scans complete backend, Collector and Prometheus logs during outage and after recovery, not merely export-failure lines. Native OTel logs remain disabled. This is a narrow server logging correction; it does not claim arbitrary third-party logging is universally sanitized.

CI adds `Application Metrics Delivery` after Docker validation. It runs this acceptance with a unique disposable project and always cleans it up. No remote CI success is claimed before a PR runs.

## Validation and remaining boundaries

Local domain-only baseline was first run from pre-binding code: domain metrics delivered, native families absent, Collector outage/recovery and Jaeger delivery passed. Prometheus outage/recovery is not exercised or claimed. The native-enabled run then passed with the same topology. Later stricter PromQL assertions were aligned with the actual existing unlabelled agent counter; application call sites were not changed to fit the test.

Local final validation (2026-10-01):

| Check | Result |
|---|---|
| Combined observability/health/delivery-helper suite | 280 passed |
| Full backend suite | 1100 passed, 1 skipped |
| `mypy app tests scripts evaluation` | 331 source files clean |
| Ruff check / format check | Passed; 338 files already formatted |
| Actionlint 1.7.7 | Passed |
| Exact repo uv 0.11.16 `lock --check` | Passed, using a read-only repository mount and Python 3.12.13 validation container |
| Real Docker domain-only baseline | Passed on pre-binding code |
| Real Docker native-enabled delivery/recovery with strict PromQL controls | Passed |
| `git diff --check` | Clean |

The single skip is `tests/test_frontend_auth_contract.py:28`: no production frontend bundle was built in this worktree. Frontend deployment acceptance remains covered by its existing CI job. Locked dependency versions and infrastructure pins are unchanged. Backend image builds ran `uv sync --frozen --no-dev --no-editable`. Local evidence is arm64; remote CI/amd64 validation remains pending until a PR runs.

Validation commands use the existing merged-main virtual environment with the T4D worktree as the working directory and `PYTHONPATH=.`; no dependency sync or lockfile rewrite was performed locally. The focused command selects `test_observability*` modules (excluding unrelated migration validation modules), `test_health.py` and `test_otel_metrics_delivery_smoke.py`.

T5 still owns real deployment/TLS/auth/access, production capacity policy, SIGTERM/shutdown budgets and release evidence. Local Docker plaintext/private-network delivery is not production TLS acceptance. Delivery of all 41 domain instruments through every business scenario, exporter durability across arbitrary outages and multi-producer aggregation are not claimed by the representative smoke. Existing metric recording/privacy/transport/lifecycle tests continue to protect their wider contracts.

Commit, push and merge require a subsequent user instruction.

## Closure evidence follow-up

The three closure findings were addressed in order: server log privacy, persistent active-request balancing, and raw Collector/Prometheus value evidence. The follow-up adds eight test cases (two server logging cases and six delivery assertion cases); the existing active-request case now covers controlled overlap, cancellation and error rather than one held request only.

The strengthened real Compose runner passed with native metrics enabled, three authenticated business requests, actual Collector stop/start, Jaeger trace delivery, full-service log scans, and series API privacy checks. Review-owned containers/volumes were removed. Combined focused validation is 280 passed; backend validation is 1100 passed with the same existing frontend-bundle skip. Mypy, Ruff check/format and diff checks passed. Dependency/lockfile/Compose/image pins, metric provider/exporter, and trace transport remain unchanged.

Closure verdict: **T4D READY TO CLOSE** for this single-producer local delivery scope. Remote CI/amd64, Prometheus outage/recovery, and T5 deployment/TLS/SIGTERM evidence are not claimed complete. No staging, commit, push or merge was performed.
