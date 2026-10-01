# T5 — Runtime and release evidence

Status: **implementation and local validation complete; closure review and remote T5 CI pending**.

Base: merged T4D at `f8da2b3`. Branch: `test/otel-runtime-release-evidence`.

## Acceptance scope

The project owner confirmed that this is a repository/portfolio project with no deployment environment. Acceptance consists of reproducible local runtime evidence, real local gRPC TLS/auth tests, CI, reference configuration validation, and release/rollback documentation. Actual staging/production deployment, ingress, external OIDC credentials, deployed trust roots and production capacity are outside this release scope. Privacy, provider ownership, metric cardinality and the single-producer restriction remain unchanged.

T4D PR #10 passed all eight CI jobs, including amd64 application/domain/native metrics delivery and Collector recovery. That run is not evidence that the new T5 jobs have passed. Earlier T4D startup failures were not proven to be a metrics/provider defect.

No production application code, dependencies, lockfile, Compose source, infrastructure pins or trace routing changed in T5.

## Real process acceptance

`scripts/otel_runtime_smoke.py` uses disposable `t4d-metrics-t5-*` Compose projects and the existing integration fixtures. It runs a real backend/Uvicorn process, PostgreSQL, Qdrant, Jaeger, Collector and Prometheus. The deterministic decision fixture replaces external LLM generation; authenticated HTTP requests still execute the production runtime/policy/tool/projection path. The rendered model must contain one backend worker and replica, with traces still routed directly to `jaeger:4317`.

Normal span batching is deferred for 60 seconds and periodic metric export for 300 seconds. Completed-request scenarios verify no domain/native chat metric delivery before stopping the backend; the healthy trace scenario also verifies no matching request trace before stopping. Final delivery therefore tests shutdown flushing rather than merely finding an earlier export.

| Scenario | Required evidence |
|---|---|
| `graceful` | Business request, projection, health/readiness and process-local summary work; SIGTERM completes lifespan; previously undelivered domain/native measurements reach Prometheus and the matching trace reaches Jaeger |
| `jaeger-outage` | Actual Jaeger service stopped; business/health/readiness/summary remain functional; bounded trace export failure; final metrics still reach Prometheus |
| `disabled` | Master flag false while metric opt-in and OTLP endpoints remain present; business/projection/summary work; projection trace ID absent; no application trace or metric export and no export attempt warning |
| `draining` | PostgreSQL transaction holds an exclusive `orders` table lock; a real HTTP business request is observed waiting on that lock; SIGTERM enters Uvicorn connection draining while the request is pending; releasing the lock permits HTTP 200 and persisted projection correlation before process exit; final metrics and trace arrive |
| `both-outage` | Actual Jaeger and Collector services stopped; business/health/readiness/summary work; both signal export failures are observed; SIGTERM and lifespan finish within the existing grace period; successful delivery is not claimed |

The drain synchronization uses a database lock marker, `pg_stat_activity`, and Uvicorn's connection-draining marker. It does not depend on a random sleep, a synthetic span, or a production handler change. Projection correlation after signal is read from the persisted database rather than requiring a new connection to a draining HTTP listener.

The both-outage scenario adds an ephemeral read-only logging configuration that includes logger names, permitting separate trace/metric failure checks. It does not enable native OpenTelemetry logs or change production logging configuration.

Locked Uvicorn 0.52.1 re-raises captured SIGTERM after graceful cleanup (`Server.capture_signals`). Exit `0` or `143` is accepted only with completed lifespan and scenario-specific delivery/failure evidence; forced-kill exit `137` is rejected. Shutdown must finish within the existing 35-second Compose grace period. This is measured local scenario evidence, not a universal shutdown guarantee.

Shutdown is sequential: application component cleanup, trace force-flush and provider shutdown, then metric force-flush and provider shutdown. Each phase can add to total process-exit latency; the timeout arguments are not one shared deadline. In the locked OpenTelemetry SDK 1.44.0, the batch processor's `force_flush()` does not enforce its timeout argument. The application's default `5000 ms` trace flush argument therefore is not a hard wall-clock guarantee. Export RPC deadlines and finite batches bound the current transport work, while the runner separately rejects shutdown taking 35 seconds or more. The reported shutdown duration measures total stop/exit time, not independently measured drain or telemetry-flush phases. This limitation requires no production change in this evidence package.

### Local results — 2026-10-01

| Scenario | Request duration | Shutdown duration | Exit | Final metrics | Final trace |
|---|---:|---:|---:|---|---|
| graceful | 0.156 s | 0.613 s | 143 | Delivered | Delivered |
| jaeger-outage | 0.258 s | 7.755 s | 143 | Delivered | Unavailable; bounded failure |
| disabled | 0.191 s | 0.634 s | 143 | No application export | No application export |
| draining | 1.674 s | 1.873 s | 143 | Delivered | Delivered |
| both-outage | 0.240 s | 7.878 s | 143 | Unavailable; bounded failure | Unavailable; bounded failure |

For healthy delivery, final `agent_runs_total` is `1` and `http_server_request_duration_seconds_count{http_route="/agent/chat"}` is `1`. Jaeger contains exactly one SERVER span, `agent.run` and `tool.execute`, all with the projection's trace ID. More detailed native operation, ancestor-chain, incoming propagation, auth/422 and exception privacy contracts remain protected by the existing T3/T4D suites. Those invalid-request cases were not repeated as new T5 Docker scenarios.

