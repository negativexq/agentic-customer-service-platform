"""Isolated current-pipeline HTTP integration harness; production remains unchanged."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import grpc
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from google.protobuf.json_format import MessageToJson
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from otel_capture import WireExporter
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send

from app import main
from app.agent import runtime as runtime_module
from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentResponse, StructuredDecision
from app.api.router import api_router
from app.api.routes import health, ui
from app.auth.backends import StaticBearerAuthenticator
from app.auth.dependencies import get_authenticator
from app.auth.models import ActorType, Principal
from app.core.database import get_db
from app.observability import metrics as application_metrics
from app.observability import privacy, tracing
from app.observability.export import PrivacyOTLPSpanExporter
from app.observability.middleware import fastapi_telemetry
from app.persistence.checkpoint import MemoryCheckpointProvider
from app.resilience.config import ResilienceConfig
from app.ui.repository import InMemoryAgentRunProjectionRepository

TOKEN = "PRIVATE_T2C_BEARER_CREDENTIAL"
ACTOR = "PRIVATE_T2C_ACTOR"


@dataclass
class HTTPPipeline:
    app: FastAPI
    client: TestClient
    capture: WireExporter
    owner: tracing.ObservabilityLifecycle
    projections: InMemoryAgentRunProjectionRepository
    metric_reader: InMemoryMetricReader
    events: list[str]
    factories: dict[str, int]
    runtime: AgentRuntime | None = None
    contexts: list[Any] = field(default_factory=list)
    worker_threads: list[int] = field(default_factory=list)
    loop_threads: list[int] = field(default_factory=list)
    dependency_threads: list[int] = field(default_factory=list)
    processors: list[object] = field(default_factory=list)
    response_sent: threading.Event = field(default_factory=threading.Event)

    def flush(self) -> None:
        provider = self.owner.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            assert provider.force_flush(5000)

    def assert_private_absent(self, *values: str) -> None:
        self.flush()
        for request, payload in zip(self.capture.requests, self.capture.payloads, strict=True):
            rendered = MessageToJson(request)
            for value in (TOKEN, ACTOR, *values):
                assert value.encode() not in payload
                assert value not in rendered


class HTTPHarness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
        self.monkeypatch = monkeypatch
        self.session = session
        self.attempts: list[tuple[WireExporter, list[str], dict[str, int]]] = []

    @contextmanager
    def start(
        self,
        decisions: Sequence[StructuredDecision],
        *,
        enabled: bool = True,
        provider: Any = None,
        external: trace.TracerProvider | None = None,
        fail_registration: bool = False,
        configure_app: Callable[[FastAPI], None] | None = None,
        metrics_endpoint: str | None = None,
        metrics_timeout_millis: int = 5000,
    ) -> Iterator[HTTPPipeline]:
        with self.monkeypatch.context() as patch:
            patch.setenv("OTEL_TRACES_SAMPLER", "parentbased_always_on")
            patch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)
            capture = WireExporter()
            real_insecure_channel = grpc.insecure_channel
            capture.install(patch)
            if metrics_endpoint is not None:
                # Keep the existing trace wire seam; metrics use a real gRPC
                # channel to their independently configured destination.
                patch.setattr(
                    grpc,
                    "insecure_channel",
                    lambda target, **kwargs: (
                        capture
                        if target == "localhost:4317"
                        else real_insecure_channel(target, **kwargs)
                    ),
                )
            events: list[str] = []
            processors: list[object] = []
            factories = {"provider": 0, "exporter": 0}
            self.attempts.append((capture, events, factories))
            owner = tracing.ObservabilityLifecycle()
            registry = {"provider": external or trace.ProxyTracerProvider()}
            reader = InMemoryMetricReader()
            meter = (
                MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
                if metrics_endpoint is None
                else None
            )
            projections = InMemoryAgentRunProjectionRepository()
            patch.setattr(tracing, "_lifecycle", owner)
            patch.setattr(trace, "get_tracer_provider", lambda: registry["provider"])

            def register(value: trace.TracerProvider) -> None:
                if not fail_registration:
                    registry["provider"] = value

            patch.setattr(trace, "set_tracer_provider", register)
            if meter is not None:
                patch.setattr(metrics, "get_meter_provider", lambda: meter)
            patch.setattr(application_metrics, "_metrics", application_metrics.get_metrics())
            settings = main.settings.model_copy(
                update={
                    "otel_enabled": enabled,
                    "otel_metrics_enabled": metrics_endpoint is not None,
                    "otel_exporter_otlp_metrics_endpoint": metrics_endpoint or "",
                    "otel_metric_export_interval_millis": 300000,
                    "otel_metric_export_timeout_millis": metrics_timeout_millis,
                    "otel_service_name": "t2c-test",
                    "otel_exporter_otlp_endpoint": "http://localhost:4317",
                }
            )
            patch.setattr(main, "settings", settings)
            patch.setattr(runtime_module, "get_settings", lambda: settings)
            patch.setattr(health, "get_settings", lambda: settings)
            patch.setattr(ui, "get_settings", lambda: settings)
            original_provider = TracerProvider
            original_exporter = PrivacyOTLPSpanExporter

            def build_provider(**kwargs: Any) -> TracerProvider:
                factories["provider"] += 1
                result = original_provider(**kwargs)
                original_flush, original_shutdown = result.force_flush, result.shutdown
                original_attach = result.add_span_processor

                def attach(processor: Any) -> None:
                    processors.append(processor)
                    original_attach(processor)

                patch.setattr(result, "add_span_processor", attach)

                def flush(timeout_millis: int = 30000) -> bool:
                    events.append("telemetry_flush")
                    return original_flush(timeout_millis)

                def shutdown() -> None:
                    events.append("telemetry_close")
                    original_shutdown()

                patch.setattr(result, "force_flush", flush)
                patch.setattr(result, "shutdown", shutdown)
                return result

            def build_exporter(**kwargs: Any) -> Any:
                factories["exporter"] += 1
                return original_exporter(**kwargs)

            patch.setattr(tracing, "TracerProvider", build_provider)
            patch.setattr(tracing, "PrivacyOTLPSpanExporter", build_exporter)
            checkpoint = MemoryCheckpointProvider()
            original_checkpoint_close = checkpoint.close

            def close_checkpoint() -> None:
                events.append("checkpoint_close")
                original_checkpoint_close()

            patch.setattr(checkpoint, "close", close_checkpoint)
            patch.setattr(main, "build_checkpoint_provider", lambda settings: checkpoint)
            patch.setattr(
                main, "engine", SimpleNamespace(dispose=lambda: events.append("database_close"))
            )
            application: FastAPI | None = None
            try:
                application = FastAPI(
                    lifespan=main.lifespan,
                    telemetry=fastapi_telemetry(
                        settings, tracing.configure_observability(settings)
                    ),
                )
                if enabled and metrics_endpoint is None:
                    # Explicit test-reader injection; production no longer adopts
                    # the global metric provider when its metric pipeline is off.
                    assert meter is not None
                    application_metrics.configure_metrics(meter)

                class LoopProbe:
                    def __init__(self, app: ASGIApp) -> None:
                        self.app = app

                    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                        if scope["type"] == "http":
                            pipeline.loop_threads.append(threading.get_ident())

                        async def observe_send(message: Any) -> None:
                            await send(message)
                            if message["type"] == "http.response.body" and not message.get(
                                "more_body"
                            ):
                                pipeline.response_sent.set()

                        await self.app(scope, receive, observe_send)

                application.add_middleware(LoopProbe)
                application.include_router(api_router)
                if configure_app is not None:
                    configure_app(application)
                patch.setattr(privacy, "_registered_routes", privacy.registered_route_templates())
                privacy.register_route_templates(
                    route.path_format
                    for route in iter_route_contexts(application.routes)
                    if route.path_format is not None
                )
                application.add_exception_handler(
                    RequestValidationError,
                    main.bounded_validation_error,  # type: ignore[arg-type]
                )
                principal = Principal(
                    actor_id=ACTOR,
                    actor_type=ActorType.SUPPORT_OPERATOR,
                    roles=["support_operator"],
                )
                authenticator = StaticBearerAuthenticator(
                    {
                        TOKEN: principal,
                        "customer-token": Principal(
                            actor_id="PRIVATE_CUSTOMER_ACTOR",
                            actor_type=ActorType.CUSTOMER,
                            roles=["customer"],
                            customer_id=1,
                        ),
                        "forbidden-token": Principal(
                            actor_id="PRIVATE_FORBIDDEN_ACTOR",
                            actor_type=ActorType.SUPPORT_OPERATOR,
                            roles=[],
                        ),
                    }
                )

                def database() -> Iterator[Session]:
                    pipeline.dependency_threads.append(threading.get_ident())
                    yield self.session

                application.dependency_overrides[get_db] = database
                application.dependency_overrides[get_authenticator] = lambda: authenticator
                patch.setattr(
                    ui, "build_agent_run_projection_repository", lambda *args: projections
                )
                pipeline: HTTPPipeline

                def build_runtime(**kwargs: Any) -> AgentRuntime:
                    runtime = AgentRuntime(
                        provider=provider or FakeDecisionProvider(decisions),
                        projection_repository=projections,
                        resilience_config=ResilienceConfig(initial_backoff_ms=0, max_backoff_ms=0),
                        **kwargs,
                    )
                    pipeline.runtime = runtime
                    original_run, original_close = runtime.run, runtime.close

                    def run(**run_kwargs: Any) -> AgentResponse:
                        pipeline.contexts.append(run_kwargs["context"])
                        pipeline.worker_threads.append(threading.get_ident())
                        return original_run(**run_kwargs)

                    def close() -> None:
                        events.append("runtime_close")
                        original_close()

                    patch.setattr(runtime, "run", run)
                    patch.setattr(runtime, "close", close)
                    return runtime

                patch.setattr(main, "AgentRuntime", build_runtime)
                client = TestClient(
                    application,
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    raise_server_exceptions=False,
                )
                pipeline = HTTPPipeline(
                    application,
                    client,
                    capture,
                    owner,
                    projections,
                    reader,
                    events,
                    factories,
                    processors=processors,
                )
                with client:
                    yield pipeline
            finally:
                owner.shutdown()
                if meter is not None:
                    meter.shutdown()
                if application is not None:
                    application.dependency_overrides.clear()
