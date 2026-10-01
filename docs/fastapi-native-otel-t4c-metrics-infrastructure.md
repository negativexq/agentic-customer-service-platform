# T4C — Collector and Prometheus infrastructure

Branch: `infra/otel-metrics-collector-prometheus`. Base: merged T4B on main (`23c7327`).

This package prepares the metrics destination and Compose wiring. It does not enable application recording by default, enable FastAPI native metrics, modify the trace exporter, or prove application metric delivery. The authoritative phase split is in [the delivery plan](fastapi-native-otel-delivery-plan.md).

## Topology and enablement

```text
backend application/domain instruments (opt-in, T4B privacy boundary)
  → OTLP gRPC otel-collector:4317
  → Prometheus text exporter otel-collector:8889
  ← Prometheus scrape every 15 seconds

backend traces → existing Jaeger:4317
```

`docker-compose.yml` adds two services under the `metrics` profile. Starting the profile does not change `OTEL_METRICS_ENABLED=false`. The backend receives its separate metric endpoint through `COMPOSE_OTEL_METRICS_ENDPOINT`; the trace endpoint remains `http://jaeger:4317`. Existing backend health/readiness and dependencies are unchanged: Collector availability is not a backend startup or readiness requirement.

The backend retains its default network and also joins the internal `metrics` network. Collector joins only that internal network. Prometheus joins `metrics` for scraping and a separate `metrics-ui` bridge for its localhost host binding; it does not join the application's default network. Docker did not create the host binding when Prometheus used only an internal network, so the UI bridge is required for the proposed host access. Collector has no published host port; gRPC 4317, scrape 8889 and health 13133 are internal. Its OTLP HTTP receiver is not enabled. Jaeger's existing published 4317/4318 ports are unchanged and do not conflict with the internal Collector listener.

Prometheus UI/API is bound to `127.0.0.1:${PROMETHEUS_PORT:-9090}`. There is no public unauthenticated listener, remote-write receiver, admin API or lifecycle reload API configured. Remote operations should use an approved tunnel/access layer. Internal receiver/scrape traffic is plaintext; this configuration is not production TLS acceptance.

## Pinned releases

| Service | Version | Multi-platform manifest digest |
|---|---|---|
| `otel/opentelemetry-collector-contrib` | `0.161.0` | `sha256:fd328de2552466ad78385e1b1289c3f2402b1c45f265b252aab1955b42845ac1` |
| `prom/prometheus` | `v3.15.0` | `sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e` |

Both pinned manifests provide linux/amd64 and linux/arm64 images. Collector 0.162.0 had an upstream release, but its expected Docker Hub and GHCR container tags were unavailable during selection; 0.161.0 is the verified available image. Configuration is validated with the selected binaries, not inferred from moving documentation.

