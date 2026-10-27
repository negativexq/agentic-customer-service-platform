# Capacity Report

This is a reproducible measurement record for the production-oriented
reference topology. It is not a production certification or a universal
throughput claim.

## Benchmark environment

The deterministic benchmark runs locally with:

- the repository's Python 3.12 environment;
- deterministic embeddings, reranking, policy evaluation, and private
  in-memory workload state;
- no OpenAI, Ollama, external network, or provider credentials;
- configurable iterations and worker count.

Run it with:

```bash
make capacity-benchmark
make capacity-db-benchmark
make capacity-load
```

`capacity-db-benchmark` uses a temporary SQLite database by default. Set
`CAPACITY_DATABASE_URL` to a dedicated PostgreSQL benchmark database to measure
PostgreSQL. The database benchmark creates and removes only its private
`capacity_benchmark_effects` table; do not point it at an application database
without an approved maintenance window.

## Real PostgreSQL application evidence

The required PostgreSQL workload is separate from the SQLite/INSERT primitive
above. Run against a dedicated test database with schema creation permission:

```bash
CAPACITY_DATABASE_URL=postgresql+psycopg://user:password@localhost/test_db \
  uv run --frozen python -m scripts.benchmark_postgres_application \
  --operations 1000 --workers 8 \
  --output artifacts/benchmarks/postgres-application.json
```

`make capacity-postgres-benchmark` runs the same tool with default workload
settings. A missing/non-PostgreSQL URL is an error; there is no SQLite fallback.
The tool creates a UUID-named schema, seeds synthetic delivered orders, runs the
real `request_refund` / `execute_idempotent` / `commit_business_write` transaction
path, and removes its schema on success or failure. It never reuses application
tables or the older primitive benchmark's fixed table name.

Three scenarios measure same-key replay, different keys competing for one order,
and independent orders. They verify actual refund and receipt counts, replay
result identity, expected duplicate rejection, and unexpected errors. Any failed
invariant returns a nonzero exit. Failure reports omit driver exception payloads
and connection URLs.

The version-1 JSON report records PostgreSQL version/isolation, operations,
workers/pool limits, Python/OS/architecture/CPU metadata, status/error counts,
p50/p95/p99/mean transaction latency, elapsed time, attempts/sec and effects/sec.
Transaction latency includes connection checkout, lock wait, and commit, but not
time queued before a worker starts. Throughput includes scheduling and pool
startup for each scenario. Bootstrap/seeding are excluded from workload timing.

CI explicitly supplies `CAPACITY_DATABASE_URL` using its PostgreSQL service,
runs 1000 independent operations with 8 workers, and retains sanitized JSON as
`postgres-benchmark-<run>-<attempt>` for 14 days, including invariant failures.
The artifact's workflow run identifies the tested commit. These measurements
exercise application transactions over a local database connection; they do not
include HTTP, LLM, Qdrant, or deployment-network latency, and are not production
SLO or managed-database sizing evidence.

## Workloads

The benchmark covers:

1. Read-only customer inquiry.
2. RAG grounded response and grounding validation.
3. Confirmation-required pending operation.
4. Confirmed write receipt path.
5. Duplicate execution replay.
6. Fail-closed policy rejection.

The persistence workload additionally checks same-key concurrency, independent
keys, and tenant-labelled isolation inputs. It records p50, p95, p99, mean,
throughput, and bounded status counts.

## Current deterministic result

Run output is intentionally generated at validation time because latency is
machine-dependent. The acceptance invariants are:

- provider calls: `0`;
- same idempotency key: one business effect and one receipt;
- different keys: independent effects;
- policy rejection: denied;
- grounded response: citation-constrained and accepted by the existing
  validator.

Do not copy local latency numbers into a production SLO without recording the
hardware, database topology, workload mix, and versioned benchmark output.

## Measurement-only runtime metrics

The application exposes bounded duration histograms for:

- agent run;
- decision compilation;
- policy evaluation;
- confirmation validation;
- checkpoint persistence setup/write path;
- idempotency lookup;
- RAG retrieval;
- grounding validation.

Only durations, counts, statuses, and bounded categories are recorded. User
messages, prompts, customer data, tool arguments, tokens, and provider
responses are excluded.

## Bottlenecks and scaling recommendations

The likely scale-sensitive boundaries are shared PostgreSQL connections and
transactions, Qdrant retrieval capacity, checkpoint storage, evidence-store
latency, and telemetry export. The reference topology supports API
horizontal-scaling validation, but production sizing requires workload-specific
measurements.

- API horizontal scaling: supported when shared state and tenant context are
  configured correctly.
- PostgreSQL: tune pool size, transaction duration, indexes, and backup load.
- Qdrant: size indexes, replicas, and shard strategy for the corpus and query
  mix.
- Evidence storage: use immutable object storage with hash verification and
  retention controls.

## Limitations

The local benchmark does not represent managed cloud pricing, network latency,
provider latency, failure distributions, multi-region behavior, or a formal
SLO. Multi-replica tests remain environment-dependent and must be run against
the deployment topology intended for release.
