"""Isolated real application -> Collector -> Prometheus delivery acceptance.

Uses only disposable project volumes and integration fixture decisions. Never point
this runner at an existing Compose project or a production deployment.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from scripts.e2e_authenticated_smoke import (
    EXPECTED_ACTOR_ID,
    ComposeStack,
    SmokeFailure,
    expect,
    request_json,
    wait_for_ready,
)

CONVERSATION = "PRIVATE_T4D_CONVERSATION"
TOKEN = "PRIVATE_T4D_VALIDATION_CREDENTIAL"
QUERY = "PRIVATE_T4D_QUERY"
MESSAGE = "Check my order 3 status"


class MetricsStack(ComposeStack):
    def __init__(self, project: str) -> None:
        # A dedicated prefix prevents accidental cleanup of an application stack.
        expect(project.startswith("t4d-metrics-"), "Disposable project prefix required.")
        super().__init__(project, TOKEN)
        self.environment.update(
            OTEL_METRICS_ENABLED="true",
            COMPOSE_OTEL_METRICS_ENDPOINT="http://otel-collector:4317",
            OTEL_METRIC_EXPORT_INTERVAL_MILLIS="1000",
            OTEL_METRIC_EXPORT_TIMEOUT_MILLIS="500",
            PROMETHEUS_PORT="0",
        )

    @property
    def command(self) -> list[str]:
        return [*super().command, "--profile", "metrics"]

    def start(self) -> None:
        self.clean()
        self.run(
            (
                "up",
                "--build",
                "--detach",
                "--wait",
                "--wait-timeout",
                "240",
                "backend",
                "otel-collector",
                "prometheus",
            ),
            timeout=600,
        )

    def url(self, service: str, port: int) -> str:
        address = self.run(("port", service, str(port)), timeout=30).stdout.strip()
        expect(bool(re.fullmatch(r"[^\n]+:\d+", address)), "Invalid isolated host port.")
        return f"http://127.0.0.1:{address.rsplit(':', 1)[1]}"

    def exporter_text(self) -> str:
        return self.run(
            ("exec", "-T", "prometheus", "wget", "-qO-", "http://otel-collector:8889/metrics"),
            timeout=15,
        ).stdout

    def query(self, expression: str) -> list[dict[str, Any]]:
        path = "/api/v1/query?" + urllib.parse.urlencode({"query": expression})
        _, result = request_json(self.url("prometheus", 9090), path)
        expect(result.get("status") == "success", "Prometheus query failed.")
        values: list[dict[str, Any]] = result["data"]["result"]
        return values


def eventually(check: Callable[[], bool], label: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.5)
    raise SmokeFailure(f"Timed out waiting for {label}.")


def metric_value(stack: MetricsStack, expression: str) -> float:
    values = stack.query(expression)
    return sum(float(value["value"][1]) for value in values)


def exposition_samples(text: str) -> list[dict[str, Any]]:
    """Read the pinned exporter's classic text samples, never guessed metric names."""
    samples: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+\d+)?", line)
        expect(match is not None, "Invalid Collector sample.")
        assert match is not None
        labels = {"__name__": match[1]}
        remaining = match[2] or ""
        while remaining:
            label = re.match(r'([a-zA-Z_][a-zA-Z0-9_]*)=("(?:[^"\\]|\\.)*")(?:,|$)', remaining)
            expect(label is not None, "Invalid Collector sample labels.")
            assert label is not None
            labels[label[1]] = json.loads(label[2])
            remaining = remaining[label.end() :]
        samples.append({"metric": labels, "value": [0, match[3]]})
    expect(bool(samples), "Collector sample capture is empty.")
    return samples


def sample_value(samples: list[dict[str, Any]], name: str, labels: dict[str, str]) -> float:
    return sum(
        float(sample["value"][1])
        for sample in samples
        if sample["metric"]["__name__"] == name
        and all(sample["metric"].get(key) == value for key, value in labels.items())
    )


def verify_delivery_snapshot(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    native: bool,
) -> None:
    """Assert request deltas and classic histogram invariants at either boundary."""
    expect(bool(after), "Delivery snapshot is empty.")
    for name, labels in (
        ("agent_runs_total", {}),
        ("tool_calls_total", {"tool_name": "get_order", "status": "executed"}),
        ("policy_decisions_total", {"policy_outcome": "allow", "risk_level": "0"}),
    ):
        expect(
            sample_value(after, name, labels) - sample_value(before, name, labels) == 1,
            "Counter request delta is incorrect.",
        )
    histograms = [("agent_run_duration_seconds", {"status": "ok"})]
    if native:
        histograms.append(
            (
                "http_server_request_duration_seconds",
                {
                    "http_route": "/agent/chat",
                    "http_request_method": "POST",
                    "http_response_status_code": "200",
                },
            )
        )
    for family, labels in histograms:
        count = sample_value(after, family + "_count", labels)
        expect(
            count - sample_value(before, family + "_count", labels) == 1,
            "Histogram request count delta is incorrect.",
        )
        total = sample_value(after, family + "_sum", labels)
        previous = sample_value(before, family + "_sum", labels)
        expect(
            math.isfinite(total) and math.isfinite(previous) and total > previous,
            "Histogram duration sum did not increase safely.",
        )
        buckets: dict[float, float] = {}
        for sample in after:
            attributes = sample["metric"]
            if attributes["__name__"] == family + "_bucket" and all(
                attributes.get(key) == value for key, value in labels.items()
            ):
                boundary = float(attributes["le"])
                value = float(sample["value"][1])
                expect(
                    not math.isnan(boundary) and math.isfinite(value), "Invalid histogram bucket."
                )
                buckets[boundary] = buckets.get(boundary, 0) + value
        expect(bool(buckets) and math.inf in buckets, "Histogram buckets absent.")
        ordered = [value for _, value in sorted(buckets.items())]
        expect(all(0 <= value <= count for value in ordered), "Histogram bucket out of range.")
        expect(
            ordered == sorted(ordered) and buckets[math.inf] == count,
            "Histogram buckets/count disagree.",
        )


