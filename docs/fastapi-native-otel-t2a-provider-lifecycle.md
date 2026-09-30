# T2A — Provider lifecycle

This delivery implements the provider ownership contract from the [T1 design](fastapi-native-otel-t1-design.md). It retains the current FastAPI version, contrib instrumentation, OTLP gRPC exporter, and deployment topology.

## Ownership and startup

`ObservabilityLifecycle` in `app/observability/tracing.py` serializes initialization and shutdown. Repeated enabled initialization with the same service name and endpoint returns the same active provider. A changed configuration or an already registered external provider raises a bounded `ObservabilityConfigurationError`; external providers are neither adopted nor closed.

The application creates one SDK `TracerProvider`, one `BatchSpanProcessor`, and one `OTLPSpanExporter`. `get_tracer_provider()` exposes the owned provider for future framework integration. Domain helpers use this accessor rather than automatically adopting a foreign global provider. The owned SDK provider remains globally registered, preserving normal OTel parent context propagation.

Import-time bootstrap remains in `app/main.py`. Lifespan startup validates the same configuration before allocating checkpoint resources. Initialization failures close constructed components and return a fixed error code without raw exception details.

## Disabled mode and metrics

Application OTel metric instruments are no-op before successful initialization, when telemetry is disabled, after initialization or registration failure, and after owned-provider shutdown. Disabled configuration also supplies a no-op domain tracer, including when another library has registered global providers. Process-local operational summaries remain active throughout these lifecycle states; they are independent of OTel instruments and export. Turning off an already active owned pipeline is a configuration conflict.

Enabled metrics retain the existing global meter path. This delivery does **not** create a production SDK `MeterProvider`, metric reader, or metric exporter. The existing production metrics export gap remains assigned to T4. Test fixtures explicitly inject their own trace and metric providers and close them without resetting private OTel globals.

## Shutdown and restart

Shutdown detaches the owned provider, attempts force-flush with the existing 5,000 ms default, then attempts provider shutdown even if flushing fails or returns false. Repeated shutdown does nothing. Cleanup warnings contain fixed messages and error types, without exception text or stack traces.

Lifespan retains cleanup order: knowledge runtime, checkpoint provider, database engine, then telemetry. An application-owned `atexit` callback provides fallback cleanup; SDK automatic exit shutdown is disabled to avoid a second owner.

OTel global registration cannot be reset through its public API. After an enabled provider is shut down, enabled startup in the same process raises `telemetry_restart_requires_new_process`. Start a new process for enabled restart. Repeated disabled lifespans remain supported.

## Validation boundaries

Local validation completed: 42 focused observability/lifecycle/health tests passed; the full suite passed all 863 tests; `mypy app tests` passed for 242 source files. Existing Starlette TestClient and SQLAlchemy datetime deprecation warnings remain.

`tests/test_observability_lifecycle.py` covers concurrent and repeated initialization, conflicting configuration, external ownership, partial initialization and registration failures, disabled mode, flush/shutdown failures, terminal restart behavior, and lifespan startup validation. A subprocess test exercises real global SDK registration, parent/child context, batch export, and exactly one exporter shutdown.

The concurrency test uses a three-party barrier to start two bootstrap callers, holds the first construction inside the exporter factory with an event, and observes the other caller contending on the original lifecycle RLock before releasing construction. It exercises the real pipeline builder with separately counted provider, exporter, and batch-processor factories. Both concurrent callers receive the same owned provider; exactly one provider, one exporter, and one attached processor are created. The exporter factory supplies an in-memory exporter to avoid network access, and the real batch processor exports a span during shutdown. No private OTel global state is reset.

`tests/test_observability.py` retains domain span and in-memory metric assertions through isolated provider injection. Health tests protect existing endpoint semantics and cleanup order.

HTTP trace duplication, native framework operation spans, and combined HTTP privacy assertions belong to T2C/T3. Export privacy enforcement, including automatic exception events in domain spans, remains T2B work. Bounded lifecycle logging does not establish that the current exported span payloads meet the complete privacy contract.
