from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.integration.yml")
CONVERSATION_ID = "e2e-authenticated-cancellation"
EXPECTED_ACTOR_ID = "e2e-support-operator"
CUSTOMER_ID = 2
ORDER_ID = 3
MEMORY_CUSTOMER_ID = 3
MEMORY_PRIVATE_CONTENT = "PRIVATE_MEMORY_SENTINEL_DO_NOT_EXPOSE"
ACTION_ID_PATTERN = re.compile(r"^act_[0-9a-f]{32}$")


class SmokeFailure(RuntimeError):
    """A bounded integration invariant failure safe to print in CI."""


class ComposeStack:
    def __init__(self, project: str, token: str) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]+", project):
            raise SmokeFailure("E2E Compose project name contains unsupported characters.")
        self.project = project
        self.token = token
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "LOCAL_DEMO_AUTH_TOKEN": token,
                "LOCAL_DEMO_ACTOR_ID": EXPECTED_ACTOR_ID,
                "BACKEND_PORT": "0",
                "FRONTEND_PORT": "0",
                "POSTGRES_PORT": "0",
                "QDRANT_HTTP_PORT": "0",
                "QDRANT_GRPC_PORT": "0",
                "JAEGER_UI_PORT": "0",
                "OTEL_GRPC_PORT": "0",
                "OTEL_HTTP_PORT": "0",
            }
        )

    @property
    def command(self) -> list[str]:
        command = ["docker", "compose", "--project-name", self.project]
        for compose_file in COMPOSE_FILES:
            command.extend(("--file", compose_file))
        command.extend(("--env-file", ".env.example"))
        return command

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int = 300,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [*self.command, *arguments],
                cwd=ROOT,
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SmokeFailure(f"Compose command timed out after {timeout} seconds.") from error
        if check and result.returncode != 0:
            output = redact(
                redact(f"{result.stdout}\n{result.stderr}", self.token),
                MEMORY_PRIVATE_CONTENT,
            )[-6000:]
            raise SmokeFailure(f"Compose command failed ({arguments[0]}):\n{output}")
        return result

    def clean(self) -> None:
        self.run(
            ("down", "--volumes", "--remove-orphans", "--timeout", "20"),
            timeout=120,
            check=False,
        )

    def start(self) -> None:
        self.clean()
        self.run(
            ("up", "--build", "--detach", "--wait", "--wait-timeout", "240"),
            timeout=600,
        )

    def frontend_url(self) -> str:
        result = self.run(("port", "frontend", "8080"), timeout=30)
        address = result.stdout.strip().splitlines()
        if len(address) != 1 or ":" not in address[0]:
            raise SmokeFailure("Could not resolve the isolated frontend host port.")
        port = address[0].rsplit(":", 1)[1]
        if not port.isdigit():
            raise SmokeFailure("Compose returned an invalid frontend host port.")
        return f"http://127.0.0.1:{port}"

    def database_scalar(self, statement: str) -> str:
        result = self.run(
            (
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "app",
                "-d",
                "customer_service",
                "-Atc",
                statement,
            ),
            timeout=30,
        )
        return result.stdout.strip()

    def qdrant_collection(self) -> dict[str, Any]:
        program = (
            "import json,urllib.request; "
            "data=json.load(urllib.request.urlopen("
            "'http://qdrant:6333/collections/customer_service_knowledge',timeout=5)); "
            "print(json.dumps(data['result']))"
        )
        result = self.run(("exec", "-T", "backend", "python", "-c", program), timeout=30)
        return expect_object(json.loads(result.stdout), "Qdrant collection response")

    def qdrant_alias_target(self) -> str:
        program = (
            "from qdrant_client import QdrantClient; "
            "client=QdrantClient('http://qdrant:6333'); "
            "aliases=client.get_aliases().aliases; "
            "print(next((a.collection_name for a in aliases "
            "if a.alias_name == 'customer_service_knowledge'), ''))"
        )
        result = self.run(("exec", "-T", "backend", "python", "-c", program), timeout=30)
        return result.stdout.strip()

    def restart_backend(self) -> None:
        self.run(("restart", "--timeout", "40", "backend"), timeout=90)

    def receipt_count(self, action_id: str) -> int:
        if ACTION_ID_PATTERN.fullmatch(action_id) is None:
            raise SmokeFailure("Pending action ID does not match the server-generated format.")
        query = (
            "select count(*) from business_action_receipts "
            f"where actor_id = '{EXPECTED_ACTOR_ID}' and operation = 'cancel_order' "
            f"and idempotency_key = '{action_id}';"
        )
        value = self.database_scalar(query)
        if not value.isdigit():
            raise SmokeFailure("Idempotency receipt query returned a non-numeric result.")
        return int(value)

    def diagnostics(self) -> str:
        status = self.run(("ps", "--all"), timeout=30, check=False)
        logs = self.run(
            ("logs", "--no-color", "--tail", "120", "demo-setup", "backend", "frontend"),
            timeout=30,
            check=False,
        )
        return redact(
            redact(
                f"Compose status:\n{status.stdout}\n"
                f"Recent bounded logs:\n{logs.stdout}{logs.stderr}",
                self.token,
            ),
            MEMORY_PRIVATE_CONTENT,
        )