def scan_private(text: str, *extra: str) -> None:
    for value in (TOKEN, EXPECTED_ACTOR_ID, CONVERSATION, QUERY, MESSAGE, *extra):
        for variant in (value, urllib.parse.quote(value, safe=""), json.dumps(value)[1:-1]):
            expect(variant not in text, "Private content appeared in validation capture.")


def business_request(stack: MetricsStack, base: str, conversation: str) -> str:
    code, response = request_json(
        base,
        f"/agent/chat?secret={QUERY}",
        token=TOKEN,
        payload={"conversation_id": conversation, "customer_id": 2, "message": MESSAGE},
    )
    expect(code == 200 and response.get("error_category") is None, "Business request failed.")
    expect(response.get("tool_call") == {"status": "executed"}, "Read tool did not execute.")
    run_id = str(response["agent_run_id"])
    code, view = request_json(base, f"/ui/agent-runs/{run_id}", token=TOKEN)
    expect(code == 200 and bool(view.get("trace_id")), "Projection trace correlation absent.")
    return str(view["trace_id"])


def trace_delivered(stack: MetricsStack, trace_id: str) -> bool:
    # Query Jaeger through its existing application network; no route is changed.
    program = (
        "import json,urllib.request; "
        f"url='http://jaeger:16686/api/traces/{trace_id}'; "
        "data=json.load(urllib.request.urlopen(url,timeout=3)); "
        "spans=data.get('data',[{}])[0].get('spans',[]); "
        "print(json.dumps([s['operationName'] for s in spans]))"
    )
    result = stack.run(("exec", "-T", "backend", "python", "-c", program), check=False)
    if result.returncode:
        return False
    names = json.loads(result.stdout)
    return "agent.run" in names and "tool.execute" in names


