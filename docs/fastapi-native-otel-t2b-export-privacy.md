# T2B — Export privacy enforcement

This delivery implements the trace export portion of the [T1 privacy contract](fastapi-native-otel-t1-design.md#5-export-privacy-contract), on top of the merged T2A lifecycle. It retains FastAPI 0.141.1, contrib framework instrumentation, application provider ownership, and direct OTLP gRPC export to Jaeger.

## Export boundary

```text
Current FastAPI/contrib spans + application domain spans
    → application-owned TracerProvider
    → BatchSpanProcessor
    → PrivacyOTLPSpanExporter
    → fresh allowlisted OTLP protobuf request
    → public gRPC TraceServiceStub.Export
    → Jaeger
```

`app/observability/tracing.py::_build_tracer_provider()` installs one `PrivacyOTLPSpanExporter` in the existing owned batch processor. `build_request()` / `project_span()` read public SDK properties and construct only approved protobuf fields. The whole request is built before any RPC; encoding/transport failures return `SpanExportResult.FAILURE` with a fixed warning and no raw fallback.

Transport ownership was explicitly accepted during the T2B SDK support investigation. The application now owns the protocol encoder and gRPC channel/stub lifecycle. It retains OTLP gRPC, the configured Jaeger destination, one provider/exporter/processor, and SDK batching. It does not delegate to the stock `OTLPSpanExporter` or use its private serialization/transport hooks.

## Transport contract

- Explicit application endpoint remains authoritative; `http://jaeger:4317` opens an insecure gRPC channel. HTTPS always selects TLS. Other targets use the standard insecure setting, otherwise TLS.
- Trace-specific environment settings override generic OTLP settings for timeout, insecure mode, headers, compression, and certificate/client key/client certificate paths. Percent-encoded header values are decoded. Export authentication metadata is separate from captured HTTP Authorization and is never copied into span payloads or logs.
- Default timeout is 10 seconds, with a monotonic deadline shared across RPC attempts. Retryable codes match the locked stock exporter's default set; up to six attempts use exponential jitter or server RetryInfo. UNAVAILABLE triggers one channel reconnect on the first failed attempt. Backoff is interruptible by shutdown.
- Nonretryable RPC and unexpected exceptions produce bounded failure diagnostics. Raw endpoint/configuration/RPC details and server response messages are not logged.
- Partial rejection returns FAILURE without retry, avoiding duplicate accepted spans. This deliberately improves on the locked stock exporter's blanket success for a returned RPC response.
- Export calls are serialized. Direct exporter flush waits for an active call up to its timeout; it has no additional buffer and does not establish delivery success. Provider flush retains SDK batch-drain semantics. Shutdown marks the exporter terminal, interrupts backoff and closes the channel once. No post-shutdown export is allowed.
- Private OTel credential-provider plugins and private retry-code environment overrides are not implemented. They are not configured by this repository. SDK internal exporter metrics are not recreated; application/domain metrics and the existing production metrics gap are unchanged.
- Tests establish these behaviors with deterministic transport doubles and a real local gRPC receiver. They do not claim production Jaeger/TLS deployment smoke validation; runtime delivery remains T5.

## Field policy

| Export field | Enforcement |
|---|---|
| Span/event attributes | Explicit key and value catalogs in `privacy.py`; unknown keys and categorical values are dropped |
| Raw/hashed customer, actor, conversation, tenant, action and checkpoint identity | Excluded; no hashing or pseudonymization is added |
| Prompt, message, body, model output, retrieved text, memory content/key and business tool arguments | Excluded, including direct SDK attribute calls |
| Authorization, cookies, bearer/JWT/credentials | Header/content fields are excluded regardless of capture source |
| `agent.run_id`, `request.id` | Only canonical, 36-character UUID strings are permitted; invalid direct context injection is excluded from export |
| Categories and sequences | Catalog membership; strings at most 128 characters; sequences at most 20 elements. Invalid sequences are dropped in full rather than retaining arbitrary content |
| Numeric diagnostics | Strict integer ranges for HTTP status (100–599), risk (-1–3), retry attempts (0–10,000), and counts (0–1,000,000); finite ratios between 0 and 1; booleans only for explicitly allowed flags |
| URL/path/query/host/client fields | Excluded. `http.route` requires exact membership in the registered router template catalog and a maximum of 256 characters |
| Span names | Existing domain catalog retained. HTTP SERVER names are reconstructed from bounded method/template fields. Contrib send/receive names require a registered route or receive a fixed generic name. Other names become `unknown` |
| Event names | Only fixed diagnostic events (`application.exception`, `exception`, `agent.persistence_or_execution_error`, `escalation.created`) survive |
| Exceptions and status | Preserve status code and known error types/categories; exclude messages, stack traces, cause content and status descriptions. Unknown exception types become `unknown` |
| Resource | Reconstruct only trusted configured `service.name` and installed SDK name/language/version. Service names must use a bounded ASCII service slug. Detector/environment identity, command lines, paths, and resource schema URLs are excluded |
| Instrumentation scope | Known module names and installed versions only; unknown scope name becomes `unknown`. Arbitrary scope attributes, supplied versions, and schema URLs are excluded |
| Context and links | Preserve trace/span/parent/link IDs, flags, remote context, kind, and timestamps. Strip vendor tracestate and all link attributes |

Service name is a deployment configuration field, not a user data channel. Catalogs deliberately omit dynamic OIDC role names, custom provider classes, and dynamic dependency identities. Extending a catalog requires reviewing the source and privacy implications; string shape or prefix matching alone does not grant permission.

`app/main.py` registers the final route templates after router inclusion using FastAPI's public `iter_route_contexts()`. This handles the currently installed FastAPI's nested router representation. It never derives permission from a request URL. A dynamically added route requires refreshing the template catalog; until then its raw path is not exported.

## Domain helper and call sites

`set_safe_attributes()` uses the same diagnostic catalogs before recording helper attributes. `span()` disables SDK automatic exception recording and automatic exception status descriptions. On an escaping exception it records an ERROR status without description and an `application.exception` event with a bounded error type, then re-raises the original exception.

The runtime no longer adds raw actor/customer/conversation identity, action identity, or checkpoint thread hashes to its spans/events. Memory forget spans no longer add `memory.key`. Business context, checkpoint identity, authorization, audit storage, and UI projections retain their existing behavior. Valid run/request UUID telemetry correlation and UI trace IDs remain intact.

## SDK compatibility and limits

The locked packages are OTel API/SDK/proto/exporters 1.44.0 and contrib 0.65b0. The installed SDK trace/resource modules, encoder facade and stock gRPC span exporter were compared against tagged v1.44.0 source and matched byte-for-byte.

The [tagged SDK source](https://github.com/open-telemetry/opentelemetry-python/blob/v1.44.0/opentelemetry-sdk/src/opentelemetry/sdk/trace/__init__.py) discourages direct `ReadableSpan` construction; its resource documentation similarly restricts direct SDK `Resource` construction. The reworked production exporter constructs neither. Resource, scope, events, links and spans in `export.py` are generated **protobuf messages**, not rebuilt SDK objects. No private OTel fields, resets, encoder helpers, exporter hooks, or monkeypatches are used in production.

This uses the public `SpanExporter` extension, public readable-span getters, generated `opentelemetry-proto` messages/stub, and public grpcio channels. The installed protocol package uses upstream protobuf schema v1.10.0. Protocol mapping, generated-package compatibility and transport semantics require regression validation on dependency upgrades. Original SDK dropped-field counters are preserved; privacy removals are not misrepresented as SDK collection-limit drops.

Raw contrib data can exist for the normal SDK span/queue lifetime before encoding. This boundary protects the application-owned trace pipeline, not independent providers/exporters, business logs or UI persistence. Native OTel logs remain outside this delivery. Own transport failure logs are bounded.

Production metric export remains absent, as recorded in T1/T2A. T2B changes neither metric instruments nor operational summaries and does not claim to sanitize arbitrary externally installed metric exporters. T4 must enforce metric label/value rules before introducing production export. T2C owns combined migration guardrails, and T3 must verify native span/scope/operation names against these catalogs before replacing contrib instrumentation.

## Validation

| Check | Local result |
|---|---|
| Focused privacy, observability, lifecycle, and health suite | 112 passed |
| Full backend suite (`pytest -o addopts='' -q`) | 933 passed |
| `mypy app tests evaluation scripts` | Passed, 316 source files |
| `ruff check .` / `ruff format --check .` | Passed; 373 files already formatted |
| `git diff --check` | Passed |

Existing Starlette TestClient and SQLAlchemy datetime deprecation warnings remain. No dependency, lockfile, Docker/Compose, or native telemetry changes are part of this delivery. Existing unrelated screenshot diffs remain unchanged and nothing has been staged.

- Production builder and real batch processor use the custom exporter; tests capture and round-trip the final serialized gRPC request. A real local gRPC receiver confirms a two-span batch arrives in one RPC.
- Private sentinels are absent from serialized payloads across attributes, events, status, resource, scope, schema URLs, span/event names, links, and tracestate.
- Direct SDK writes, automatic third-party exception recording, and environment resource attributes cannot bypass the export filter.
- Current FastAPI/contrib tests cover parameterized and unmatched paths, URL-encoded paths/query strings, JSON-serialized bodies, JWT-shaped Authorization, captured Cookie/Set-Cookie, and chained endpoint exceptions. They retain only registered route templates.
- Explicit and implicit domain exception chains and explicit framework chains are scanned in final protobuf bytes; framework payloads are also rendered as JSON and scanned.
- Tests reject invalid UUIDs, unknown/overlong categories and sequences, non-finite ratios, and out-of-range numeric values; known domain catalogs and bounded outcomes remain intact.
- Actual agent runs retain valid UUID correlation and UI trace IDs while excluding raw identity and business content from OTLP.
- Existing domain span/metric tests now use the custom exporter with a protobuf transport capture. Lifecycle isolation continues to use injection or subprocesses without private OTel global resets.

Commit/push/merge and native telemetry migration are separate steps.

## Closure review after transport rework

**Verdict: T2B READY TO CLOSE.** This is a local closure assessment, not a commit, merge, T2C guardrail completion, native migration, or deployment delivery claim.

| Area | Review result |
|---|---|
| Scope | Only privacy enforcement, necessary domain cleanup/template registration, focused tests and delivery documentation. No FastAPI/dependency/lockfile/Docker/contrib-removal/metric-export changes |
| Export boundary | One custom exporter in the owned batch processor. Entire allowlisted protobuf request is built before any RPC; no raw fallback or alternate current owned export path |
| Privacy matrix | Identity, URL/address/header/content/tool/SQL/exception attributes denied; approved UUIDs, categories, sequences, routes and finite diagnostics remain bounded. No truncation-based redaction |
| Exception privacy | Automatic recording/descriptions disabled in domain helper; final boundary strips contrib/direct SDK message, stack/cause/context and status text. Explicit/implicit domain and explicit framework chains tested |
| Resource privacy | Original resources/schema URLs never copied; protobuf contains approved service slug and fixed installed SDK data only. Environment host/process and supplied username/path identities covered |
| Correlation | IDs, parent IDs, kind and timestamps preserved; approved scope names/versions retained. Runtime run/request UUIDs and UI trace ID remain intact. HTTP/domain trace ID agreement covered; combined incoming propagation/stage guardrails remain T2C |
| Test coverage | Final serialized protobuf and framework protobuf JSON scans; encoded/serialized values, JWT/cookies, multi-span batch, bounded failure and catalog coverage. Real local gRPC test uses two spans in one RPC |
| T2A regression | Barrier/Event contention test still executes the real builder and custom exporter constructor: exactly one provider factory, one exporter factory and one processor; all callers share the owned provider. Failure/disabled/shutdown tests pass without private OTel global resets |
| Performance | LOW: bounded SDK batch, fresh allowlisted protobuf, no recursive arbitrary object traversal, extra raw retention or second queue. SDK spans remain sensitive for their normal in-memory lifetime |
| Git | Nothing staged. Intended untracked privacy/transport tests included in the delivery set. Unrelated assets/scripts remain excluded; six pre-existing screenshot diffs match the saved baseline hash |
| Remaining limitations | No production Jaeger/TLS smoke claim; transport/protocol upgrade regression remains required. Production metric export remains absent. Private OTel transport plugins/settings and internal exporter metrics are not replicated |
| Closure blockers | None identified for accepted T2B scope after rework and validation |

### Intended T2B file set

Tracked modifications:

- `app/agent/nodes/memory_action.py`
- `app/agent/runtime.py`
- `app/main.py`
- `app/observability/attributes.py`
- `app/observability/tracing.py`
- `docs/fastapi-native-otel-delivery-plan.md`
- `tests/test_observability.py`
- `tests/test_observability_lifecycle.py`

Intended new files (currently untracked):

- `app/observability/export.py`
- `app/observability/privacy.py`
- `docs/fastapi-native-otel-t2b-export-privacy.md`
- `tests/otel_capture.py`
- `tests/test_observability_privacy.py`
- `tests/test_observability_transport.py`

The transport rework touched `export.py`, `tracing.py`, the three observability test files, the two new transport/capture test files, and the two delivery documents. The other T2B modifications predate this rework.

Unrelated modified `screenshots/operator-e2e/*.png` and untracked demo/security/approval/screenshot artifacts remain outside this set. The untracked scripts `capture_demo_final_release.sh`, `capture_demo_final_release_v2.sh`, and `capture_demo_showcase.sh` remain excluded. Neither those files nor screenshots were edited or staged by this rework.
