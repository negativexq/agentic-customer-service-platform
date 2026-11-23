"""Improve loop API: performance, optimisation gaps, quality monitors, error logs, simulations."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.agent_config import service as config_service
from app.agent_config.schemas import AgentConfig
from app.api.routes.studio import recent_runs
from app.auth.dependencies import require_studio_editor, require_support_operator
from app.auth.models import Principal
from app.auth.scope import operator_customer_filter
from app.core.config import get_settings
from app.core.database import get_db
from app.core.time import utc_now_naive
from app.models.entities import Conversation, QualityMonitor
from app.services import audit, insights, quality, simulations
from app.ui.audit_schemas import AuditLogView
from app.ui.insights_schemas import (
    ErrorLogView,
    GapView,
    MonitorInput,
    MonitorPreview,
    MonitorPreviewInput,
    PerformanceView,
    QualityView,
    ReviewInput,
    ScenarioView,
    SimulationInput,
    SimulationRunView,
)
from app.ui.repository import build_agent_run_projection_repository

router = APIRouter(prefix="/ui", tags=["improve"])

Days = Query(default=30, ge=1, le=90)


def _tenant(principal: Principal) -> str:
    return principal.tenant_id or "default"


def _scope(principal: Principal) -> set[int] | None:
    allowed = operator_customer_filter(principal)
    return set(allowed) if allowed is not None else None


@router.get("/performance", response_model=PerformanceView)
def performance(
    days: int = Days,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> PerformanceView:
    return insights.performance(
        session,
        _tenant(principal),
        recent_runs(session, principal, days),
        days=days,
        customer_ids=_scope(principal),
    )


@router.get("/gaps", response_model=list[GapView])
def gaps(
    days: int = Days,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[GapView]:
    return insights.gaps(
        session,
        _tenant(principal),
        recent_runs(session, principal, days),
        since=utc_now_naive() - timedelta(days=days),
    )


@router.get("/errors", response_model=ErrorLogView)
def errors(
    days: int = Days,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> ErrorLogView:
    return insights.error_log(recent_runs(session, principal, days), days=days)


@router.get("/quality", response_model=QualityView)
def quality_view(
    days: int = Days,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> QualityView:
    return quality.quality(
        session,
        _tenant(principal),
        recent_runs(session, principal, days),
        since=utc_now_naive() - timedelta(days=days),
        days=days,
        customer_ids=_scope(principal),
    )


def _clean_phrases(phrases: list[str]) -> list[str]:
    cleaned = [phrase.strip()[:80] for phrase in phrases if phrase.strip()]
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Add at least one phrase."
        )
    return list(dict.fromkeys(cleaned))


@router.post("/quality/monitors/preview", response_model=MonitorPreview)
def preview_monitor(
    payload: MonitorPreviewInput,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> MonitorPreview:
    return quality.preview_monitor(
        session,
        _tenant(principal),
        phrases=_clean_phrases(payload.phrases),
        sender_type=payload.sender_type,
        since=utc_now_naive() - timedelta(days=payload.days),
        days=payload.days,
        customer_ids=_scope(principal),
    )


@router.post("/quality/monitors", status_code=status.HTTP_201_CREATED)
def create_monitor(
    payload: MonitorInput,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    monitor = QualityMonitor(
        tenant_id=_tenant(principal),
        name=payload.name.strip(),
        description=payload.description.strip(),
        sender_type=payload.sender_type,
        phrases=_clean_phrases(payload.phrases),
        severity=payload.severity,
        enabled=payload.enabled,
        created_by=principal.actor_id,
        created_at=utc_now_naive(),
    )
    session.add(monitor)
    session.commit()
    return {"key": f"custom:{monitor.id}"}


def _monitor_or_404(session: Session, principal: Principal, monitor_id: int) -> QualityMonitor:
    monitor = session.get(QualityMonitor, monitor_id)
    if monitor is None or monitor.tenant_id != _tenant(principal):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found")
    return monitor


@router.put("/quality/monitors/{monitor_id}")
def update_monitor(
    monitor_id: int,
    payload: MonitorInput,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    monitor = _monitor_or_404(session, principal, monitor_id)
    monitor.name = payload.name.strip()
    monitor.description = payload.description.strip()
    monitor.sender_type = payload.sender_type
    monitor.phrases = _clean_phrases(payload.phrases)
    monitor.severity = payload.severity
    monitor.enabled = payload.enabled
    session.commit()
    return {"key": f"custom:{monitor.id}"}


@router.delete("/quality/monitors/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_monitor(
    monitor_id: int,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> Response:
    session.delete(_monitor_or_404(session, principal, monitor_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/quality/reviews", status_code=status.HTTP_204_NO_CONTENT)
def review(
    payload: ReviewInput,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> Response:
    quality.review_flag(
        session,
        _tenant(principal),
        monitor_key=payload.monitor_key,
        conversation_pk=payload.conversation_pk,
        status=payload.status,
        note=payload.note.strip(),
        actor_id=principal.actor_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _unavailable(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))


@router.get("/simulations/scenarios", response_model=list[ScenarioView])
def scenarios(principal: Principal = Depends(require_support_operator)) -> list[ScenarioView]:
    del principal
    try:
        return simulations.list_scenarios()
    except simulations.SimulationUnavailableError as error:
        raise _unavailable(error) from error


@router.get("/simulations", response_model=list[SimulationRunView])
def simulation_runs(
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[SimulationRunView]:
    return simulations.list_runs(session, _tenant(principal))


@router.get("/simulations/{run_id}", response_model=SimulationRunView)
def simulation_run(
    run_id: int,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> SimulationRunView:
    view = simulations.get_run(session, _tenant(principal), run_id)
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    return view


@router.post("/simulations", response_model=SimulationRunView)
def run_simulation(
    payload: SimulationInput,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> SimulationRunView:
    tenant = _tenant(principal)
    if payload.against == "draft":
        row = config_service.draft_version(session, tenant)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="There is no draft to simulate."
            )
    else:
        row = config_service.published_version(session, tenant)
    config = AgentConfig.model_validate(row.config) if row else AgentConfig()
    try:
        return simulations.run_simulation(
            session,
            tenant,
            scenario_ids=list(dict.fromkeys(payload.scenario_ids)),
            against=payload.against,
            config=config,
            config_version=row.version if row else None,
            label=payload.label.strip(),
            actor_id=principal.actor_id,
        )
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except simulations.SimulationUnavailableError as error:
        raise _unavailable(error) from error


@router.get("/audit", response_model=AuditLogView)
def audit_log(
    days: int = Days,
    conversation: int | None = Query(default=None, ge=1),
    kind: list[str] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=500, ge=1, le=2000),
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> AuditLogView:
    """Who did what to which conversation, and when: handoffs, takeovers, closes, decisions."""

    tenant = _tenant(principal)
    if conversation is not None:
        record = session.get(Conversation, conversation)
        if record is None or record.tenant_id != tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
            )
        repository = build_agent_run_projection_repository(get_settings(), session)
        runs = repository.list_for_conversation(record.conversation_id, tenant_id=tenant)
    else:
        runs = recent_runs(session, principal, days)
    return audit.audit_log(
        session,
        tenant,
        runs,
        since=utc_now_naive() - timedelta(days=days),
        customer_ids=_scope(principal),
        operator_names={principal.actor_id: principal.display_name or "Operator"},
        conversation_pk=conversation,
        kinds=kind,
        search=q,
        limit=limit,
    )
