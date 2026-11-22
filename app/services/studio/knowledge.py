"""Knowledge base: built-in markdown plus console articles, usage from runs, preview and publish.

Publishing builds an immutable Qdrant snapshot and moves the live alias to it, the same path as
``scripts/rag_ingest.py``. It is only offered with the deterministic embedding provider so the
console never triggers a paid embedding call.
"""

import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utc_now_naive
from app.models.entities import KnowledgeArticle
from app.rag.embeddings import build_embedding_provider
from app.rag.ingestion.chunking import chunk_document
from app.rag.ingestion.loader import load_markdown_documents
from app.rag.retrieval.service import KnowledgeService, build_knowledge_service
from app.rag.schemas import DocumentChunk, KnowledgeDocument
from app.rag.storage.qdrant import QdrantKnowledgeStore, _canonical_chunks, _corpus_hash
from app.ui.schemas import AgentRunView
from app.ui.studio_schemas import (
    KnowledgeChunkView,
    KnowledgeDocumentDetail,
    KnowledgeDocumentView,
    KnowledgeIndexStatus,
    KnowledgePublishResult,
    KnowledgeSearchHit,
    KnowledgeSearchResult,
    KnowledgeSectionView,
)

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"


class KnowledgePublishBlockedError(Exception):
    pass


def built_in_documents() -> list[KnowledgeDocument]:
    return load_markdown_documents(KNOWLEDGE_DIR)


def _articles(session: Session, tenant_id: str) -> dict[str, KnowledgeArticle]:
    rows = session.scalars(select(KnowledgeArticle).where(KnowledgeArticle.tenant_id == tenant_id))
    return {row.document_id: row for row in rows}


def _with_title(content: str, title: str) -> str:
    body = "\n".join(line for line in content.splitlines() if not line.startswith("# "))
    return f"# {title}\n\n{body.strip()}\n"


def _article_document(row: KnowledgeArticle, base: KnowledgeDocument | None) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=row.document_id,
        title=row.title,
        category=base.category if base else row.document_id.replace("-", "_"),
        source=base.source if base else f"console:{row.document_id}",
        content=_with_title(row.content, row.title),
    )


def corpus(session: Session | None, tenant_id: str = "default") -> list[KnowledgeDocument]:
    """The documents the agent should know: built-ins with console edits applied."""

    documents = {document.document_id: document for document in built_in_documents()}
    if session is not None:
        for document_id, row in _articles(session, tenant_id).items():
            if row.archived:
                documents.pop(document_id, None)
            else:
                documents[document_id] = _article_document(row, documents.get(document_id))
    return sorted(documents.values(), key=lambda document: document.document_id)


def corpus_chunks(
    documents: Sequence[KnowledgeDocument], settings: Settings
) -> list[DocumentChunk]:
    return [
        chunk
        for document in documents
        for chunk in chunk_document(document, settings.rag_chunk_size)
    ]


def _usage(runs: Sequence[AgentRunView]) -> tuple[Counter[str], Counter[str], dict[str, datetime]]:
    by_document: Counter[str] = Counter()
    by_chunk: Counter[str] = Counter()
    last_used: dict[str, datetime] = {}
    for run in runs:
        seen: set[str] = set()
        for document in run.rag_documents:
            chunk_id = document.chunk_id or document.citation_id
            document_id = chunk_id.split("#", 1)[0]
            by_chunk[chunk_id] += 1
            if document_id not in seen:
                seen.add(document_id)
                by_document[document_id] += 1
                if document_id not in last_used or run.started_at > last_used[document_id]:
                    last_used[document_id] = run.started_at
    return by_document, by_chunk, last_used


