import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import iter_route_contexts

from app.agent.runtime import AgentRuntime
from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import engine
from app.observability.middleware import fastapi_telemetry
from app.observability.privacy import register_route_templates
from app.observability.tracing import configure_observability, shutdown_observability
from app.persistence.checkpoint import build_checkpoint_provider

settings = get_settings()
logger = logging.getLogger(__name__)
tracer_provider = configure_observability(settings)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    configure_observability(settings)
    checkpoint_provider = build_checkpoint_provider(settings)
    runtime: AgentRuntime | None = None
    application.state.accepting_requests = False
    try:
        checkpoint_provider.initialize()
        application.state.checkpoint_provider = checkpoint_provider
        runtime = AgentRuntime(
            checkpointer=checkpoint_provider.checkpointer,
            checkpoint_backend=checkpoint_provider.backend,
        )
        application.state.agent_runtime = runtime
        application.state.accepting_requests = True
        logger.info(
            "Application startup complete.",
            extra={
                "lifecycle_status": "ready",
                "checkpoint_backend": checkpoint_provider.backend.value,
            },
        )
        yield
    finally:
        application.state.accepting_requests = False
        logger.info("Application shutdown started.", extra={"lifecycle_status": "draining"})
        if runtime is not None:
            _safe_close("knowledge", runtime.close)
        _safe_close("checkpoint", checkpoint_provider.close)
        _safe_close("database", engine.dispose)
        _safe_close("telemetry", shutdown_observability)
        logger.info("Application shutdown complete.", extra={"lifecycle_status": "stopped"})


def _safe_close(component: str, close: Callable[[], None]) -> None:
    try:
        close()
    except Exception as error:
        logger.warning(
            "Application component cleanup failed.",
            extra={
                "lifecycle_component": component,
                "lifecycle_error_type": type(error).__name__,
            },
        )


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
    telemetry=fastapi_telemetry(settings, tracer_provider),
)


@app.exception_handler(RequestValidationError)
async def bounded_validation_error(request: Request, exc: RequestValidationError) -> Response:
    """Return safe, actionable validation feedback for oversized chat input."""

    too_long = request.url.path == "/agent/chat" and any(
        list(error.get("loc", ()))[-1:] == ["message"]
        and error.get("type") in {"string_too_long", "value_error.any_str.max_length"}
        for error in exc.errors()
    )
    if too_long:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "reason": "input_too_long",
                    "message": "Your message is too long. Please shorten it.",
                    "trace_event": "rejected_before_agent",
                }
            },
        )
    return await request_validation_exception_handler(request, exc)


app.include_router(api_router)
register_route_templates(
    route.path_format for route in iter_route_contexts(app.routes) if route.path_format is not None
)
