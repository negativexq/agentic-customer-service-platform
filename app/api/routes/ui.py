from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_support_operator, resolve_customer_scope
from app.auth.models import Principal
from app.auth.scope import operator_customer_filter, operator_may_access
from app.core.config import LLMProvider, get_settings
from app.core.database import get_db
from app.health import RuntimeHealthService
from app.memory.models import MemoryRecord
from app.memory.service import MemoryService
from app.models.entities import Conversation, MessageSender
from app.policies.repository import SqlAlchemyPolicyAuditRepository
from app.services.conversation_messages import list_messages
from app.ui.demo import demo_scenarios
from app.ui.repository import AgentRunProjectionRepository, build_agent_run_projection_repository
from app.ui.schemas import (
    AgentRunView,
    ConversationView,
    DemoScenarioView,
    MemoryView,
    RuntimeConfigView,
    SystemComponentHealth,
    SystemHealthView,
    UIMemoryItem,
    UIMemoryUsage,
    UIMessageView,
    UIPolicyEvent,
    UIRagDocument,
    UIToolEvent,
    UITraceEvent,
)

router = APIRouter(
    prefix="/ui",
    tags=["operator-console"],
    dependencies=[Depends(require_support_operator)],
)


@router.get("/demo-scenarios", response_model=list[DemoScenarioView])
def demo_scenario_projections() -> list[DemoScenarioView]:
    """Return read-only, explicitly recorded showcase projections.

    These fixtures never enter the agent runtime, policy store, or execution
    path. They exist so the operator console can demonstrate bounded evidence
    when a local database has no corresponding run yet.
    """

    return demo_scenarios()


_MESSAGE_ROLES = {
    MessageSender.CUSTOMER: "user",
    MessageSender.AI: "assistant",
    MessageSender.OPERATOR: "operator",
    MessageSender.SYSTEM: "system",
}


def _customer_filter(principal: Principal) -> list[int] | None:
    return operator_customer_filter(principal)


