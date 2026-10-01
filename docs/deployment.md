# Production Container Expectations

The repository provides hardened backend and frontend images plus a production-oriented Compose
overlay. It does not provision infrastructure, manage secrets, publish images, or deploy services.

## Images

The backend uses a multi-stage build. uv resolves the production environment from `uv.lock` in the
builder, and only the virtual environment plus operational migration files are copied into the
runtime image. The runtime does not contain uv or frontend/build dependencies and runs as the
non-login `app` user (UID/GID 10001).

The frontend is built with `npm ci` and copied into an unprivileged nginx runtime on port 8080.
Hashed assets under `/assets/` are cached for one year as immutable. HTML uses revalidation, API
responses are not cached, unknown application paths fall back to `index.html`, and security headers
are applied to static and proxied responses.

Backend configuration is supplied at runtime. The local frontend build receives only the public,
non-secret demo token so the static console can exercise authenticated routes. Integration receives
an explicitly test-only credential. The production overlay builds with
`FRONTEND_AUTH_MODE=external_session` and no credential, so its bundle is credential-free. No real
credentials or environment files are copied into either image.

## Health contract

| Endpoint | Meaning | Dependencies | Response body |
| --- | --- | --- | --- |
| `GET /health` | Process liveness | None | `{"status":"ok"}` |
| `GET /ready` | Traffic readiness | PostgreSQL, checkpoint backend, configured RAG backend | `{"status":"ready"}` or `{"status":"not_ready"}` |
| `GET /ui/system-health` | Authenticated component health snapshot | Same runtime checks as `/ready`, plus safe configuration state | `{"status":"ready|not_ready", "components":[...]}` |

Readiness failures return HTTP 503 without naming the failed dependency. Liveness deliberately
stays healthy during dependency outages so an orchestrator does not restart an otherwise healthy
process. The backend image healthcheck uses liveness; Compose uses readiness for service ordering.

`/ready` and authenticated `/ui/system-health` use one request-scoped runtime health model. The
health view reports PostgreSQL, checkpoint, and retrieval checks as `healthy`, `unavailable`, or
`incompatible`; it also reports LLM configuration as `not_probed` when no active provider probe has
run. Configuration is not availability, so a configured LLM is never displayed as healthy without
an actual health probe. `/health` remains lightweight process liveness and does not query dependencies.

For `RAG_BACKEND=qdrant`, `/ready` is fail-closed until the configured alias exists and its active
physical snapshot exists with
metadata matches the runtime contract: one unnamed dense vector, a named `lexical` sparse vector,
`Distance.COSINE`, and `EMBEDDING_DIMENSION`. The collection must also contain valid deterministic
lexical metadata and at least one indexed knowledge point; this is the selected
ingestion-completeness policy for the normal demo and production RAG path.
Readiness only observes Qdrant metadata and never creates, recreates, upserts, or deletes a
collection. It also checks persisted embedding provider/model identity and supported snapshot,
chunking, and lexical-index versions. `RAG_BACKEND=local` bypasses Qdrant readiness entirely.
For providers without a stable model identity, matching dimensions remain a structural check only
and cannot prove semantic compatibility. Production Qdrant retrieval fuses the
dense and lexical branches using reciprocal-rank fusion before optional reranking. Existing
dense-only collections must be re-ingested into a compatible hybrid collection; startup does not
delete or upgrade them automatically. Provision a new collection (or explicitly replace the old
one under an operator-controlled migration), set `QDRANT_COLLECTION`, and run
`python -m scripts.rag_ingest` before switching traffic.

## Graceful shutdown

The backend receives `SIGTERM` directly. Uvicorn stops accepting connections and is allowed up to
30 seconds to finish active requests. FastAPI lifespan then marks the process unready and closes:

1. the agent runtime and managed Qdrant client;
2. the LangGraph checkpoint provider and its connection pool;
3. the SQLAlchemy engine pool;
4. the owned OpenTelemetry tracer provider after a bounded flush, then the optional owned meter provider after its independent flush/shutdown attempts.