def run_delivery(stack: MetricsStack, *, native: bool = True) -> None:
    stack.start()
    base = stack.url("backend", 8000)
    wait_for_ready(base)
    model = json.loads(stack.run(("config", "--format", "json")).stdout)
    backend = model["services"]["backend"]
    expect(backend["environment"]["WEB_CONCURRENCY"] == "1", "One worker required.")
    expect(backend["deploy"]["replicas"] == 1, "One replica required.")
    containers = stack.run(("ps", "--quiet", "backend")).stdout.strip().splitlines()
    expect(len(containers) == 1, "Exactly one backend container required.")
    program = """import pathlib
count = 0
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:
        args = path.read_bytes().split(b'\\0')[:2]
    except OSError:
        continue
    if args and not args[0].endswith(b'docker-init'):
        count += any(arg.rsplit(b'/', 1)[-1] == b'uvicorn' for arg in args)
print(count)
"""
    count = stack.run(("exec", "-T", "backend", "python", "-c", program)).stdout.strip()
    expect(count == "1", "Exactly one running Uvicorn process required.")
    expect(
        backend["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://jaeger:4317",
        "Trace destination changed.",
    )
    for family in ("agent_runs.*", "tool_calls.*", "policy_decisions.*"):
        expect(not stack.query(f'{{__name__=~"{family}"}}'), "Domain baseline is not empty.")
    raw_before = exposition_samples(stack.exporter_text())
    stored_before = stack.query('{job="application-domain-metrics"}')
    trace_id = business_request(stack, base, CONVERSATION)
    eventually(lambda: bool(stack.query('{__name__=~"agent_runs.*"}')), "domain delivery")
    families = stack.query(
        '{__name__=~"agent_runs.*|tool_calls.*|policy_decisions.*|agent_run_duration.*"}'
    )
    names = sorted({sample["metric"]["__name__"] for sample in families})
    expect(
        set(names)
        == {
            "agent_runs_total",
            "tool_calls_total",
            "policy_decisions_total",
            "agent_run_duration_seconds_count",
            "agent_run_duration_seconds_sum",
            "agent_run_duration_seconds_bucket",
        },
        "Translated domain families differ from the accepted catalog.",
    )
    agent_counter = "agent_runs_total"
    expect(metric_value(stack, agent_counter) == 1, "First agent counter value is incorrect.")
    for expression in (
        'tool_calls_total{tool_name="get_order",status="executed"}',
        'policy_decisions_total{policy_outcome="allow",risk_level="0"}',
        'agent_run_duration_seconds_count{status="ok"}',
    ):
        expect(metric_value(stack, expression) == 1, "Domain value or labels are incorrect.")
    raw = stack.exporter_text()
    scan_private(raw)
    expect("target_info" not in raw and "otel_scope_info" not in raw, "Metadata family leaked.")
    native_families = stack.query('{__name__=~"http_server_.*"}')
    if native:
        eventually(
            lambda: bool(
                stack.query('http_server_request_duration_seconds_count{http_route="/agent/chat"}')
            ),
            "native HTTP delivery",
        )
        expect(
            metric_value(
                stack,
                'http_server_request_duration_seconds_count{http_route="/agent/chat",http_request_method="POST",http_response_status_code="200"}',
            )
            == 1,
            "Native request duration count is incorrect.",
        )
        eventually(
            lambda: (
                metric_value(stack, "http_server_active_requests") == 0
                and bool(stack.query("http_server_active_requests"))
            ),
            "active requests returning to zero",
        )
    else:
        expect(
            not native_families and "http_server_" not in raw,
            "Native metrics active in domain baseline.",
        )

    def delivery_ready() -> bool:
        try:
            verify_delivery_snapshot(
                raw_before, exposition_samples(stack.exporter_text()), native=native
            )
            verify_delivery_snapshot(
                stored_before, stack.query('{job="application-domain-metrics"}'), native=native
            )
        except SmokeFailure:
            return False
        return True

    eventually(delivery_ready, "raw Collector and Prometheus request deltas/histograms")
    eventually(lambda: trace_delivered(stack, trace_id), "Jaeger trace delivery")
    # Collector absence must not become a business/readiness dependency.
    stack.run(("stop", "--timeout", "15", "otel-collector"))
    trace_outage = business_request(stack, base, CONVERSATION + "_OUTAGE")
    for path in ("/health", "/ready"):
        expect(request_json(base, path)[0] == 200, "Metrics outage changed health semantics.")
    _, health = request_json(base, "/health/details")
    expect(health["metrics"]["request_count"] == 2, "Operational summary stopped updating.")
    eventually(
        lambda: "Telemetry export failed." in stack.run(("logs", "--no-color", "backend")).stdout,
        "bounded export failure",
    )
    logs = stack.run(("logs", "--no-color", "backend", "otel-collector", "prometheus")).stdout
    scan_private(logs)
    eventually(lambda: trace_delivered(stack, trace_outage), "outage trace delivery")
    stack.run(("start", "otel-collector"))
    trace_recovery = business_request(stack, base, CONVERSATION + "_RECOVERY")
    eventually(
        lambda: metric_value(stack, agent_counter) == 3, "post-recovery cumulative domain delivery"
    )
    expect(
        metric_value(stack, 'agent_run_duration_seconds_count{status="ok"}') == 3,
        "Recovered domain histogram count is incorrect.",
    )
    if native:
        eventually(
            lambda: (
                metric_value(
                    stack, 'http_server_request_duration_seconds_count{http_route="/agent/chat"}'
                )
                == 3
            ),
            "post-recovery native delivery",
        )
    eventually(lambda: trace_delivered(stack, trace_recovery), "recovery trace delivery")
    stored = stack.query('{job="application-domain-metrics"}')
    series_path = "/api/v1/series?" + urllib.parse.urlencode(
        {"match[]": '{job="application-domain-metrics"}'}
    )
    _, series_response = request_json(stack.url("prometheus", 9090), series_path)
    expect(
        series_response.get("status") == "success" and bool(series_response.get("data")),
        "Stored series capture is empty.",
    )
    series = series_response["data"]
    scan_private(json.dumps(series))
    expect(bool(stored), "Stored metrics capture is empty.")
    scan_private(json.dumps(stored), CONVERSATION + "_OUTAGE", CONVERSATION + "_RECOVERY")
    forbidden = {
        "customer_id",
        "actor_id",
        "conversation_id",
        "request_id",
        "agent_run_id",
        "memory_key",
        "url_path",
        "url_query",
        "authorization",
    }
    for labels in [sample["metric"] for sample in stored] + series:
        expect(not forbidden.intersection(labels), "Forbidden series label present.")
    scan_private(stack.exporter_text())
    scan_private(
        stack.run(("logs", "--no-color", "backend", "otel-collector", "prometheus")).stdout
    )
    print(
        json.dumps(
            {
                "result": "passed",
                "native_metrics": native,
                "domain_families": names,
                "requests": 3,
                "collector_recovery": True,
            }
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", default=os.getenv("T4D_COMPOSE_PROJECT_NAME", "t4d-metrics-delivery")
    )
    arguments = parser.parse_args()
    stack = MetricsStack(arguments.project)
    try:
        run_delivery(stack)
        return 0
    except (SmokeFailure, OSError, ValueError, KeyError):
        print(
            "Metrics delivery acceptance failed; inspect isolated validation locally.",
            file=sys.stderr,
        )
        return 1
    finally:
        stack.clean()


if __name__ == "__main__":
    raise SystemExit(main())