def list_documents(
    session: Session, tenant_id: str, runs: Sequence[AgentRunView], settings: Settings
) -> list[KnowledgeDocumentView]:
    by_document, _, last_used = _usage(runs)
    built_ins = {document.document_id: document for document in built_in_documents()}
    articles = _articles(session, tenant_id)
    views: list[KnowledgeDocumentView] = []
    for document_id in sorted(set(built_ins) | set(articles)):
        base = built_ins.get(document_id)
        row = articles.get(document_id)
        document = _article_document(row, base) if row is not None else base
        assert document is not None
        chunks = chunk_document(document, settings.rag_chunk_size)
        file_time = (
            datetime.fromtimestamp(Path(base.source).stat().st_mtime)
            if base is not None and Path(base.source).exists()
            else None
        )
        views.append(
            KnowledgeDocumentView(
                document_id=document_id,
                title=document.title,
                category=document.category,
                origin="built_in" if base is not None else "console",
                edited_in_console=row is not None and base is not None,
                archived=bool(row and row.archived),
                section_count=len({chunk.section for chunk in chunks}),
                chunk_count=len(chunks),
                word_count=len(document.content.split()),
                updated_at=row.updated_at if row is not None else file_time,
                updated_by=row.updated_by if row is not None else None,
                runs_citing=by_document.get(document_id, 0),
                last_cited_at=last_used.get(document_id),
            )
        )
    return sorted(views, key=lambda view: (view.archived, -view.runs_citing, view.title))


def get_document(
    session: Session,
    tenant_id: str,
    document_id: str,
    runs: Sequence[AgentRunView],
    settings: Settings,
) -> KnowledgeDocumentDetail | None:
    base = next((item for item in built_in_documents() if item.document_id == document_id), None)
    row = _articles(session, tenant_id).get(document_id)
    if base is None and row is None:
        return None
    document = _article_document(row, base) if row is not None else base
    assert document is not None
    _, by_chunk, _ = _usage(runs)
    sections: dict[str, KnowledgeSectionView] = {}
    for chunk in chunk_document(document, settings.rag_chunk_size):
        section = sections.setdefault(
            chunk.section, KnowledgeSectionView(section=chunk.section, chunks=[])
        )
        section.chunks.append(
            KnowledgeChunkView(
                chunk_id=chunk.chunk_id,
                content=chunk.content,
                runs_citing=by_chunk.get(chunk.chunk_id, 0),
            )
        )
    body = "\n".join(line for line in document.content.splitlines() if not line.startswith("# "))
    return KnowledgeDocumentDetail(
        document_id=document.document_id,
        title=document.title,
        category=document.category,
        origin="built_in" if base is not None else "console",
        archived=bool(row and row.archived),
        content=body.strip(),
        sections=list(sections.values()),
    )


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:80] or "article"


def create_article(session: Session, tenant_id: str, actor: str, title: str, content: str) -> str:
    existing = {document.document_id for document in built_in_documents()} | set(
        _articles(session, tenant_id)
    )
    base = slugify(title)
    document_id, suffix = base, 2
    while document_id in existing:
        document_id, suffix = f"{base}-{suffix}", suffix + 1
    session.add(
        KnowledgeArticle(
            tenant_id=tenant_id,
            document_id=document_id,
            title=title.strip(),
            content=content.strip(),
            archived=False,
            updated_by=actor,
            updated_at=utc_now_naive(),
        )
    )
    session.commit()
    return document_id


def save_article(
    session: Session,
    tenant_id: str,
    actor: str,
    document_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    archived: bool | None = None,
) -> bool:
    base = next((item for item in built_in_documents() if item.document_id == document_id), None)
    row = _articles(session, tenant_id).get(document_id)
    if row is None:
        if base is None:
            return False
        body = "\n".join(line for line in base.content.splitlines() if not line.startswith("# "))
        row = KnowledgeArticle(
            tenant_id=tenant_id,
            document_id=document_id,
            title=base.title,
            content=body.strip(),
            archived=False,
            updated_by=actor,
        )
        session.add(row)
    if title is not None:
        row.title = title.strip()
    if content is not None:
        row.content = content.strip()
    if archived is not None:
        row.archived = archived
    row.updated_by = actor
    row.updated_at = utc_now_naive()
    session.commit()
    return True


def revert_article(session: Session, tenant_id: str, document_id: str) -> bool:
    """Drop console edits of a built-in document (console-only articles cannot be reverted)."""

    if document_id not in {document.document_id for document in built_in_documents()}:
        return False
    row = _articles(session, tenant_id).get(document_id)
    if row is not None:
        session.delete(row)
        session.commit()
    return True


