"""Guard runtime evidence scope without claiming these units prove SIGTERM delivery."""

import json
import subprocess

import pytest

from scripts.e2e_authenticated_smoke import SmokeFailure
from scripts.otel_runtime_smoke import RuntimeStack, backend_exit


def test_runtime_stack_is_disposable_and_defers_periodic_exports() -> None:
    with pytest.raises(SmokeFailure, match="T5 disposable"):
        RuntimeStack("customer-service", "graceful")
    with pytest.raises(SmokeFailure, match="Unknown runtime"):
        RuntimeStack("t4d-metrics-t5-unit", "untrusted")
    stack = RuntimeStack("t4d-metrics-t5-unit", "graceful")
    assert stack.environment["OTEL_BSP_SCHEDULE_DELAY"] == "60000"
    assert stack.environment["OTEL_METRIC_EXPORT_INTERVAL_MILLIS"] == "300000"
    assert stack.environment["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT"] == "0.5"
    assert stack.environment["OTEL_METRICS_ENABLED"] == "true"
    assert stack.environment["COMPOSE_OTEL_METRICS_ENDPOINT"] == "http://otel-collector:4317"


@pytest.mark.parametrize("array", [True, False])
def test_backend_exit_parses_supported_compose_json_forms(
    monkeypatch: pytest.MonkeyPatch,
    array: bool,
) -> None:
    stack = RuntimeStack("t4d-metrics-t5-unit", "graceful")
    row = {"State": "exited", "ExitCode": 0}
    monkeypatch.setattr(
        stack,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps([row] if array else row), ""
        ),
    )
    assert backend_exit(stack) == 0


@pytest.mark.parametrize("row", [{"State": "running", "ExitCode": 0}, {"State": "exited"}])
def test_backend_exit_does_not_treat_running_or_unknown_exit_as_success(
    monkeypatch: pytest.MonkeyPatch,
    row: dict[str, object],
) -> None:
    stack = RuntimeStack("t4d-metrics-t5-unit", "graceful")
    monkeypatch.setattr(
        stack,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(row), ""),
    )
    with pytest.raises(SmokeFailure):
        backend_exit(stack)


def test_disabled_override_preserves_metric_opt_in_and_disposes_fixture() -> None:
    from pathlib import Path

    stack = RuntimeStack("t4d-metrics-t5-unit", "disabled")
    try:
        location = Path(stack.command[-1])
        assert location.is_file()
        data = json.loads(location.read_text())
        assert data == {"services": {"backend": {"environment": {"OTEL_ENABLED": "false"}}}}
        assert stack.environment["OTEL_METRICS_ENABLED"] == "true"
        assert stack.environment["COMPOSE_OTEL_METRICS_ENDPOINT"]
    finally:
        stack.close_fixture()
    assert not location.exists()


def test_both_outage_fixture_only_adds_bounded_log_format() -> None:
    from pathlib import Path

    stack = RuntimeStack("t4d-metrics-t5-unit", "both-outage")
    try:
        backend = json.loads(Path(stack.command[-1]).read_text())["services"]["backend"]
        assert "environment" not in backend
        assert backend["command"][-2:] == ["--log-config", "/tmp/t5-logging.json"]
        assert backend["volumes"][0]["read_only"] is True
        config = json.loads(Path(backend["volumes"][0]["source"]).read_text())
        assert config["root"]["level"] == "WARNING"
        assert config["formatters"]["bounded"]["format"] == "%(levelname)s:%(name)s:%(message)s"
    finally:
        stack.close_fixture()


@pytest.mark.parametrize("failure", ["runtime", "cleanup"])
def test_main_fails_boundedly_and_never_reports_success_if_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    import sys

    from scripts import otel_runtime_smoke as module

    stack = RuntimeStack("t4d-metrics-t5-unit", "graceful")
    closed: list[str] = []
    monkeypatch.setattr(sys, "argv", ["runtime"])
    monkeypatch.setattr(module, "RuntimeStack", lambda *args: stack)

    def failed(*args: object) -> None:
        raise SmokeFailure("PRIVATE_FAILURE_DETAILS")

    monkeypatch.setattr(
        module,
        "run_runtime",
        failed if failure == "runtime" else lambda *args: {"result": "passed"},
    )
    monkeypatch.setattr(
        stack, "clean", failed if failure == "cleanup" else lambda: closed.append("clean")
    )
    monkeypatch.setattr(stack, "close_fixture", lambda: closed.append("fixture"))
    assert module.main() == 1
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and '"result": "passed"' not in output
    assert closed[-1] == "fixture"
    if failure == "runtime":
        assert closed == ["clean", "fixture"]