The locked Uvicorn version can re-raise SIGTERM after graceful completion, producing exit 143. Exit code alone is not delivery evidence: the [runtime acceptance](fastapi-native-otel-t5-runtime-release-evidence.md) also checks lifespan completion and final delivery. The request-draining period and subsequent component cleanup consume time; the 35-second Compose ceiling is not a universal flush guarantee.

Compose allows 35 seconds before forcefully stopping the backend. Lifecycle logs contain only
bounded component names and statuses; they do not contain prompts, customer data, or credentials.

## Timeout and retry coordination

Network dependencies use their native bounded request deadlines: the OpenAI-compatible model and
embedding clients receive explicit connect/read/write/pool timeouts with SDK retries disabled, and
Qdrant receives the effective retrieval-attempt timeout on each request. The retry coordinator
budgets the complete sequence and schedules an attempt only after the previous native call has
returned; it does not run synchronous I/O in a detached daemon thread. Local CPU rerankers are not
wrapped in a non-cancellable wall-clock thread, so a reranker failure or provider-native timeout
degrades to the original fused ranking. PostgreSQL uses pool acquisition, connection, and
statement timeouts, with transaction cleanup handled by the existing session boundary.

Write timeouts retain the unknown-outcome safety model. If a commit response cannot be confirmed,
the operation raises `UnknownWriteOutcomeError`, is not automatically replayed, and can only be
reconciled with the same idempotency key. These deadlines bound dependency work; they do not claim
to cancel arbitrary synchronous application code that has no native cancellation mechanism.

## Checkpoint deserialization boundary

PostgreSQL checkpoint data is integrity-sensitive. The application configures LangGraph's msgpack
serializer with a small allowlist of exact `(module_name, class_name)` application symbols and no
pickle fallback. Unknown Python types fail checkpoint loading rather than being imported or
reconstructed. Local Compose defaults `LANGGRAPH_STRICT_MSGPACK=true`; the integration and
production overlays set it unconditionally so LangGraph also derives allowed types from the graph
schema during compilation.

Checkpoints written before strict mode remain readable when they contain the same legitimate
application types because strict mode changes reconstruction authorization, not the msgpack wire
format. There is deliberately no permissive retry or automatic migration fallback. Deployments
with independently modified historical state should validate it before rollout. This boundary
reduces risk from tampered checkpoint rows; it does not protect a system whose application runtime
or database credentials are already fully compromised.

## Local development stack

The base file keeps an explicitly development-only authenticated demo. It migrates the database,
resets and loads demo records, ingests bundled knowledge into Qdrant, and starts the backend only
after setup succeeds:

```bash
cp .env.example .env
docker compose up --build --detach
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8000/ui/system-health)" = 401
curl --fail -H 'Authorization: Bearer local-demo-support-token' http://127.0.0.1:8000/ui/system-health
curl --fail http://127.0.0.1:5173/
docker compose down
```

`AUTH_MODE=local_demo` maps the configured non-secret demo token to a typed support-operator
principal. It does not make protected routes anonymous. The frontend holds that token in memory
and sends it through nginx, which explicitly preserves `Authorization` for `/agent` and `/ui`.

Frontend authentication has three deployment modes: `local_demo`, `integration`, and
`external_session`. Production selects `external_session`; it does not silently use a demo or
static backend token. Without a trusted external identity/session layer, the console displays
“Production authentication is not configured” and does not call protected APIs. A trusted OIDC
client, OAuth2 Authorization Code + PKCE flow, BFF, auth gateway, or reverse-proxy identity
integration can provide the `window.__OPERATOR_AUTH__` adapter. The adapter may establish an
HTTP-only cookie session or provide an externally acquired access credential. The backend validates
OIDC JWTs; the repository does not implement browser login or persist browser credentials.

A real `/agent/chat` result additionally requires a reachable OpenAI-compatible LLM. The Compose
default uses `http://host.docker.internal:11434/v1`; change `COMPOSE_LLM_BASE_URL`, `LLM_MODEL`, and
`LLM_API_KEY` as needed. Do not report the agent path as validated if that runtime is absent.

