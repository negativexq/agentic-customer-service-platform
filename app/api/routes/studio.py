"""Agent studio API: configure (knowledge, guidance, procedures, actions) and publish."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agent_config import service as config_service
from app.agent_config.schemas import AgentConfig
from app.auth.dependencies import require_studio_editor, require_support_operator
from app.auth.models import Principal
from app.auth.scope import operator_customer_filter
from app.core.config import get_settings
from app.core.database import get_db
from app.core.time import utc_now_naive
from app.services.studio import knowledge
from app.services.studio.actions import list_actions
from app.services.studio.procedures import list_procedures
from app.ui.repository import build_agent_run_projection_repository
from app.ui.schemas import AgentRunView
from app.ui.studio_schemas import (
    ActionView,
    AgentConfigState,
    ConfigVersionView,
    DraftInput,
    KnowledgeArticleInput,
    KnowledgeDocumentDetail,
    KnowledgeDocumentView,
    KnowledgeIndexStatus,
    KnowledgePublishResult,
    KnowledgeSearchInput,
    KnowledgeSearchResult,
    ProcedureView,
    PublishInput,
)

router = APIRouter(prefix="/ui", tags=["agent-studio"])

USAGE_WINDOW_DAYS = 30


def _tenant(principal: Principal) -> str:
    return principal.tenant_id or "default"


def recent_runs(
    session: Session, principal: Principal, days: int = USAGE_WINDOW_DAYS
) -> list[AgentRunView]:
    repository = build_agent_run_projection_repository(get_settings(), session)
    return repository.list_window(
        tenant_id=_tenant(principal),
        since=utc_now_naive() - timedelta(days=days),
        customer_ids=operator_customer_filter(principal),
    )


def _state(session: Session, tenant_id: str) -> AgentConfigState:
    published = config_service.published_version(session, tenant_id)
    draft = config_service.draft_version(session, tenant_id)
    return AgentConfigState(
        published=AgentConfig.model_validate(published.config) if published else AgentConfig(),
        published_version=published.version if published else None,
        draft=AgentConfig.model_validate(draft.config) if draft else None,
        draft_version=draft.version if draft else None,
        versions=[
            ConfigVersionView(
                version=row.version,
                status=row.status,
                note=row.note,
                created_by=row.created_by,
                created_at=row.created_at,
                updated_at=row.updated_at,
                published_by=row.published_by,
                published_at=row.published_at,
            )
            for row in config_service.list_versions(session, tenant_id)
        ],
    )


@router.get("/agent-config", response_model=AgentConfigState)
def agent_config(
    principal: Principal = Depends(require_support_operator), session: Session = Depends(get_db)
) -> AgentConfigState:
    return _state(session, _tenant(principal))


@router.put("/agent-config/draft", response_model=AgentConfigState)
def save_draft(
    body: DraftInput,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> AgentConfigState:
    config_service.save_draft(
        session, _tenant(principal), principal.actor_id, body.config, body.note
    )
    return _state(session, _tenant(principal))


@router.delete("/agent-config/draft", response_model=AgentConfigState)
def discard_draft(
    principal: Principal = Depends(require_studio_editor), session: Session = Depends(get_db)
) -> AgentConfigState:
    config_service.discard_draft(session, _tenant(principal))
    return _state(session, _tenant(principal))


@router.post("/agent-config/publish", response_model=AgentConfigState)
def publish_draft(
    body: PublishInput,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> AgentConfigState:
    try:
        config_service.publish_draft(session, _tenant(principal), principal.actor_id, body.note)
    except config_service.NoDraftError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="There is no draft to publish."
        ) from error
    return _state(session, _tenant(principal))


@router.post("/agent-config/versions/{version}/restore", response_model=AgentConfigState)
def restore_version(
    version: int,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> AgentConfigState:
    try:
        config_service.restore_version(session, _tenant(principal), principal.actor_id, version)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Version not found.") from error
    return _state(session, _tenant(principal))


@router.get("/procedures", response_model=list[ProcedureView])
def procedures(
    principal: Principal = Depends(require_support_operator), session: Session = Depends(get_db)
) -> list[ProcedureView]:
    tenant_id = _tenant(principal)
    draft = config_service.draft_version(session, tenant_id)
    config = (
        AgentConfig.model_validate(draft.config)
        if draft
        else config_service.load_published_config(session, tenant_id)
    )
    return list_procedures(config, recent_runs(session, principal))


@router.get("/actions", response_model=list[ActionView])
def actions(
    principal: Principal = Depends(require_support_operator), session: Session = Depends(get_db)
) -> list[ActionView]:
    state = _state(session, _tenant(principal))
    return list_actions(state.published, state.draft, recent_runs(session, principal))


@router.get("/knowledge", response_model=list[KnowledgeDocumentView])
def knowledge_documents(
    principal: Principal = Depends(require_support_operator), session: Session = Depends(get_db)
) -> list[KnowledgeDocumentView]:
    return knowledge.list_documents(
        session, _tenant(principal), recent_runs(session, principal), get_settings()
    )


@router.get("/knowledge/status", response_model=KnowledgeIndexStatus)
def knowledge_status(
    principal: Principal = Depends(require_support_operator), session: Session = Depends(get_db)
) -> KnowledgeIndexStatus:
    return knowledge.index_status(session, _tenant(principal), get_settings())


@router.post("/knowledge/search", response_model=KnowledgeSearchResult)
def knowledge_search(
    body: KnowledgeSearchInput,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> KnowledgeSearchResult:
    return knowledge.search(session, _tenant(principal), body.query, get_settings())


@router.post("/knowledge/publish", response_model=KnowledgePublishResult)
def knowledge_publish(
    principal: Principal = Depends(require_studio_editor), session: Session = Depends(get_db)
) -> KnowledgePublishResult:
    try:
        return knowledge.publish(session, _tenant(principal), get_settings())
    except knowledge.KnowledgePublishBlockedError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/knowledge", response_model=KnowledgeDocumentDetail, status_code=201)
def create_article(
    body: KnowledgeArticleInput,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> KnowledgeDocumentDetail:
    document_id = knowledge.create_article(
        session, _tenant(principal), principal.actor_id, body.title, body.content
    )
    return _document_or_404(session, principal, document_id)


@router.get("/knowledge/{document_id}", response_model=KnowledgeDocumentDetail)
def knowledge_document(
    document_id: str,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> KnowledgeDocumentDetail:
    return _document_or_404(session, principal, document_id)


@router.put("/knowledge/{document_id}", response_model=KnowledgeDocumentDetail)
def update_article(
    document_id: str,
    body: KnowledgeArticleInput,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> KnowledgeDocumentDetail:
    if not knowledge.save_article(
        session,
        _tenant(principal),
        principal.actor_id,
        document_id,
        title=body.title,
        content=body.content,
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return _document_or_404(session, principal, document_id)


@router.post("/knowledge/{document_id}/archive", response_model=KnowledgeDocumentDetail)
def archive_article(
    document_id: str,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> KnowledgeDocumentDetail:
    return _set_archived(session, principal, document_id, True)


@router.post("/knowledge/{document_id}/unarchive", response_model=KnowledgeDocumentDetail)
def unarchive_article(
    document_id: str,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> KnowledgeDocumentDetail:
    return _set_archived(session, principal, document_id, False)


@router.post("/knowledge/{document_id}/revert", response_model=KnowledgeDocumentDetail)
def revert_article(
    document_id: str,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> KnowledgeDocumentDetail:
    if not knowledge.revert_article(session, _tenant(principal), document_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Only built-in documents can be reverted."
        )
    return _document_or_404(session, principal, document_id)


def _set_archived(
    session: Session, principal: Principal, document_id: str, archived: bool
) -> KnowledgeDocumentDetail:
    if not knowledge.save_article(
        session, _tenant(principal), principal.actor_id, document_id, archived=archived
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return _document_or_404(session, principal, document_id)


def _document_or_404(
    session: Session, principal: Principal, document_id: str
) -> KnowledgeDocumentDetail:
    detail = knowledge.get_document(
        session, _tenant(principal), document_id, recent_runs(session, principal), get_settings()
    )
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return detail