@lru_cache(maxsize=8)
def _preview_service(corpus_key: str) -> KnowledgeService:
    del corpus_key
    # Deterministic embeddings over an in-process index: no provider call and no cost.
    return build_knowledge_service(
        Settings(rag_backend="local", embedding_provider="deterministic")
    )


def search(
    session: Session, tenant_id: str, query: str, settings: Settings
) -> KnowledgeSearchResult:
    """Preview what retrieval returns for a question, over the current (unpublished) corpus."""

    documents = corpus(session, tenant_id)
    chunks = corpus_chunks(documents, settings)
    key = _corpus_hash(_canonical_chunks(chunks))
    service = _preview_service(key)
    if getattr(service, "_studio_corpus", None) != key:
        service.reset()
        service.ingest_documents(documents, chunk_size=settings.rag_chunk_size)
        service._studio_corpus = key  # type: ignore[attr-defined]
    result = service.retrieve(query)
    return KnowledgeSearchResult(
        query=query,
        index="preview",
        hits=[
            KnowledgeSearchHit(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                title=chunk.title,
                section=chunk.section,
                score=round(
                    float(chunk.rerank_score if chunk.rerank_score is not None else chunk.score), 4
                ),
                content=chunk.content,
            )
            for chunk in result.chunks
        ],
    )


def _publish_blocked_reason(settings: Settings) -> str | None:
    if settings.embedding_provider != "deterministic":
        return (
            "Publishing would call the paid embedding provider. Run scripts/rag_ingest.py "
            "deliberately instead."
        )
    return None


def _store(settings: Settings) -> QdrantKnowledgeStore:
    return QdrantKnowledgeStore(
        settings.qdrant_url,
        settings.qdrant_collection,
        build_embedding_provider(settings),
        timeout_seconds=settings.qdrant_timeout_seconds,
        embedding_model=settings.embedding_model,
        schema_version=settings.qdrant_schema_version,
        chunking_version=settings.qdrant_chunking_version,
    )


def index_status(session: Session, tenant_id: str, settings: Settings) -> KnowledgeIndexStatus:
    chunks = corpus_chunks(corpus(session, tenant_id), settings)
    blocked = _publish_blocked_reason(settings)
    active_snapshot: str | None = None
    active_chunks: int | None = None
    pending = True
    if settings.rag_backend == "qdrant":
        try:
            store = _store(settings)
            try:
                active = next((item for item in store.list_snapshots() if item.get("active")), None)
            finally:
                store.close()
        except Exception:  # noqa: BLE001 - status must render even when Qdrant is down
            active = None
        if active is not None:
            active_snapshot = str(active.get("snapshot_id") or active.get("collection_name"))
            count = active.get("expected_chunk_count") or active.get("points_count")
            active_chunks = int(count) if isinstance(count, int | str) else None
            pending = active.get("corpus_hash") != _corpus_hash(_canonical_chunks(chunks))
    else:
        blocked = blocked or "The local index is rebuilt when the application starts."
    return KnowledgeIndexStatus(
        backend=settings.rag_backend,
        embedding_provider=settings.embedding_provider,
        publish_allowed=blocked is None,
        publish_blocked_reason=blocked,
        pending_changes=pending,
        active_snapshot=active_snapshot,
        active_chunk_count=active_chunks,
        corpus_chunk_count=len(chunks),
    )


def publish(session: Session, tenant_id: str, settings: Settings) -> KnowledgePublishResult:
    blocked = _publish_blocked_reason(settings)
    if blocked is not None or settings.rag_backend != "qdrant":
        raise KnowledgePublishBlockedError(blocked or "Publishing needs the Qdrant backend.")
    chunks = corpus_chunks(corpus(session, tenant_id), settings)
    store = _store(settings)
    try:
        snapshot = store.build_snapshot(chunks, activate=True)
        action = store.last_build_action
    finally:
        store.close()
    return KnowledgePublishResult(
        snapshot=snapshot.snapshot_id, chunk_count=snapshot.chunk_count, action=action
    )