For an optional local provider smoke with Ollama, install the development baseline `qwen2.5:7b`
and configure the host process with `LLM_PROVIDER=openai_compatible`,
`LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=qwen2.5:7b`, and `LLM_API_KEY=ollama`.
Compose on macOS reaches the host through `COMPOSE_LLM_BASE_URL=http://host.docker.internal:11434/v1`.
The optional `LLM_REASONING_EFFORT` setting accepts `none`, `low`, `medium`, or `high`. If it is
unset, the OpenAI-compatible provider sends no reasoning override and preserves provider defaults.
This is a non-deterministic development check only; deterministic integration mode remains the CI
path and Ollama is not a production dependency.

For a hermetic integration proof instead of a live-model demo, run
`python3 scripts/e2e_authenticated_smoke.py`. It uses a separate Compose project and fresh volumes,
selects the explicit integration-only deterministic decision provider, and tears all isolated
resources down afterward. It validates the authenticated Risk-2 proposal, PostgreSQL-backed resume
after a backend restart, one idempotent business mutation, replay safety, and safe operator
projections. It does not validate real-model semantic quality.

## Production-oriented Compose overlay

The overlay adds restart policies, read-only application filesystems, dropped Linux capabilities,
`no-new-privileges`, temporary writable filesystems, and configurable resource limits. Supply
credentials externally; do not put them in a committed `.env` file.

```bash
export POSTGRES_PASSWORD='set-outside-source-control'
export DATABASE_URL='postgresql+psycopg://app:encoded-password@db:5432/customer_service'
export OIDC_ISSUER='https://identity.example.com'
export OIDC_AUDIENCE='agent-control-plane'
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build --detach
```

CPU and memory defaults can be overridden with `BACKEND_CPU_LIMIT`, `BACKEND_MEMORY_LIMIT`,
`FRONTEND_CPU_LIMIT`, `FRONTEND_MEMORY_LIMIT`, `POSTGRES_CPU_LIMIT`, `POSTGRES_MEMORY_LIMIT`,
`QDRANT_CPU_LIMIT`, `QDRANT_MEMORY_LIMIT`, `JAEGER_CPU_LIMIT`, and `JAEGER_MEMORY_LIMIT`.

The production overlay sets `APP_ENV=production`, forces `AUTH_MODE=oidc`, requires an HTTPS issuer
and explicit audience, removes the demo token from backend/setup containers, and builds the frontend
with `FRONTEND_AUTH_MODE=external_session` and no bundled browser credential. The backend discovers
JWKS from the issuer and validates signature, rotation, issuer, audience, subject, and expiration.
The overlay also forces `LANGGRAPH_STRICT_MSGPACK=true`.

The production Operator Console service is present by default but is intentionally fail-closed
until a trusted external identity/session layer supplies the `window.__OPERATOR_AUTH__` adapter.
Without that integration, it displays “Production authentication is not configured” and does not
call protected APIs. Static bearer authentication remains a non-production compatibility/test
adapter. The repository implements backend OIDC token validation but not Authorization Code + PKCE,
a BFF, identity-provider provisioning, or enterprise login UX.
It forces `POLICY_AUDIT_BACKEND=postgres`; production policy audit is a durable, bounded operational
evidence trail. Audit rows contain structured policy lifecycle metadata only and are never used as
an authorization or business-state source. Configure database retention/pruning operationally;
the application does not claim immutable compliance-ledger guarantees. Every agent-originated
business write has policy and execution evidence: Risk 1 records allow plus execution outcome,
Risk 2 records confirmation/revalidation plus execution outcome, and Risk 3 records the actual
escalation persistence outcome. Execution outcomes distinguish success, failure, and unknown.
Audit is written before a protected write is attempted, but audit persistence and the business
mutation are not one atomic distributed transaction; idempotency receipts and business invariants
remain authoritative for post-commit reconciliation. Raw prompts, tool payloads, and business free
text are excluded from audit rows.

Runtime diagnostics use safe source classification: `LLM_ERROR` identifies model/provider
interaction, `TOOL_ERROR` a controlled business-tool failure, `DEPENDENCY_ERROR` an external
service or infrastructure failure, and `INTERNAL_ERROR` an unexpected platform/runtime failure.
Timeout, validation, policy, retrieval, and unknown-write semantics remain distinct where
applicable. Categories do not imply retryability; native dependency retry rules, idempotency, and
unknown-write reconciliation remain authoritative. Raw exception text is not returned as an
operator error payload or persisted in the run projection/audit record.