def redact(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def expect_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SmokeFailure(f"{label} was not a JSON object.")
    return value


def expect_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SmokeFailure(f"{label} was not a JSON list.")
    return value


def assert_no_sensitive_projection_fields(value: object) -> None:
    forbidden = {"authorization", "token", "credential", "secret", "prompt", "chain_of_thought"}
    if isinstance(value, dict):
        exposed = forbidden.intersection(str(key).casefold() for key in value)
        expect(not exposed, f"Projection exposed forbidden field(s): {sorted(exposed)}.")
        for nested in value.values():
            assert_no_sensitive_projection_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            assert_no_sensitive_projection_fields(nested)


def _request_json_value(
    base_url: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
    timeout: float = 15.0,
) -> tuple[int, object]:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    method = "GET"
    if payload is not None:
        method = "POST"
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"{base_url}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        raw_body = error.read().decode("utf-8")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise SmokeFailure(f"{path} did not return JSON (HTTP {status}).") from error
    return status, parsed


def request_json(
    base_url: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any]]:
    status, parsed = _request_json_value(
        base_url,
        path,
        token=token,
        payload=payload,
        timeout=timeout,
    )
    return status, expect_object(parsed, path)


def request_json_list(
    base_url: str,
    path: str,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> tuple[int, list[Any]]:
    status, parsed = _request_json_value(base_url, path, token=token, timeout=timeout)
    return status, expect_list(parsed, path)


def wait_for_ready(base_url: str, timeout_seconds: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status: int | None = None
    while time.monotonic() < deadline:
        try:
            last_status, payload = request_json(base_url, "/ready", timeout=3.0)
            if last_status == 200 and payload.get("status") == "ready":
                return
        except (OSError, SmokeFailure):
            pass
        time.sleep(1)
    raise SmokeFailure(f"Frontend-proxied readiness did not recover; last status={last_status}.")


def assert_pending_response(response: dict[str, Any]) -> None:
    pending = expect_object(response.get("pending_action"), "pending action")
    expect(pending.get("status") == "pending", "Risk-2 action was not left pending.")
    expect(set(pending) == {"status"}, "Customer response exposed pending action metadata.")


def assert_projection(projection: dict[str, Any], *, confirmation_run: bool) -> None:
    expect(projection.get("actor_id") == EXPECTED_ACTOR_ID, "Projection actor is wrong.")
    expect(projection.get("actor_type") == "support_operator", "Projection actor type is wrong.")
    expect(projection.get("customer_id") == CUSTOMER_ID, "Projection customer scope is wrong.")
    expect(
        projection.get("conversation_id") == CONVERSATION_ID,
        "Projection conversation is wrong.",
    )
    path = projection.get("path")
    expect(isinstance(path, list), "Projection path is missing.")
    assert isinstance(path, list)
    expect("check_pending_action" in path, "Projection omitted confirmation evaluation.")
    tools = projection.get("tools")
    expect(isinstance(tools, list) and len(tools) == 1, "Projection tool event is missing.")
    assert isinstance(tools, list)
    tool = expect_object(tools[0], "projection tool")
    expect(tool.get("name") == "cancel_order", "Projection contains the wrong tool.")
    expect(tool.get("risk_level") == 2, "Projection contains the wrong risk level.")
    if confirmation_run:
        expect(tool.get("status") == "completed", "Confirmed tool was not projected as completed.")
        expect("policy_revalidate" in path, "Projection omitted policy revalidation.")
        expect("execute_tool" in path, "Projection omitted confirmed execution.")
    else:
        expect(
            tool.get("status") == "blocked_before_execution",
            "Initial tool was not projected as blocked before execution.",
        )
        policy = projection.get("policy")
        expect(isinstance(policy, list) and len(policy) >= 1, "Policy projection is missing.")
        assert isinstance(policy, list)
        policy_event = expect_object(policy[-1], "projection policy event")
        expect(
            policy_event.get("outcome") == "require_confirmation",
            "Projection omitted confirmation-required policy outcome.",
        )


def authenticated_order(base_url: str, token: str) -> dict[str, Any]:
    status, order = request_json(
        base_url,
        f"/orders/{ORDER_ID}?customer_id={CUSTOMER_ID}",
        token=token,
    )
    expect(status == 200, f"Order read failed with HTTP {status}.")
    return order


def run_smoke(stack: ComposeStack) -> None:
    stack.start()
    base_url = stack.frontend_url()
    wait_for_ready(base_url)

    bootstrap = stack.database_scalar(
        "select (select version_num from alembic_version) || '|' || "
        "(select count(*) from customers) || '|' || "
        "(select count(*) from orders) || '|' || "
        "(select count(*) from support_tickets) || '|' || "
        "(select count(*) from memory_records);"
    )
    expect(bootstrap == "20261122_0013|3|6|4|1", "Migration or demo seed state is incorrect.")
    projection_schema = stack.database_scalar(
        "select (to_regclass('public.agent_run_projections') is not null)::text || '|' || "
        "(select count(*) from pg_indexes where tablename = 'agent_run_projections' "
        "and indexname in ('ix_agent_run_projection_conversation_created', "
        "'ix_agent_run_projection_customer_created', 'ix_agent_run_projection_created')) || '|' || "
        "(select count(*) from pg_constraint where conrelid = 'agent_run_projections'::regclass "
        "and contype = 'u');"
    )
    expect(projection_schema == "true|3|1", "Projection schema or indexes are incorrect.")
    collection = stack.qdrant_collection()
    expect(collection.get("status") == "green", "Qdrant collection is not green.")
    expect(collection.get("points_count") == 14, "Knowledge ingestion did not load 14 chunks.")
    metadata = collection.get("config", {}).get("metadata", {})
    provenance = metadata.get("knowledge_snapshot", {}) if isinstance(metadata, dict) else {}
    expect(
        isinstance(provenance, dict)
        and len(str(provenance.get("corpus_hash", ""))) == 64
        and len(str(provenance.get("snapshot_spec_hash", ""))) == 64
        and provenance.get("snapshot_id") == provenance.get("snapshot_spec_hash"),
        "Qdrant snapshot provenance does not contain a full corpus/spec identity.",
    )
    active_snapshot = stack.qdrant_alias_target()
    expect(
        re.fullmatch(r"customer_service_knowledge_v_[0-9a-f]{16}", active_snapshot) is not None,
        "Qdrant runtime is not serving a versioned snapshot alias target.",
    )
    health_status, health = request_json(base_url, "/ui/system-health", token=stack.token)
    expect(health_status == 200, "Authenticated system health is unavailable.")
    expect(health.get("status") == "ready", "System health disagrees with ready endpoint.")
    health_components = {
        expect_object(component, "system health component").get("name"): component
        for component in expect_list(health.get("components"), "system health components")
    }
    for component_name in ("database", "checkpoint", "retriever"):
        component = expect_object(health_components.get(component_name), component_name)
        expect(
            component.get("status") == "healthy",
            f"System health reported {component_name} as unhealthy.",
        )
    llm_component = expect_object(health_components.get("llm"), "llm health component")
    expect(llm_component.get("status") == "not_probed", "LLM health semantics are not honest.")
    expect(
        "local retrieval boundary available" not in json.dumps(health),
        "Static retrieval health leaked.",
    )
    assert_no_sensitive_projection_fields(health)

    anonymous_status, _ = request_json(base_url, f"/ui/memory/{MEMORY_CUSTOMER_ID}")
    expect(anonymous_status == 401, "Anonymous operator memory request did not return 401.")
    invalid_status, _ = request_json(
        base_url,
        f"/ui/memory/{MEMORY_CUSTOMER_ID}",
        token="invalid-integration-token",
    )
    expect(invalid_status == 401, "Invalid Bearer operator memory request did not return 401.")

    before = authenticated_order(base_url, stack.token)
    expect(before.get("status") == "processing", "Canonical order is not initially processing.")

    initial_status, initial = request_json(
        base_url,
        "/agent/chat",
        token=stack.token,
        payload={
            "conversation_id": CONVERSATION_ID,
            "customer_id": CUSTOMER_ID,
            "message": "Cancel order 3",
        },
    )
    expect(initial_status == 200, f"Initial agent request returned HTTP {initial_status}.")
    assert_pending_response(initial)
    expect(initial.get("error_category") is None, "Initial agent request returned an error.")
    expect(
        authenticated_order(base_url, stack.token).get("status") == "processing",
        "Order mutated before confirmation.",
    )
    initial_run_id = initial.get("agent_run_id")
    expect(isinstance(initial_run_id, str), "Initial agent run ID is missing.")
    initial_projection_status, initial_projection = request_json(
        base_url, f"/ui/agent-runs/{initial_run_id}", token=stack.token
    )
    expect(initial_projection_status == 200, "Initial inspector projection is unavailable.")
    assert_projection(initial_projection, confirmation_run=False)
    action_id = initial_projection.get("action_id")
    expect(isinstance(action_id, str), "Operator projection action ID is missing.")
    assert isinstance(action_id, str)
    expect(ACTION_ID_PATTERN.fullmatch(action_id) is not None, "Action ID is malformed.")
    assert_no_sensitive_projection_fields(initial_projection)
    initial_audit_status, initial_audit = request_json_list(
        base_url, f"/ui/policy-audit/{CONVERSATION_ID}", token=stack.token
    )
    expect(initial_audit_status == 200, "Initial durable policy audit is unavailable.")
    expect(len(initial_audit) == 1, "Initial policy audit event count is incorrect.")
    initial_audit_event = expect_object(initial_audit[0], "initial policy audit event")
    expect(initial_audit_event.get("stage") == "policy_evaluation", "Initial audit stage is wrong.")
    assert_no_sensitive_projection_fields(initial_audit_event)

    stack.restart_backend()
    wait_for_ready(base_url)

    restarted_projection_status, restarted_projection = request_json(
        base_url, f"/ui/agent-runs/{initial_run_id}", token=stack.token
    )
    expect(
        restarted_projection_status == 200,
        "Durable inspector projection did not survive the backend restart.",
    )
    expect(
        restarted_projection.get("run_id") == initial_run_id,
        "Restarted inspector projection changed the run identity.",
    )
    assert_projection(restarted_projection, confirmation_run=False)
    assert_no_sensitive_projection_fields(restarted_projection)

    confirmation_status, confirmation = request_json(
        base_url,
        "/agent/chat",
        token=stack.token,
        payload={
            "conversation_id": CONVERSATION_ID,
            "customer_id": CUSTOMER_ID,
            "message": "confirm",
        },
    )
    expect(confirmation_status == 200, f"Confirmation returned HTTP {confirmation_status}.")
    confirmed_action = expect_object(confirmation.get("pending_action"), "confirmed action")
    expect(confirmed_action.get("status") == "executed", "Confirmed action was not executed.")
    expect(set(confirmed_action) == {"status"}, "Customer response exposed action metadata.")
    tool_call = expect_object(confirmation.get("tool_call"), "confirmed tool call")
    expect(tool_call.get("status") == "executed", "Confirmation tool execution failed.")
    expect(
        authenticated_order(base_url, stack.token).get("status") == "cancelled",
        "Order was not cancelled after confirmation.",
    )
    expect(stack.receipt_count(action_id) == 1, "Exactly one idempotency receipt was not stored.")

    confirmation_run_id = confirmation.get("agent_run_id")
    expect(isinstance(confirmation_run_id, str), "Confirmation run ID is missing.")
    expect(
        confirmation_run_id != initial_run_id,
        "Confirmation incorrectly reused the initial invocation run identity.",
    )
    projection_status, projection = request_json(
        base_url, f"/ui/agent-runs/{confirmation_run_id}", token=stack.token
    )
    expect(projection_status == 200, "Confirmation inspector projection is unavailable.")
    assert_projection(projection, confirmation_run=True)
    assert_no_sensitive_projection_fields(projection)
    expect(
        initial_projection.get("run_id") != projection.get("run_id")
        and initial_projection.get("request_id") != projection.get("request_id")
        and initial_projection.get("action_id") == projection.get("action_id") == action_id,
        "Invocation and stable action identities were not separated in projections.",
    )

    replay_status, replay = request_json(
        base_url,
        "/agent/chat",
        token=stack.token,
        payload={
            "conversation_id": CONVERSATION_ID,
            "customer_id": CUSTOMER_ID,
            "message": "confirm",
        },
    )
    expect(replay_status == 200, f"Confirmation replay returned HTTP {replay_status}.")
    expect(replay.get("tool_call") is None, "Confirmation replay executed a tool again.")
    expect("already completed" in str(replay.get("message", "")), "Replay was not stable.")
    expect(
        authenticated_order(base_url, stack.token).get("status") == "cancelled",
        "Replay changed the final order state.",
    )
    expect(stack.receipt_count(action_id) == 1, "Replay duplicated the idempotency receipt.")

    audit_status, audit_events = request_json_list(
        base_url, f"/ui/policy-audit/{CONVERSATION_ID}", token=stack.token
    )
    expect(audit_status == 200, "Durable policy audit history is unavailable after restart.")
    stages = [expect_object(event, "policy audit event").get("stage") for event in audit_events]
    expect(
        {"policy_evaluation", "confirmation", "policy_revalidation", "execution"} <= set(stages),
        "Policy audit history did not retain the full confirmation lifecycle.",
    )
    assert_no_sensitive_projection_fields(audit_events)

    memory_status, memory_records = request_json_list(
        base_url,
        f"/ui/memory/{MEMORY_CUSTOMER_ID}",
        token=stack.token,
    )
    expect(memory_status == 200, f"Operator memory projection returned HTTP {memory_status}.")
    expect(len(memory_records) == 1, "Seeded memory metadata was not visible to the operator.")
    memory_record = expect_object(memory_records[0], "operator memory record")
    expect(
        set(memory_record)
        == {
            "id",
            "customer_id",
            "memory_type",
            "normalized_key",
            "source",
            "status",
            "created_at",
            "updated_at",
            "expires_at",
        },
        "Operator memory projection did not match the metadata-only contract.",
    )
    expect(memory_record.get("customer_id") == MEMORY_CUSTOMER_ID, "Memory scope was incorrect.")
    expect(memory_record.get("normalized_key") == "response_style", "Memory key was missing.")
    serialized_memory = json.dumps(memory_records, sort_keys=True)
    expect(MEMORY_PRIVATE_CONTENT not in serialized_memory, "Raw memory content leaked via /ui.")
    expect(stack.token not in serialized_memory, "Credential leaked via the memory projection.")

    captured = json.dumps(
        [
            initial,
            initial_projection,
            confirmation,
            projection,
            replay,
            memory_records,
            audit_events,
        ],
        sort_keys=True,
    )
    expect(stack.token not in captured, "Credential appeared in an API response projection.")
    logs = stack.run(("logs", "--no-color", "backend", "frontend"), timeout=30).stdout
    expect(stack.token not in logs, "Credential appeared in application logs.")
    expect(MEMORY_PRIVATE_CONTENT not in logs, "Raw memory content appeared in application logs.")
    expect(
        "Deserializing unregistered type" not in logs,
        "Checkpoint restore used permissive unregistered-type deserialization.",
    )
    expect(
        "Blocked deserialization of" not in logs,
        "Checkpoint restore encountered a type outside the application allowlist.",
    )

    print("Authenticated full-stack lifecycle smoke passed.")
    print("bootstrap=migrated,seeded,knowledge-ingested")
    print("auth=anonymous-401,invalid-401,support-operator-authenticated")
    print("lifecycle=confirmation_required,restarted,resumed,completed,replay-safe")
    print("projection=policy-and-tool-metadata-safe")
    print("audit=durable,scoped,bounded,retained-across-restart")
    print("memory=metadata-only,private-content-absent")


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--leave-on-failure",
        action="store_true",
        help="Leave the isolated stack for an external CI log/cleanup step after failure.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    project = os.environ.get(
        "E2E_COMPOSE_PROJECT_NAME", f"customer-service-e2e-{os.getpid()}"
    ).casefold()
    token = os.environ.get("E2E_DEMO_AUTH_TOKEN", "integration-demo-credential")
    stack = ComposeStack(project, token)
    succeeded = False
    try:
        run_smoke(stack)
        succeeded = True
        return 0
    except (OSError, ValueError, SmokeFailure) as error:
        print(f"Authenticated lifecycle smoke failed: {redact(str(error), token)}", file=sys.stderr)
        print(stack.diagnostics(), file=sys.stderr)
        return 1
    finally:
        if succeeded or not args.leave_on_failure:
            stack.clean()


if __name__ == "__main__":
    raise SystemExit(main())