Relevant official sources: [Collector 0.161.0 release](https://github.com/open-telemetry/opentelemetry-collector-releases/releases/tag/v0.161.0), [Prometheus 3.15.0 release](https://github.com/prometheus/prometheus/releases/tag/v3.15.0), [tagged Prometheus exporter configuration](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.161.0/exporter/prometheusexporter/config.go), [tagged batch processor](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.161.0/processor/batchprocessor/README.md).

## Resource, privacy and durability boundaries

Collector's only pipeline is metrics, with `memory_limiter → transform/metric_resource → batch → prometheus`. No trace/log pipeline, raw debug exporter, resource detector, tenant metadata batching or persistent queue is configured.

- Collector: 0.5 CPU / 256 MB container limit, 192 MiB memory limiter, 48 MiB spike allowance, 1-second check; `GOMEMLIMIT=192MiB`.
- gRPC: 4 MiB receive limit and 16 concurrent streams per transport.
- Batch: 512-point trigger, 1024-point maximum, 1-second timeout. The maximum splits output batches; it is not a total memory guarantee. Memory limiter/container limits remain separate protection.
- The resource-context transform uses `delete_matching_keys(attributes, ".*")` with `error_mode: propagate` to remove every resource attribute before batching/export. This also removes approved `service.name` and SDK resource metadata on the Collector-to-Prometheus leg; the application's sanitized OTLP resource on the backend-to-Collector leg remains unchanged. Prometheus supplies the fixed scrape job/instance labels for the supported single-producer topology. Metric values and approved datapoint attributes are retained.
- Prometheus exporter: sending queue disabled, five-minute metric expiration, classic text format without exemplars, no source timestamps and no instrumentation scope labels. `resource_constant_labels.excluded: ["*"]` alone does not suppress resource metadata in `target_info`; the transform prevents that metadata from reaching the exporter. The scrape-time metadata-family drop remains defense in depth.
- Translation strategy is explicitly `UnderscoreEscapingWithSuffixes`. Actual translated domain/HTTP family names and queries must be established in T4D.
- Prometheus drops `target_info`/`otel_scope_info`, limits a scrape to 10,000 samples, 20 labels, 100-character label names, 200-character values and an 8 MB body. Scrape limits are safety ceilings, not redaction.
- Collector self-metric recording is disabled; no self-scrape job is added. Its logs use error level; no payload debug logging is enabled.

T4B's recording guard and final sanitized OTLP projection remain the application privacy boundary. Collector resource removal is an additional infrastructure boundary; it does not sanitize arbitrary datapoint labels or authorize arbitrary SDK producers. Only the owned backend producer is permitted to submit metrics; do not attach other services to this network or expose the receiver publicly without a separate privacy/access design. The transform uses the pinned release's public [resource-context configuration](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.161.0/processor/transformprocessor/README.md) and [delete_matching_keys function](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.161.0/pkg/ottl/ottlfuncs/func_delete_matching_keys.go).

Prometheus has a writable named volume with both **7-day** and **1 GB** retention flags; its API normalizes these to `1w` and `1GiB`. These are initial bounded defaults, not an approved production sizing/access policy. Retention size governs retained TSDB blocks; it is not a filesystem quota or a hard bound on WAL/head/disk use. Production capacity and access/TLS acceptance remain open.

Collector buffers and its exported snapshot are in-memory only. Restart/absence can lose measurements; no retry queue or persistence durability is claimed. Prometheus retention does not make Collector delivery durable. Failure/recovery and application measurement visibility belong to T4D.

## Single producer and hardening

The supported initial topology is **one backend replica and one Uvicorn worker**. Compose sets backend replicas to one and `WEB_CONCURRENCY=1`; the command adds no extra workers. Collector and Prometheus also default to one replica. CLI scaling or command overrides can defeat these settings and are unsupported for metric acceptance. Multiple producers can collide on the same cumulative series after approved resource projection; scaling requires a separate reviewed producer/aggregation design.

Both new services use explicit non-root users, read-only root filesystems, all capabilities dropped, no-new-privileges, init, bounded CPU/memory, restart policy and a 15-second stop grace period. Only the Prometheus data volume is writable. Production Compose inherits these settings. This is configuration evidence, not T5 proof of total shutdown time.

Prometheus has a Docker healthcheck against `/-/ready`. The Collector's scratch image has no shell/wget/curl; no misleading Docker healthcheck using `validate` is added. Its internal health extension can be probed from Prometheus. It proves process readiness, not end-to-end metric delivery. Prometheus depends on Collector `service_started`; backend depends on neither new service.

## Operator commands

Validate configuration without starting the application:

```sh
docker compose --profile metrics config --quiet
docker compose --profile metrics run --rm --no-deps otel-collector \
  validate --config=/etc/otelcol-contrib/metrics.yaml
docker compose --profile metrics run --rm --no-deps --entrypoint /bin/promtool \
  prometheus check config /etc/prometheus/prometheus.yml
```

Start only the infrastructure; application recording stays disabled:

```sh
docker compose --profile metrics up -d --no-deps otel-collector prometheus
docker compose --profile metrics exec -T prometheus \
  wget -qO- http://otel-collector:13133/
docker compose --profile metrics exec -T prometheus \
  wget -qO- http://127.0.0.1:9090/-/ready
```

When T4D explicitly validates application recording, its opt-in is `OTEL_METRICS_ENABLED=true` with the infrastructure profile present. Both the existing master `OTEL_ENABLED` and metrics switch are required. Compose configuration alone does not activate native FastAPI metrics, which remain `metrics=False` in application code.

Stop only these services without deleting stored metrics:

```sh
docker compose --profile metrics stop prometheus otel-collector
```

Avoid `down --volumes` against an existing application stack: it also removes database/Qdrant storage. Only the isolated disposable validation project may be cleaned with volume removal.

## Validation evidence

Local checks completed on 2026-10-01 (Docker Desktop linux/arm64):

| Check | Result |
|---|---|
| Base Compose, profile off/on | Configuration valid |
| Production overlay + metrics profile | Configuration valid; existing production topology validator passed |
| Integration overlay + metrics profile | Configuration valid |
| Rendered base/production topology inspection | Metrics default false; trace destination unchanged; separate metric destination; single backend worker/replica; no backend dependency on metrics services; private receiver network and localhost UI binding; non-root/read-only/capability/resource settings present |
| Pinned Collector `validate --config=/etc/otelcol-contrib/metrics.yaml` | Passed |
| Pinned Prometheus `promtool check config /etc/prometheus/prometheus.yml` | Passed |
| Isolated `t4c-metrics-validation` project | Only Collector/Prometheus started; Prometheus Docker healthcheck passed, Collector internal health responded, host-local Prometheus readiness responded |
| Infrastructure scrape target | Exactly one target, `http://otel-collector:8889/metrics`, reported `up` with no error |
| Runtime Prometheus flags | Retention `1w`/`1GiB`; admin/lifecycle APIs false |
| Trivy 0.70.0, `--ignore-unfixed --severity HIGH,CRITICAL` | Zero matching fixable findings in selected local Collector/Prometheus images (arm64) |
| Actionlint 1.7.7 | Passed |
| `git diff --check` | Clean |

Closure follow-up on the same date validated the resource transform with the exact pinned Collector image and reran pinned promtool. An isolated `t4c-resource-validation` project received two synthetic OTLP gRPC batches, each containing a Sum and Histogram plus resource sentinels for host/user/environment/service/instance/command-line identity. Both RPC responses reported zero rejected datapoints and no partial-success error. The raw Collector `/metrics` output contained neither the sentinels nor `target_info`/`otel_scope_info`; both Sum values and Histogram count/sum/bucket values retained their safe `outcome=success` datapoint label. No resource-derived job/instance labels appeared in that raw output. Collector logs were empty, both readiness endpoints responded, and the single Prometheus scrape target remained `up`. This is a synthetic infrastructure resource-boundary probe, not an application/domain delivery test. The temporary sender and isolated project's containers, networks and volume were removed afterward.

The isolated validation used an ephemeral localhost port instead of 9090 to avoid affecting existing services. Its containers, networks and disposable Prometheus volume were removed afterward. No backend business request, domain OTLP submission or domain PromQL query was run. A healthy empty scrape target is infrastructure connectivity evidence only, not application delivery.

The closure review ran 255 focused observability tests successfully; no full backend suite rerun is claimed for this configuration-only package. The synthetic resource probe is local validation evidence, not a committed regression test or CI delivery check. CI now validates the profile and both pinned configurations, scans both new images for fixable HIGH/CRITICAL findings and validates the production profile. The retained full CI pipeline will run when a PR is opened; no remote CI pass is claimed yet. Local image evidence covers arm64; the amd64 CI image check remains pending.

## Remaining work

- **T4D:** actual application → Collector → Prometheus delivery/query validation, approved translated names/labels, privacy scans of emitted/stored families, and failure/recovery. Native provider binding/enablement and HTTP metric guardrails are explicit application-side prerequisites before native metric delivery can be claimed.
- **T5:** real deployment/TLS/access, SIGTERM/shutdown budgets, production capacity/delivery and release evidence.

No dependency/lockfile, application metrics/privacy/export implementation, native telemetry setting, trace transport, business logic or screenshot change is part of T4C.
