"""Isolated simulation worker for the console.

Reads ``{"scenario_ids": [...], "config": {...}, "dataset": "..."}`` on stdin and writes the
scenario results with transcripts to stdout as JSON. It runs in its own process because fault
scenarios patch module-level tool definitions. Decisions are scripted, embeddings deterministic,
and every database is in memory: a simulation never calls a paid provider or touches real data.
"""

import json
import sys
from pathlib import Path
from typing import Any

from app.agent.schemas import AgentResponse
from app.agent_config.context import use_agent_config
from app.agent_config.schemas import AgentConfig
from evaluation.runner import load_scenarios, run_scenario


def simulate(payload: dict[str, Any]) -> list[dict[str, Any]]:
    wanted = set(payload["scenario_ids"])
    scenarios = [s for s in load_scenarios(Path(payload["dataset"])) if s.id in wanted]
    config = AgentConfig.model_validate(payload.get("config") or {})
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        responses: list[AgentResponse] = []
        with use_agent_config(config):
            result = run_scenario(scenario, responses_sink=responses)
        transcript = [
            {
                "user": turn.user,
                "assistant": response.message,
                "intent": response.intent.value,
                "tool": response.tool_call.name if response.tool_call else None,
                "status": response.tool_call.status
                if response.tool_call
                else (response.pending_action.status if response.pending_action else None),
            }
            for turn, response in zip(scenario.conversation, responses, strict=False)
        ]
        results.append(
            {
                "scenario_id": result.scenario_id,
                "category": result.category,
                "passed": result.passed,
                "critical_safety": scenario.expect.critical_safety,
                "failure_reasons": result.failure_reasons,
                "selected_tools": result.selected_tools,
                "executed_tools": result.executed_tools,
                "latency_ms": round(result.latency_ms, 1),
                "transcript": transcript,
            }
        )
    return results


def main() -> int:
    payload = json.load(sys.stdin)
    json.dump({"results": simulate(payload)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