Disabled-mode queries exclude Prometheus's own `up`/`scrape_*` metadata when checking absence of application measurements. Scrape metadata is not application export. Logs, retrieved trace graphs, Collector exposition and Prometheus application query representations are scanned using the existing private sentinels; `target_info` and `otel_scope_info` must be absent from successful metric delivery.

Reproduce each scenario:

```sh
python3 -m scripts.otel_runtime_smoke --project t4d-metrics-t5-graceful --scenario graceful
python3 -m scripts.otel_runtime_smoke --project t4d-metrics-t5-jaeger-outage --scenario jaeger-outage
python3 -m scripts.otel_runtime_smoke --project t4d-metrics-t5-disabled --scenario disabled
python3 -m scripts.otel_runtime_smoke --project t4d-metrics-t5-draining --scenario draining
python3 -m scripts.otel_runtime_smoke --project t4d-metrics-t5-both-outage --scenario both-outage
```

All five final runs completed cleanup. The runner removes only its disposable project's containers/volumes and temporary fixtures. Cleanup failure returns failure and suppresses a successful acceptance result. Nine helper test cases cover namespace/configuration, exit-state parsing, narrow overrides and failure reporting; they do not substitute for process evidence.

## Real local OTLP TLS/auth evidence

`tests/test_observability_tls.py` contains 16 cases: both trace and metric exporters exercise eight scenarios against real local gRPC receivers:

- trusted CA and matching hostname;
- wrong CA;
- wrong hostname;
- missing Authorization;
- wrong Authorization;
- valid mutual TLS client certificate;
- missing required client certificate;
- unavailable receiver.

Certificates are ephemeral, generated with the already installed `cryptography` dependency. On-disk CA/client certificate/key material lives in an explicitly managed temporary directory removed on success, setup failure and assertion failure; three cleanup regression cases verify removal. Receiver teardown also covers setup failures. Receivers implement actual OTLP protobuf services and inspect actual authorization metadata. Tests use the production privacy exporters and secure gRPC channels, not channel/exporter mocks or private SDK global resets. Positive cases inspect nonempty final serialized protobuf for safe controls and absence of private fields. Negative cases reject delivery, keep failure logging bounded, and finish within a three-second test bound with a one-second configured export timeout. Flush, repeated shutdown and rejection of post-shutdown export are checked.

These tests establish local transport behavior, not deployment TLS or external trust-policy acceptance. The Docker integration network remains plaintext/private-network OTLP.

## Configuration and release documentation

All four rendered Compose combinations passed: local/production-reference overlay, with/without the `metrics` profile. Checks preserve default-disabled metrics, direct Jaeger tracing, private Collector ports, localhost-only Prometheus, pinned images and a single producer. `scripts/validate_production_topology.py` passed as a reference-policy check, not an actual deployment.

Collector validation used the exact pinned Collector image/config; promtool used the exact pinned Prometheus image/config. Both passed. No production image or configuration was upgraded. Locked versions remain FastAPI 0.142.2, OTel 1.44.0, Starlette 1.6.0, Pydantic 2.13.4 and Uvicorn 0.52.1.

[Observability architecture and rollback](observability.md) records provider ownership, the enablement matrix, separate signal destinations, privacy/cardinality constraints and rollback through a new process. README and deployment documentation link or describe the current architecture and shutdown behavior. Configuration rollback preserves business state; disposable smoke cleanup is not a production rollback procedure.

## Persistent CI and validation

The new `Telemetry Runtime Acceptance` job follows `metrics-delivery` and runs all five scenarios in independent disposable projects on Ubuntu 24.04. It has bounded job/command deadlines and always-cleanup, without broad permissions or `continue-on-error`. TLS tests run as part of the existing backend suite. **Remote T5 CI is pending until this branch is reviewed and pushed.**

Fresh local validation after closure fixes (16 transport cases plus 3 cleanup regression cases):

| Check | Result |
|---|---|
| Combined observability/health/runtime focused suite | 308 passed |
| Full backend suite | 1128 passed, 1 existing skip |
| mypy | 334 source files clean |
| Ruff check | Passed |
| Ruff format check | 397 files already formatted |
| actionlint 1.7.7 | Passed |
| `uv lock --check` | Passed; 113 packages; lockfile unchanged |
| Four Compose models and reference topology policy | Passed |
| Exact pinned Collector validate and promtool | Passed |
| Five real runtime scenarios | Passed; timings above |
| `git diff --check` | Clean |

The existing skip is `tests/test_frontend_auth_contract.py:28`: a production frontend bundle was not built in this worktree. No auth/security assertion was relaxed. Existing trace/privacy/metrics guardrails remain intact.

## Closure boundaries

Local implementation/evidence is ready for a separate closure review. T5 is not yet closed or merged: closure review and successful remote T5 CI remain required. No commit, push or merge was performed as part of this implementation.

The following are explicit limitations, not completed deployment claims:

- no actual deployed ingress, external OIDC or production TLS acceptance;
- no universal shutdown-time SLO or durable replay guarantee for outage measurements;
- no multi-worker/multi-replica cumulative metric support;
- no production capacity, backup or public-access certification;
- no new Docker invalid-request scenario beyond composed T3/T4D regression evidence.

A future deployment needs its own secret management, access/TLS policy, capacity and rollout validation. It is outside the portfolio release accepted by the project owner.
