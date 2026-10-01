"""Bounded HTTP access records without Uvicorn's raw URL/client fields."""

from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.privacy import HTTP_METHODS, registered_route_templates

logger = logging.getLogger("uvicorn.error.safe_access")


class _DiscardRawAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


class _BoundedServerException(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info or record.stack_info:
            record.msg = "HTTP server request failed."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def configure_safe_server_logging() -> None:
    """Install idempotent filters on the server's public logging interface."""
    for name, filter_type in (
        ("uvicorn.access", _DiscardRawAccess),
        ("uvicorn.error", _BoundedServerException),
    ):
        server_logger = logging.getLogger(name)
        if not any(isinstance(item, filter_type) for item in server_logger.filters):
            server_logger.addFilter(filter_type())


class SafeAccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 500

        async def observe(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                candidate = message.get("status")
                if isinstance(candidate, int) and 100 <= candidate <= 599:
                    status = candidate
            await send(message)

        try:
            await self.app(scope, receive, observe)
        finally:
            route = getattr(scope.get("route"), "path_format", None)
            route = (
                route
                if isinstance(route, str) and route in registered_route_templates()
                else "unmatched"
            )
            method = scope.get("method")
            method = method if method in HTTP_METHODS else "_OTHER"
            logger.info(
                "HTTP request completed method=%s route=%s status=%d duration_seconds=%.6f",
                method,
                route,
                status,
                max(0, time.monotonic() - started),
            )
