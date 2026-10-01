"""Disposable real-process SIGTERM/flush and Jaeger outage evidence for T5.

This integration fixture is not production deployment or TLS acceptance.
"""

from __future__ import annotations

import argparse
import json
import select
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from scripts.e2e_authenticated_smoke import SmokeFailure, expect, request_json, wait_for_ready
from scripts.otel_metrics_delivery_smoke import (
    CONVERSATION,
    MESSAGE,
    QUERY,
    TOKEN,
    MetricsStack,
    business_request,
    eventually,
    metric_value,
    scan_private,
)


class RuntimeStack(MetricsStack):
    def __init__(self, project: str, scenario: str) -> None:
        expect(project.startswith("t4d-metrics-t5-"), "T5 disposable project prefix required.")
        super().__init__(project)
        expect(
            scenario in {"graceful", "jaeger-outage", "both-outage", "disabled", "draining"},
            "Unknown runtime scenario.",
        )
        self.environment.update(
            OTEL_BSP_SCHEDULE_DELAY="60000",
            OTEL_EXPORTER_OTLP_TRACES_TIMEOUT="0.5",
            OTEL_METRIC_EXPORT_INTERVAL_MILLIS="300000",
        )
        self.scenario = scenario
        self._fixture: TemporaryDirectory[str] | None = None
        if scenario in {"disabled", "both-outage"}:
            self._fixture = TemporaryDirectory(prefix="otel-t5-")
            backend: dict[str, Any] = {}
            if scenario == "disabled":
                backend["environment"] = {"OTEL_ENABLED": "false"}
            else:
                log_config = Path(self._fixture.name, "logging.json")
                log_config.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "disable_existing_loggers": False,
                            "formatters": {
                                "bounded": {"format": "%(levelname)s:%(name)s:%(message)s"}
                            },
                            "handlers": {
                                "stderr": {
                                    "class": "logging.StreamHandler",
                                    "formatter": "bounded",
                                    "stream": "ext://sys.stderr",
                                }
                            },
                            "root": {"handlers": ["stderr"], "level": "WARNING"},
                            "loggers": {
                                name: {"handlers": ["stderr"], "level": "INFO", "propagate": False}
                                for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
                            },
                        }
                    )
                )
                backend["volumes"] = [
                    {
                        "type": "bind",
                        "source": str(log_config),
                        "target": "/tmp/t5-logging.json",
                        "read_only": True,
                    }
                ]
                backend["command"] = [
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                    "--timeout-keep-alive",
                    "5",
                    "--timeout-graceful-shutdown",
                    "30",
                    "--log-config",
                    "/tmp/t5-logging.json",
                ]
            Path(self._fixture.name, "override.json").write_text(
                json.dumps({"services": {"backend": backend}})
            )

    @property
    def command(self) -> list[str]:
        command = super().command
        if self._fixture is not None:
            command.extend(("--file", str(Path(self._fixture.name, "override.json"))))
        return command

    def clean(self) -> None:
        # Unlike best-effort interactive cleanup, acceptance must fail if down fails.
        self.run(("down", "--volumes", "--remove-orphans", "--timeout", "20"), timeout=120)

    def close_fixture(self) -> None:
        if self._fixture is not None:
            self._fixture.cleanup()
            self._fixture = None


def backend_exit(stack: RuntimeStack) -> int:
    output = stack.run(("ps", "--all", "--format", "json", "backend")).stdout
    rows: list[dict[str, Any]] = (
        json.loads(output)
        if output.lstrip().startswith("[")
        else [json.loads(line) for line in output.splitlines() if line.strip()]
    )
    expect(len(rows) == 1 and rows[0].get("State") == "exited", "Backend did not exit.")
    value = rows[0].get("ExitCode")
    expect(type(value) is int, "Backend exit code unavailable.")
    assert isinstance(value, int)
    return value


def trace_graph(stack: RuntimeStack, trace_id: str) -> dict[str, Any] | None:
    code, payload = request_json(stack.url("jaeger", 16686), f"/api/traces/{trace_id}", timeout=3)
    if code != 200 or not payload.get("data"):
        return None
    graph: dict[str, Any] = payload["data"][0]
    return graph


