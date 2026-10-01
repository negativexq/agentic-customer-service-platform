"""Server log boundaries retain HTTP diagnostics without raw request data."""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import privacy
from app.observability.access_logging import (
    SafeAccessLogMiddleware,
    configure_safe_server_logging,
)


def test_safe_http_logging_uses_templates_and_never_raw_request_data(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(privacy, "_registered_routes", frozenset({"/test/{item}"}))
    app = FastAPI()
    app.add_middleware(SafeAccessLogMiddleware)

    @app.get("/test/{item}")
    def endpoint(item: str) -> dict[str, bool]:
        return {"ok": True}

    with caplog.at_level(logging.INFO):
        with TestClient(app) as client:
            assert (
                client.get(
                    "/test/PRIVATE_ID?secret=PRIVATE_QUERY",
                    headers={"Authorization": "Bearer PRIVATE_TOKEN", "Cookie": "PRIVATE_COOKIE"},
                ).status_code
                == 200
            )
            assert client.get("/PRIVATE_UNKNOWN?secret=PRIVATE_QUERY").status_code == 404
    records = [r.getMessage() for r in caplog.records if r.name == "uvicorn.error.safe_access"]
    assert len(records) == 2
    assert "route=/test/{item} status=200" in records[0]
    assert "route=unmatched status=404" in records[1]
    assert "PRIVATE" not in str(records)


def test_server_filters_are_idempotent_and_remove_raw_access_and_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_safe_server_logging()
    access, error = logging.getLogger("uvicorn.access"), logging.getLogger("uvicorn.error")
    counts = len(access.filters), len(error.filters)
    configure_safe_server_logging()
    assert counts == (len(access.filters), len(error.filters))
    with caplog.at_level(logging.INFO):
        access.info("PRIVATE_CLIENT GET /PRIVATE_PATH?secret=PRIVATE_QUERY 200")
        try:
            raise RuntimeError("PRIVATE_EXCEPTION") from ValueError("PRIVATE_CAUSE")
        except RuntimeError:
            error.exception("PRIVATE_MESSAGE")
    server_records = [r for r in caplog.records if r.name.startswith("uvicorn.")]
    assert len(server_records) == 1
    assert server_records[0].getMessage() == "HTTP server request failed."
    assert server_records[0].exc_info is None and server_records[0].exc_text is None
