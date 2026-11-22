import argparse
import sys
from pathlib import Path

import app as app_package
from app.core.config import get_settings
from app.rag.embeddings import build_embedding_provider
from app.rag.ingestion.chunking import chunk_document
from app.rag.ingestion.loader import load_markdown_documents
from app.rag.schemas import KnowledgeDocument
from app.rag.storage.qdrant import QdrantKnowledgeStore


def _documents(directory: Path) -> list[KnowledgeDocument]:
    """Built-in markdown plus articles edited in the console, when the database is reachable."""

    try:
        from app.core.database import SessionLocal
        from app.services.studio.knowledge import corpus

        with SessionLocal() as session:
            return corpus(session)
    except Exception as error:  # noqa: BLE001 - ingestion must work without a database
        print(f"Console articles unavailable ({type(error).__name__}); using built-in files.")
        return load_markdown_documents(directory)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build and manage immutable Qdrant knowledge snapshots."
    )
    parser.add_argument(
        "command", nargs="?", choices=("build", "list", "rollback"), default="build"
    )
    parser.add_argument("snapshot", nargs="?", help="Physical snapshot collection for rollback.")
    arguments = parser.parse_args()
    settings = get_settings()
    package_file = app_package.__file__
    if package_file is None:
        raise RuntimeError("Application package location is unavailable.")
    documents = _documents(Path(package_file).parent / "knowledge")
    chunks = [
        chunk
        for document in documents
        for chunk in chunk_document(document, settings.rag_chunk_size)
    ]
    store = QdrantKnowledgeStore(
        settings.qdrant_url,
        settings.qdrant_collection,
        build_embedding_provider(settings),
        timeout_seconds=settings.qdrant_timeout_seconds,
        embedding_model=settings.embedding_model,
        schema_version=settings.qdrant_schema_version,
        chunking_version=settings.qdrant_chunking_version,
    )
    try:
        if arguments.command == "list":
            for snapshot_record in store.list_snapshots():
                print(
                    f"{snapshot_record.get('collection_name')} "
                    f"snapshot={snapshot_record.get('snapshot_id')} "
                    f"active={snapshot_record.get('active')} "
                    f"state={snapshot_record.get('build_state')} "
                    f"points={snapshot_record.get('points_count')} "
                    f"expected={snapshot_record.get('expected_chunk_count')} "
                    f"corpus={snapshot_record.get('corpus_hash')} "
                    f"embedding={snapshot_record.get('embedding_provider')}/"
                    f"{snapshot_record.get('embedding_model')} "
                    f"schema={snapshot_record.get('schema_version')} "
                    f"chunking={snapshot_record.get('chunking_version')} "
                    f"lexical={snapshot_record.get('lexical_index_version')}"
                )
            return
        if arguments.command == "rollback":
            if not arguments.snapshot:
                raise SystemExit("rollback requires a physical snapshot collection name")
            store.rollback(arguments.snapshot)
            print(
                f"Activated rollback snapshot {arguments.snapshot} for "
                f"{settings.qdrant_collection}."
            )
            return
        try:
            snapshot = store.build_snapshot(chunks, activate=True)
        except Exception as error:
            print(
                "Snapshot build/activation failed; the previous active alias was preserved "
                "when activation could not complete.",
                file=sys.stderr,
            )
            raise SystemExit(1) from error
        count = snapshot.chunk_count
    finally:
        store.close()
    print(
        f"Snapshot {snapshot.snapshot_id} {store.last_build_action} with {count} chunks "
        "and activated "
        f"{settings.qdrant_collection} using "
        f"{settings.embedding_provider}/{settings.embedding_model}; "
        f"corpus={snapshot.corpus_hash}; physical={snapshot.collection_name}."
    )


if __name__ == "__main__":
    main()
