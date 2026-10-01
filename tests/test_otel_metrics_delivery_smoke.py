"""Guard the disposable delivery runner's scope and privacy assertion helpers."""

import pytest

from scripts.e2e_authenticated_smoke import SmokeFailure
from scripts.otel_metrics_delivery_smoke import QUERY, MetricsStack, scan_private


def test_delivery_runner_requires_disposable_project_namespace() -> None:
    with pytest.raises(SmokeFailure, match="Disposable project prefix"):
        MetricsStack("customer-service")
    with pytest.raises(SmokeFailure, match="unsupported characters"):
        MetricsStack("t4d-metrics-invalid/name")


def test_delivery_runner_pins_opt_in_destination_and_uses_ephemeral_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSE_OTEL_METRICS_ENDPOINT", "http://external.invalid:4317")
    stack = MetricsStack("t4d-metrics-unit-control")
    assert stack.environment["OTEL_METRICS_ENABLED"] == "true"
    assert stack.environment["COMPOSE_OTEL_METRICS_ENDPOINT"] == "http://otel-collector:4317"
    assert stack.environment["PROMETHEUS_PORT"] == stack.environment["BACKEND_PORT"] == "0"
    assert stack.command[-2:] == ["--profile", "metrics"]


def test_delivery_privacy_scan_detects_substrings_and_encoded_content() -> None:
    from urllib.parse import quote

    scan_private('http_server_active_requests{http_request_method="GET"} 0')
    with pytest.raises(SmokeFailure, match="Private content"):
        scan_private("prefix" + QUERY + "suffix")
    with pytest.raises(SmokeFailure, match="Private content"):
        scan_private(
            "prefix" + quote("private spaced content", safe="") + "suffix", "private spaced content"
        )


def delivery_exposition() -> str:
    native_labels = (
        'http_route="/agent/chat",http_request_method="POST",http_response_status_code="200"'
    )
    lines = [
        "agent_runs_total 1",
        'tool_calls_total{tool_name="get_order",status="executed"} 1',
        'policy_decisions_total{policy_outcome="allow",risk_level="0"} 1',
        'agent_run_duration_seconds_count{status="ok"} 1',
        'agent_run_duration_seconds_sum{status="ok"} 0.25',
        'agent_run_duration_seconds_bucket{status="ok",le="0.1"} 0',
        'agent_run_duration_seconds_bucket{status="ok",le="+Inf"} 1',
    ]
    for suffix, labels, value in (
        ("_count", native_labels, "1"),
        ("_sum", native_labels, "0.3"),
        ("_bucket", native_labels + ',le="0.1"', "0"),
        ("_bucket", native_labels + ',le="+Inf"', "1"),
    ):
        lines.append("http_server_request_duration_seconds" + suffix + "{" + labels + "} " + value)
    return "\n".join(lines)


def test_delivery_snapshot_checks_real_names_deltas_and_histograms() -> None:
    from scripts.otel_metrics_delivery_smoke import exposition_samples, verify_delivery_snapshot

    samples = exposition_samples(delivery_exposition())
    verify_delivery_snapshot([], samples, native=True)
    with pytest.raises(SmokeFailure, match="delta"):
        verify_delivery_snapshot(samples, samples, native=True)


@pytest.mark.parametrize("mutation", ["empty", "count", "sum", "bucket", "missing_bucket"])
def test_delivery_snapshot_rejects_missing_or_invalid_evidence(mutation: str) -> None:
    from scripts.otel_metrics_delivery_smoke import exposition_samples, verify_delivery_snapshot

    text = delivery_exposition()
    if mutation == "empty":
        text = "# No samples\n"
    elif mutation == "count":
        text = text.replace(
            'agent_run_duration_seconds_count{status="ok"} 1',
            'agent_run_duration_seconds_count{status="ok"} 0',
        )
    elif mutation == "sum":
        text = text.replace(
            'agent_run_duration_seconds_sum{status="ok"} 0.25',
            'agent_run_duration_seconds_sum{status="ok"} NaN',
        )
    elif mutation == "bucket":
        text = text.replace(
            'agent_run_duration_seconds_bucket{status="ok",le="+Inf"} 1',
            'agent_run_duration_seconds_bucket{status="ok",le="+Inf"} 0',
        )
    else:
        text = "\n".join(
            line
            for line in text.splitlines()
            if not line.startswith("agent_run_duration_seconds_bucket")
        )
    with pytest.raises(SmokeFailure):
        verify_delivery_snapshot([], exposition_samples(text), native=True)