def drain_request(stack: RuntimeStack, base: str) -> tuple[str, float, float]:
    """Gate a real order-read query using an isolated DB transaction, then signal Uvicorn."""
    command = [
        *stack.command,
        "exec",
        "-T",
        "db",
        "psql",
        "-X",
        "-qAt",
        "-U",
        "app",
        "-d",
        "customer_service",
    ]
    process = subprocess.Popen(
        command,
        env=stack.environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = None
        try:
            process.stdin.write(
                "BEGIN; LOCK TABLE orders IN ACCESS EXCLUSIVE MODE; SELECT 't5-lock-held';\n"
            )
            process.stdin.flush()
            readable, _, _ = select.select([process.stdout], [], [], 10)
            expect(
                bool(readable) and process.stdout.readline().strip() == "t5-lock-held",
                "Database gate was not acquired.",
            )
            started = time.monotonic()
            pending = executor.submit(
                request_json,
                base,
                f"/agent/chat?secret={QUERY}",
                token=TOKEN,
                payload={
                    "conversation_id": CONVERSATION + "_T5",
                    "customer_id": 2,
                    "message": MESSAGE,
                },
                timeout=25,
            )
            eventually(
                lambda: (
                    int(
                        stack.database_scalar(
                            "select count(*) from pg_stat_activity "
                            "where datname='customer_service' "
                            "and wait_event_type='Lock' and query ilike '%orders%';"
                        )
                    )
                    >= 1
                ),
                "business request reaching database gate",
                timeout=10,
            )
            expect(not pending.done(), "Business request did not remain in flight.")
            signal_started = time.monotonic()
            stack.run(("kill", "--signal", "SIGTERM", "backend"), timeout=10)
            eventually(
                lambda: (
                    "Waiting for connections to close"
                    in stack.run(("logs", "--no-color", "backend")).stdout
                ),
                "Uvicorn draining an active connection",
                timeout=10,
            )
            expect(not pending.done(), "Request completed before the controlled release.")
            process.stdin.write("COMMIT;\n\\q\n")
            process.stdin.flush()
            expect(process.wait(timeout=5) == 0, "Database gate did not release safely.")
            code, response = pending.result(timeout=10)
            expect(
                code == 200 and response.get("tool_call") == {"status": "executed"},
                "In-flight business request failed while draining.",
            )
            request_seconds = time.monotonic() - started
            run_id = str(UUID(response["agent_run_id"]))
            trace_id = stack.database_scalar(
                f"select trace_id from agent_run_projections where run_id='{run_id}';"
            )
            expect(
                len(trace_id) == 32,
                "Drained business projection did not persist trace correlation.",
            )

            def exited() -> bool:
                try:
                    backend_exit(stack)
                    return True
                except SmokeFailure:
                    return False

            eventually(exited, "backend exit after request completion", timeout=30)
            return trace_id, request_seconds, time.monotonic() - signal_started
        finally:
            if process.poll() is None:
                try:
                    process.stdin.write("ROLLBACK;\n\\q\n")
                    process.stdin.flush()
                    process.wait(timeout=5)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            if pending is not None:
                # Release the DB lock before waiting on the HTTP thread.
                pending.result(timeout=30)


def run_runtime(stack: RuntimeStack) -> dict[str, Any]:
    stack.stage = "startup"
    stack.start()
    base = stack.url("backend", 8000)
    wait_for_ready(base)
    model = json.loads(stack.run(("config", "--format", "json")).stdout)
    backend = model["services"]["backend"]
    expect(backend["environment"]["WEB_CONCURRENCY"] == "1", "One backend worker required.")
    expect(backend["deploy"]["replicas"] == 1, "One backend replica required.")
    expect(
        backend["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://jaeger:4317",
        "Trace route changed.",
    )
    stack.stage = "business-request"
    if stack.scenario in {"jaeger-outage", "both-outage"}:
        targets = ("jaeger", "otel-collector") if stack.scenario == "both-outage" else ("jaeger",)
        stack.run(("stop", "--timeout", "15", *targets))
    started = time.monotonic()
    if stack.scenario == "draining":
        trace_id, request_seconds, shutdown_seconds = drain_request(stack, base)
    elif stack.scenario == "disabled":
        expect(backend["environment"]["OTEL_ENABLED"] == "false", "Master switch not disabled.")
        expect(
            backend["environment"]["OTEL_METRICS_ENABLED"] == "true",
            "Metric flag must remain enabled.",
        )
        code, response = request_json(
            base,
            f"/agent/chat?secret={QUERY}",
            token=TOKEN,
            payload={"conversation_id": CONVERSATION + "_T5", "customer_id": 2, "message": MESSAGE},
        )
        expect(
            code == 200 and response.get("tool_call") == {"status": "executed"},
            "Disabled business request failed.",
        )
        code, view = request_json(base, f"/ui/agent-runs/{response['agent_run_id']}", token=TOKEN)
        expect(
            code == 200 and view.get("trace_id") is None,
            "Disabled projection is missing or recorded a trace.",
        )
        trace_id = ""
    else:
        trace_id = business_request(stack, base, CONVERSATION + "_T5")
    if stack.scenario != "draining":
        request_seconds = time.monotonic() - started
        expect(request_seconds < 15, "Business request exceeded validation deadline.")
        for path in ("/health", "/ready"):
            expect(request_json(base, path)[0] == 200, "Telemetry changed health/readiness.")
        _, details = request_json(base, "/health/details")
        expect(details["metrics"]["request_count"] == 1, "Operational summary stopped updating.")
        expect(not stack.query("agent_runs_total"), "Domain metric exported before final flush.")
        expect(
            not stack.query('http_server_request_duration_seconds_count{http_route="/agent/chat"}'),
            "Native metric exported before final flush.",
        )
        if stack.scenario in {"graceful", "draining"}:
            expect(
                trace_graph(stack, trace_id) is None, "Request trace exported before final flush."
            )
        stack.stage = "sigterm"
        started = time.monotonic()
        stack.run(("stop", "--timeout", "35", "backend"), timeout=50)
        shutdown_seconds = time.monotonic() - started
        expect(shutdown_seconds < 35, "Shutdown exceeded Compose grace period.")
    expect(shutdown_seconds < 35, "Shutdown exceeded Compose grace period.")
    exit_code = backend_exit(stack)
    # Locked Uvicorn re-raises the received SIGTERM after graceful completion.
    # Lifespan completion and final delivery below distinguish this from forced kill.
    expect(exit_code in {0, 143}, "Backend required forced termination or failed shutdown.")
    logs = stack.run(("logs", "--no-color", "backend", "otel-collector", "prometheus")).stdout
    scan_private(logs)
    expect("Application shutdown complete." in logs, "ASGI lifespan did not complete.")
    stack.stage = "final-delivery"
    if stack.scenario == "disabled":
        expect(
            not stack.query('{job="application-domain-metrics",__name__!~"up|scrape_.*"}'),
            "Disabled application metrics exported.",
        )
        code, traces = request_json(
            stack.url("jaeger", 16686), "/api/traces?service=agentic-customer-service-platform"
        )
        expect(code == 200 and not traces.get("data"), "Disabled application traces exported.")
        expect("Telemetry export failed." not in logs, "Disabled mode attempted export.")
    elif stack.scenario == "both-outage":
        expect(
            "Telemetry export failed." in logs, "Endpoint outage did not exercise export failure."
        )
        expect(
            "app.observability.export" in logs and "app.observability.metric_export" in logs,
            "Both signal failures must be observed.",
        )
        expect(not stack.query("agent_runs_total"), "Outage was reported as metric delivery.")
    else:
        eventually(
            lambda: metric_value(stack, "agent_runs_total") == 1, "SIGTERM domain metric delivery"
        )
        eventually(
            lambda: (
                metric_value(
                    stack, 'http_server_request_duration_seconds_count{http_route="/agent/chat"}'
                )
                == 1
            ),
            "SIGTERM native metric delivery",
        )
        if stack.scenario in {"graceful", "draining"}:
            eventually(lambda: trace_graph(stack, trace_id) is not None, "SIGTERM trace delivery")
            graph = trace_graph(stack, trace_id)
            assert graph is not None
            scan_private(json.dumps(graph))
            spans = graph["spans"]
            servers = [
                span
                for span in spans
                if any(
                    tag["key"] == "span.kind" and tag["value"] == "server" for tag in span["tags"]
                )
            ]
            expect(len(servers) == 1, "Duplicate or missing request SERVER span.")
            expect(
                {"agent.run", "tool.execute"} <= {span["operationName"] for span in spans},
                "Domain spans missing.",
            )
            expect(
                all(span["traceID"] == trace_id for span in spans),
                "Projection/trace correlation changed.",
            )
        else:
            expect(
                "Telemetry export failed." in logs,
                "Jaeger outage did not exercise bounded export failure.",
            )
        raw = stack.exporter_text()
        scan_private(raw)
        expect(
            "target_info" not in raw and "otel_scope_info" not in raw,
            "Resource/scope metadata leaked.",
        )
        scan_private(json.dumps(stack.query('{job="application-domain-metrics"}')))
    return {
        "scenario": stack.scenario,
        "result": "passed",
        "request_seconds": round(request_seconds, 3),
        "shutdown_seconds": round(shutdown_seconds, 3),
        "exit_code": exit_code,
        "final_metric_delivery": stack.scenario not in {"disabled", "both-outage"},
        "final_trace_delivery": stack.scenario in {"graceful", "draining"},
        "deployment_tls_acceptance": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=("graceful", "jaeger-outage", "both-outage", "disabled", "draining"),
        default="graceful",
    )
    parser.add_argument("--project", default="t4d-metrics-t5-runtime")
    args = parser.parse_args()
    stack = RuntimeStack(args.project, args.scenario)
    result: dict[str, Any] | None = None
    code = 0
    try:
        result = run_runtime(stack)
    except (SmokeFailure, OSError, ValueError, KeyError) as error:
        print(
            f"Runtime acceptance failed at {stack.stage} ({type(error).__name__}); "
            "details withheld."
        )
        code = 1
    finally:
        try:
            stack.clean()
        except (SmokeFailure, OSError):
            print("Runtime cleanup failed; details withheld.")
            code = 1
        stack.close_fixture()
    if code == 0:
        print(json.dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