Operator Console agent-run projections use the `agent_run_projections` PostgreSQL table in
integration and production. They are a bounded, durable read model for inspection: a backend restart
or a second backend instance can read the same safe projection. Each projection row represents one
graph invocation; a pending confirmation receives a new run/request/trace identity while retaining
the same conversation and opaque action identity. Projection data is never consulted for authentication, authorization,
confirmation validity, business state, checkpoint restoration, or idempotency. The default list limit
is 50 and the hard maximum is 100. Projection retention/pruning is operator- or database-managed;
this table is not an immutable audit ledger. Development/test-only memory storage is bounded and
rejected by configuration in integration and production.

The checkpoint `thread_id` remains the actor/customer/conversation-scoped workflow key. It is not
reused as `run_id`: every HTTP request invokes a fresh graph run. `action_id` is the stable lifecycle
correlation for a pending action, so a confirmation or lost-response retry can be related to the
original proposal without mutating its historical invocation projection. A new trace is likewise
created per request; this repository correlates related traces through safe conversation/action
attributes rather than claiming cross-request trace continuation.

Production knowledge ingestion is a complete-snapshot operation. `QDRANT_COLLECTION` is the stable
logical alias; each build creates a new physical `*_v_<snapshot-spec-hash-prefix>` collection.
`corpus_hash` is the SHA-256 of canonical complete-corpus data. `snapshot_id`/`snapshot_spec_hash`
is the SHA-256 of that corpus hash plus the embedding provider/model/dimension and the semantic
dense/sparse, knowledge-schema, chunking, and lexical-index versions. This lets identical corpus
contents coexist safely under different embedding/index specifications. The full spec hash is
stored in provenance and the physical name uses only a 16-character prefix. Each build computes
lexical vocabulary/IDF from the full corpus, persists embedding and schema provenance, validates
point count/schema/spec identity, and atomically switches the alias. The active snapshot is never
incrementally mutated or deleted by readiness/startup. Removed source documents disappear when the
new snapshot is activated. Operators may inspect snapshots with `python -m scripts.rag_ingest list`
and roll back with `python -m scripts.rag_ingest rollback <physical-collection>`; previous
collections remain available until explicitly retired. A failed build or alias operation leaves
the previous alias target authoritative. Legacy corpus-only collections without snapshot-spec
provenance are rejected rather than silently reinterpreted. Rollback validates the target against
the current runtime embedding provider/model and index specification; an embedding-model rollback
therefore requires coordinated runtime configuration and alias changes. This repository provides
artifact validation and atomic alias switching, not a deployment control plane for coordinating that
rollout.

Snapshot provenance also records `build_state` and `expected_chunk_count`. New builds remain
`building` until the full dense/sparse collection, lexical metadata, exact point count, schema, and
snapshot provenance validate; only then are they marked `complete` and eligible for alias activation
or rollback. A normal retry after a failed build marks the exact inactive managed artifact failed,
deletes only that artifact, and rebuilds the complete corpus from scratch. Complete compatible
artifacts are reused without re-ingestion.

Active snapshots are immutable: an invalid or incomplete active target causes readiness/activation
to fail and is never automatically deleted or repaired. A collection with missing, foreign, or
full-hash-mismatched provenance is a collision and is never deleted automatically. Concurrent
independent builders for the same deterministic snapshot are not a supported operating mode; a
visible `building` artifact is rejected so a competing builder cannot destroy it. Operators or CI
should serialize snapshot builds; an interrupted process may require explicit operator cleanup or
retry after confirming the artifact is no longer being built.
Backend startup rejects disabled or local-demo
authentication in production. Static opaque bearers are only the repository's current integration
adapter, not a claim of production IAM; deployers must provide secret rotation/storage and their
environment-specific identity integration.

For a real deployment, replace the local PostgreSQL and Qdrant services with managed or separately
operated dependencies as appropriate, supply identity and observability configuration through the
deployment environment, and terminate TLS at a trusted ingress or load balancer.

## Production deployment topology

The Compose overlay is a reference service topology. In a managed deployment, public traffic
should terminate at an external ingress or load balancer and reach multiple stateless API
replicas:

