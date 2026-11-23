"""Draft, publish, restore and read versioned agent configuration."""

import logging
import time

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent_config.schemas import AgentConfig
from app.core.time import utc_now_naive
from app.models.entities import AgentConfigVersion

logger = logging.getLogger(__name__)

DRAFT = "draft"
PUBLISHED = "published"
ARCHIVED = "archived"

_CACHE_SECONDS = 3.0
_cache: dict[str, tuple[float, AgentConfig]] = {}


class NoDraftError(Exception):
    pass


def _latest(session: Session, tenant_id: str, status: str) -> AgentConfigVersion | None:
    return session.scalar(
        select(AgentConfigVersion)
        .where(AgentConfigVersion.tenant_id == tenant_id, AgentConfigVersion.status == status)
        .order_by(AgentConfigVersion.version.desc())
        .limit(1)
    )


def published_version(session: Session, tenant_id: str) -> AgentConfigVersion | None:
    return _latest(session, tenant_id, PUBLISHED)


def draft_version(session: Session, tenant_id: str) -> AgentConfigVersion | None:
    return _latest(session, tenant_id, DRAFT)


def load_published_config(session: Session, tenant_id: str) -> AgentConfig:
    """The configuration the runtime uses. Falls back to defaults if it cannot be read."""

    cached = _cache.get(tenant_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    try:
        row = published_version(session, tenant_id)
        config = AgentConfig.model_validate(row.config) if row is not None else AgentConfig()
    except (SQLAlchemyError, ValueError) as error:
        logger.warning("agent config unavailable; using defaults (%s)", type(error).__name__)
        session.rollback()
        config = AgentConfig()
    _cache[tenant_id] = (now, config)
    return config


def invalidate_cache(tenant_id: str | None = None) -> None:
    if tenant_id is None:
        _cache.clear()
    else:
        _cache.pop(tenant_id, None)


def _next_version(session: Session, tenant_id: str) -> int:
    current = session.scalar(
        select(func.max(AgentConfigVersion.version)).where(
            AgentConfigVersion.tenant_id == tenant_id
        )
    )
    return int(current or 0) + 1


def save_draft(
    session: Session, tenant_id: str, actor: str, config: AgentConfig, note: str = ""
) -> AgentConfigVersion:
    draft = draft_version(session, tenant_id)
    now = utc_now_naive()
    if draft is None:
        draft = AgentConfigVersion(
            tenant_id=tenant_id,
            version=_next_version(session, tenant_id),
            status=DRAFT,
            config=config.model_dump(mode="json"),
            note=note,
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        session.add(draft)
    else:
        draft.config = config.model_dump(mode="json")
        draft.note = note or draft.note
        draft.updated_at = now
    session.commit()
    return draft


def publish_draft(
    session: Session, tenant_id: str, actor: str, note: str = ""
) -> AgentConfigVersion:
    draft = draft_version(session, tenant_id)
    if draft is None:
        raise NoDraftError
    previous = published_version(session, tenant_id)
    if previous is not None:
        previous.status = ARCHIVED
    draft.status = PUBLISHED
    draft.published_by = actor
    draft.published_at = utc_now_naive()
    if note:
        draft.note = note
    session.commit()
    invalidate_cache(tenant_id)
    return draft


def discard_draft(session: Session, tenant_id: str) -> None:
    draft = draft_version(session, tenant_id)
    if draft is not None:
        session.delete(draft)
        session.commit()


def restore_version(
    session: Session, tenant_id: str, actor: str, version: int
) -> AgentConfigVersion:
    source = session.scalar(
        select(AgentConfigVersion).where(
            AgentConfigVersion.tenant_id == tenant_id, AgentConfigVersion.version == version
        )
    )
    if source is None:
        raise LookupError(version)
    config = AgentConfig.model_validate(source.config)
    return save_draft(session, tenant_id, actor, config, note=f"Restored from version {version}")


def list_versions(session: Session, tenant_id: str, limit: int = 50) -> list[AgentConfigVersion]:
    return list(
        session.scalars(
            select(AgentConfigVersion)
            .where(AgentConfigVersion.tenant_id == tenant_id)
            .order_by(AgentConfigVersion.version.desc())
            .limit(limit)
        )
    )
