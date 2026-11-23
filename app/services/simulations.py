"""Run scripted scenarios against the published or draft configuration, in a separate process.

The scenario library is ``evaluation/datasets``. Each run happens in a child Python process
(fault scenarios patch module-level state) with scripted decisions, deterministic embeddings
and in-memory databases, so it is free and cannot touch production data.
"""

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_config.schemas import AgentConfig
from app.core.time import utc_now_naive
from app.models.entities import SimulationRun
from app.ui.insights_schemas import ScenarioView, SimulationResultView, SimulationRunView

SIMULATION_TIMEOUT_SECONDS = 300
_SECRET_ENV = ("LLM_API_KEY", "OPENAI_API_KEY", "EMBEDDING_API_KEY")


class SimulationUnavailableError(RuntimeError):
    pass


def project_root() -> Path:
    return Path(os.environ.get("SIMULATION_ROOT", Path.cwd()))


def dataset_dir() -> Path:
    return project_root() / "evaluation" / "datasets"


def list_scenarios() -> list[ScenarioView]:
    directory = dataset_dir()
    if not directory.is_dir():
        raise SimulationUnavailableError("The scenario library is not available on this server.")
    scenarios: list[ScenarioView] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            expect = raw.get("expect", {})
            scenarios.append(
                ScenarioView(
                    id=raw["id"],
                    category=raw["category"],
                    customer_id=raw["customer_id"],
                    turns=[turn["user"] for turn in raw["conversation"]],
                    expected_intent=expect.get("intent"),
                    completion=expect.get("completion", "response"),
                    expected_tools=expect.get("expected_tools", []),
                    forbidden_tools=expect.get("forbidden_tools", []),
                    critical_safety=bool(expect.get("critical_safety", False)),
                )
            )
    return scenarios


def _child_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _SECRET_ENV}
    env.update(
        {
            "EMBEDDING_PROVIDER": "deterministic",
            "RAG_BACKEND": "local",
            "OTEL_SDK_DISABLED": "true",
        }
    )
    return env


def execute(scenario_ids: Sequence[str], config: AgentConfig) -> list[dict[str, Any]]:
    payload = {
        "scenario_ids": list(scenario_ids),
        "config": config.model_dump(mode="json"),
        "dataset": str(dataset_dir()),
    }
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and module, no shell
            [sys.executable, "-m", "evaluation.simulate"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=project_root(),
            env=_child_env(),
            timeout=SIMULATION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SimulationUnavailableError("The simulation took too long and was stopped.") from error
    if completed.returncode != 0:
        raise SimulationUnavailableError("The simulation worker failed. See the server log.")
    results: list[dict[str, Any]] = json.loads(completed.stdout)["results"]
    return results


def _view(row: SimulationRun, *, with_results: bool) -> SimulationRunView:
    meta = row.results if isinstance(row.results, dict) else {"items": row.results}
    return SimulationRunView(
        id=row.id,
        label=row.label,
        against=str(meta.get("against", "published")),
        config_version=meta.get("config_version"),
        started_by=row.started_by,
        started_at=row.started_at,
        finished_at=row.finished_at,
        scenario_count=row.scenario_count,
        passed_count=row.passed_count,
        results=[SimulationResultView.model_validate(item) for item in meta.get("items", [])]
        if with_results
        else None,
    )


def run_simulation(
    session: Session,
    tenant_id: str,
    *,
    scenario_ids: Sequence[str],
    against: str,
    config: AgentConfig,
    config_version: int | None,
    label: str,
    actor_id: str,
) -> SimulationRunView:
    known = {scenario.id for scenario in list_scenarios()}
    unknown = [item for item in scenario_ids if item not in known]
    if unknown:
        raise LookupError(f"Unknown scenarios: {', '.join(unknown[:5])}")
    started = utc_now_naive()
    items = execute(scenario_ids, config)
    row = SimulationRun(
        tenant_id=tenant_id,
        label=label or f"{len(items)} scenario{'s' if len(items) != 1 else ''}",
        started_by=actor_id,
        started_at=started,
        finished_at=utc_now_naive(),
        scenario_count=len(items),
        passed_count=sum(1 for item in items if item["passed"]),
        results={"against": against, "config_version": config_version, "items": items},
    )
    session.add(row)
    session.commit()
    return _view(row, with_results=True)


def list_runs(session: Session, tenant_id: str, limit: int = 30) -> list[SimulationRunView]:
    rows = session.scalars(
        select(SimulationRun)
        .where(SimulationRun.tenant_id == tenant_id)
        .order_by(SimulationRun.id.desc())
        .limit(limit)
    )
    return [_view(row, with_results=False) for row in rows]


def get_run(session: Session, tenant_id: str, run_id: int) -> SimulationRunView | None:
    row = session.get(SimulationRun, run_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return _view(row, with_results=True)