```text
Frontend / trusted operator session
              |
              v
Ingress / load balancer
              |
              v
      API replicas (stateless)
          |       |       \
          v       v        v
     PostgreSQL  Qdrant  Evidence store
          |
          v
   Checkpoints, audit, idempotency, projections

Supporting plane: metrics, logs, traces, secrets, backups
```

PostgreSQL is the shared authority for business state, idempotency receipts, policy audit
records, checkpoints, and durable operator projections. Qdrant is a replaceable knowledge
snapshot service; it is not an execution authority. Evidence payloads belong in an immutable,
access-controlled object store, while manifests and hashes remain independently verifiable.
The ingress, TLS termination, identity/session gateway, secret manager, databases, and object
storage are intentionally outside this repository's Compose stack.

The API process is designed to be horizontally replicated when all replicas use the same
PostgreSQL/checkpoint store and tenant-aware configuration. Local in-memory checkpoint, audit,
projection, or rate-limit modes are development/test modes and are not a multi-instance
deployment strategy. The application does not coordinate leader election, database failover,
object-store replication, or ingress cutover.

## Production hardening checks

The production overlay applies read-only filesystems where supported, temporary writable paths,
dropped capabilities, `no-new-privileges`, restart policies, resource limits, health checks, and
graceful shutdown windows. Application images run as non-root users. PostgreSQL's vendor
entrypoint starts with the privileges required to initialize its volume and drops to its database
user for the server process. The pinned upstream Qdrant image currently declares a root runtime;
production must use a managed Qdrant service or an approved hardened derivative that runs as a
non-root UID before treating the topology as fully hardened. The Compose overlay does not silently
claim this vendor image is non-root.

Run the static topology policy check before deployment:

```bash
make production-topology-validate
```

The check renders the production Compose model with validation-only placeholders, verifies
service hardening and resource policies, confirms OIDC/external-session configuration, and checks
database, Qdrant, and Jaeger healthcheck definitions. With an already-running environment, add
`--check-health` to validate `/health` and `/ready` without printing dependency details. For the
development filesystem evidence adapter, add `--evidence-root artifacts/evidence-payloads` to
check that the configured root is readable and writable. An S3-compatible deployment should use
its provider's authenticated, non-mutating bucket/prefix check instead.

## Database, evidence, and secrets operations

Run migrations as a release step before routing traffic. Use connection pooling and bounded
connection/statement timeouts; keep business writes and idempotency receipts in the existing
transaction boundaries. PostgreSQL backups, restore verification, Qdrant snapshot recovery, and
evidence hash validation are described in [disaster recovery](disaster-recovery.md).

Production secrets must come from an external manager such as AWS Secrets Manager, Vault, or a
Kubernetes Secret projected into the workload. Do not bake credentials into images, pass them as
committed Compose literals, or persist them in logs. In this repository, bounded traces go directly to Jaeger over OTLP gRPC; opt-in domain/native metrics go through the metrics-only Collector to Prometheus. These services are not business-state stores. See [observability architecture](observability.md) for signal ownership and portfolio/deployment boundaries. Prompts, tokens, customer data, and raw provider payloads remain excluded
from telemetry and evidence projections.

## Optional live-model behavioral evaluation

Live-model evaluation is an explicit development operation and is not part of normal CI. The local
baseline uses the existing OpenAI-compatible boundary with Ollama:

```bash
ollama list
python -m evaluation.live --model qwen2.5:7b-instruct \
  --base-url http://localhost:11434/v1 --runs-per-case 3 --layer both
```

For a Compose backend reaching Ollama on macOS, use
`http://host.docker.internal:11434/v1` as the base URL. The case set is versioned (`live_eval_v1`),
and reports are written to the ignored `artifacts/live-eval/` directory. The decision layer only
measures typed model proposals; the smaller control-plane layer verifies deterministic policy,
confirmation, idempotency, and mutation safety. A model failure never silently falls back to the
deterministic provider.

The runner records safe metrics and does not persist API keys, authorization headers, hidden
prompts, or raw model output by default. `qwen2.5:7b-instruct` is a local development baseline,
not a production recommendation. Live results are non-deterministic and do not turn the global LLM
health state into `healthy`; without an active probe it remains configured/not-probed.