def _run_or_404(
    run_id: str, repository: AgentRunProjectionRepository, principal: Principal
) -> AgentRunView:
    run = repository.get_by_run_id(run_id, tenant_id=principal.tenant_id or "default")
    if run is None or not operator_may_access(principal, run.customer_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return _bounded_operator_view(run)


@router.get("/agent-runs", response_model=list[AgentRunView])
def agent_runs(
    customer_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[AgentRunView]:
    repository = build_agent_run_projection_repository(get_settings(), session)
    if customer_id is not None:
        resolve_customer_scope(principal, customer_id)
        runs = repository.list_for_customer(
            customer_id, tenant_id=principal.tenant_id or "default", limit=limit
        )
    else:
        runs = repository.list_recent(
            tenant_id=principal.tenant_id or "default",
            limit=limit,
            customer_ids=_customer_filter(principal),
        )
    return [_bounded_operator_view(run) for run in runs]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationView,
    response_model_exclude_none=True,
)
def conversation(
    conversation_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> ConversationView:
    repository = build_agent_run_projection_repository(get_settings(), session)
    runs = repository.list_for_conversation(
        conversation_id,
        tenant_id=principal.tenant_id or "default",
        limit=get_settings().agent_run_projection_query_limit,
        customer_ids=_customer_filter(principal),
    )
    if not runs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    runs = [_bounded_operator_view(run) for run in runs]
    conversation_row = session.scalar(
        select(Conversation).where(
            Conversation.tenant_id == (principal.tenant_id or "default"),
            Conversation.customer_id == runs[-1].customer_id,
            Conversation.conversation_id == conversation_id,
        )
    )
    recorded = list_messages(session, conversation_row) if conversation_row is not None else []
    messages = [
        UIMessageView(
            role=_MESSAGE_ROLES[MessageSender(message.sender_type)],
            content_available=True,
            content=message.content,
        )
        for message in recorded
    ] or [
        UIMessageView(role=role, content_available=False)
        for _ in runs
        for role in ("user", "assistant")
    ]
    return ConversationView(
        conversation_id=conversation_id,
        customer_id=runs[-1].customer_id,
        run_count=len(runs),
        runs=runs,
        messages=messages,
    )


@router.get("/agent-runs/{agent_run_id}", response_model=AgentRunView)
def agent_run(
    agent_run_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> AgentRunView:
    return _run_or_404(
        agent_run_id,
        build_agent_run_projection_repository(get_settings(), session),
        principal,
    )


@router.get("/tool-events/{agent_run_id}", response_model=list[UIToolEvent])
def tool_events(
    agent_run_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[UIToolEvent]:
    return _run_or_404(
        agent_run_id,
        build_agent_run_projection_repository(get_settings(), session),
        principal,
    ).tools


@router.get("/policy-events/{agent_run_id}", response_model=list[UIPolicyEvent])
def policy_events(
    agent_run_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[UIPolicyEvent]:
    events = SqlAlchemyPolicyAuditRepository(session).list_for_agent_run(
        agent_run_id,
        tenant_id=principal.tenant_id or "default",
        limit=get_settings().policy_audit_query_limit,
        customer_ids=_customer_filter(principal),
    )
    if not events:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy audit not found")
    return [_policy_event_view(event) for event in events]


@router.get("/policy-audit/{conversation_id}", response_model=list[UIPolicyEvent])
def policy_audit(
    conversation_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[UIPolicyEvent]:
    events = SqlAlchemyPolicyAuditRepository(session).list_for_conversation(
        conversation_id,
        tenant_id=principal.tenant_id or "default",
        limit=get_settings().policy_audit_query_limit,
        customer_ids=_customer_filter(principal),
    )
    if not events:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy audit not found")
    return [_policy_event_view(event) for event in events]


def _policy_event_view(event: object) -> UIPolicyEvent:
    from app.policies.models import PolicyAuditEvent

    if not isinstance(event, PolicyAuditEvent):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid audit event"
        )
    return UIPolicyEvent(
        event_id=event.event_id,
        request_id=event.request_id,
        conversation_id=event.conversation_id,
        action_id=event.action_id,
        timestamp=event.timestamp,
        stage=event.stage,
        confirmation_status=event.confirmation_status,
        revalidation=event.revalidation,
        execution_status=event.execution_status,
        actor_id=event.actor_id,
        actor_type=event.actor_type.value,
        roles=list(event.roles),
        effective_customer_id=event.effective_customer_id,
        tool_name=event.tool_name,
        risk_level=event.risk_level,
        outcome=event.policy_outcome.value,
        reason_codes=event.reason_codes[:10],
    )


def _bounded_operator_view(view: AgentRunView) -> AgentRunView:
    """Remove forensic trace payloads before data reaches the operator UI."""

    sensitive_fragments = (
        "policy_input",
        "action_argument",
        "tool_argument",
        "prompt",
        "raw",
        "hash",
    )
    trace = [
        event.model_copy(
            update={
                "metadata": {
                    key: value
                    for key, value in event.metadata.items()
                    if not any(fragment in key.casefold() for fragment in sensitive_fragments)
                }
            }
        )
        for event in view.trace
    ]
    return view.model_copy(update={"trace": trace})


@router.get("/rag-events/{agent_run_id}", response_model=list[UIRagDocument])
def rag_events(
    agent_run_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[UIRagDocument]:
    return _run_or_404(
        agent_run_id,
        build_agent_run_projection_repository(get_settings(), session),
        principal,
    ).rag_documents


@router.get("/traces/{agent_run_id}", response_model=list[UITraceEvent])
def traces(
    agent_run_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[UITraceEvent]:
    return _run_or_404(
        agent_run_id,
        build_agent_run_projection_repository(get_settings(), session),
        principal,
    ).trace


@router.get("/agent-runs/{agent_run_id}/memory", response_model=UIMemoryUsage)
def agent_run_memory(
    agent_run_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> UIMemoryUsage:
    """Resolve the run's memory references against the live, governed memory store.

    Deleted, expired, or restricted memories return no content, so retention and deletion
    apply to the debug view as well.
    """

    run = _run_or_404(
        agent_run_id, build_agent_run_projection_repository(get_settings(), session), principal
    )
    usage = run.memory
    items = [*usage.read_items, *([usage.written_item] if usage.written_item else [])]
    ids = [item.memory_id for item in items if item.memory_id is not None]
    records = (
        {
            record.id: record
            for record in session.scalars(
                select(MemoryRecord).where(
                    MemoryRecord.id.in_(ids),
                    MemoryRecord.tenant_id == (principal.tenant_id or "default"),
                    MemoryRecord.customer_id == run.customer_id,
                )
            )
        }
        if ids
        else {}
    )

    def resolve(item: UIMemoryItem) -> UIMemoryItem:
        record = records.get(item.memory_id) if item.memory_id is not None else None
        if record is None:
            return item.model_copy(update={"content": None, "status": "unavailable"})
        status_value = str(getattr(record.status, "value", record.status))
        visible = (
            status_value == "active"
            and str(getattr(record.sensitivity_level, "value", record.sensitivity_level))
            != "restricted"
        )
        return item.model_copy(
            update={"content": record.content if visible else None, "status": status_value}
        )

    return usage.model_copy(
        update={
            "read_items": [resolve(item) for item in usage.read_items],
            "written_item": resolve(usage.written_item) if usage.written_item else None,
        }
    )


@router.get("/memory/{customer_id}", response_model=list[MemoryView])
def memory(
    customer_id: int,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[MemoryView]:
    if customer_id <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid customer ID")
    resolve_customer_scope(principal, customer_id)
    settings = get_settings()
    service = MemoryService(
        enabled=settings.memory_enabled,
        max_context_items=20,
        default_ttl_days=settings.memory_default_ttl_days,
        support_context_ttl_days=settings.memory_support_context_ttl_days,
    )
    records = service.retrieve(
        session,
        customer_id,
        "",
        principal=principal,
    )
    return [
        MemoryView(
            id=record.id,
            customer_id=record.customer_id,
            memory_type=str(getattr(record.memory_type, "value", record.memory_type)),
            normalized_key=record.normalized_key,
            source=str(getattr(record.source, "value", record.source)),
            status=str(getattr(record.status, "value", record.status)),
            created_at=record.created_at,
            updated_at=record.updated_at,
            expires_at=record.expires_at,
        )
        for record in records
    ]


@router.get("/system-health", response_model=SystemHealthView)
def system_health(request: Request, session: Session = Depends(get_db)) -> SystemHealthView:
    settings = get_settings()
    snapshot = RuntimeHealthService(settings).snapshot(
        session=session,
        checkpoint_provider=request.app.state.checkpoint_provider,
        runtime=request.app.state.agent_runtime,
        accepting_requests=getattr(request.app.state, "accepting_requests", False),
    )
    return SystemHealthView(
        status="ready" if snapshot.overall_ready else "not_ready",
        components=[
            SystemComponentHealth(
                name=component.name,
                status=component.status.value,
                detail=component.detail,
            )
            for component in snapshot.components
        ],
    )


@router.get("/runtime-config", response_model=RuntimeConfigView)
def runtime_config() -> RuntimeConfigView:
    settings = get_settings()
    live_available = bool(
        settings.llm_provider == LLMProvider.OPENAI_COMPATIBLE
        and settings.llm_api_key
        and settings.llm_base_url.rstrip("/").casefold() == "https://api.openai.com/v1"
    )
    provider = "OpenAI" if live_available else "Configured provider"
    return RuntimeConfigView(
        provider=provider,
        model=settings.llm_model,
        environment=settings.app_env,
        live_proposal_available=live_available,
    )
