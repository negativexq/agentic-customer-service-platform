from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.rag.embeddings import EmbeddingProvider
from app.rag.retrieval.lexical import (
    LEXICAL_METADATA_KEY,
    LEXICAL_SCHEMA_VERSION,
    LEXICAL_VECTOR_NAME,
    LexicalIndex,
    build_lexical_index,
)
from app.rag.schemas import DocumentChunk

QDRANT_DENSE_DISTANCE = "Cosine"
SNAPSHOT_METADATA_KEY = "knowledge_snapshot"
SNAPSHOT_SPEC_VERSION = 1
SNAPSHOT_BUILD_STATE_BUILDING = "building"
SNAPSHOT_BUILD_STATE_FAILED = "failed"
SNAPSHOT_BUILD_STATE_COMPLETE = "complete"
KNOWLEDGE_SCHEMA_VERSION = 2
CHUNKING_VERSION = 1
SNAPSHOT_UPSERT_BATCH_SIZE = 128


@dataclass(frozen=True)
class KnowledgeSnapshot:
    snapshot_id: str
    snapshot_spec_hash: str
    collection_name: str
    corpus_hash: str
    chunk_count: int
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    schema_version: int
    chunking_version: int
    lexical_index_version: int
    lexical_index_hash: str
    created_at: str
    build_state: str


@dataclass(frozen=True)
class KnowledgeSnapshotSpec:
    """Canonical semantic identity of one immutable dense+sparse index artifact."""

    corpus_hash: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    schema_version: int
    chunking_version: int
    lexical_index_version: int
    dense_distance: str = QDRANT_DENSE_DISTANCE
    sparse_vector_name: str = LEXICAL_VECTOR_NAME
    spec_version: int = SNAPSHOT_SPEC_VERSION

    def canonical_payload(self) -> dict[str, object]:
        return {
            "chunking_version": self.chunking_version,
            "corpus_hash": self.corpus_hash,
            "dense_distance": self.dense_distance,
            "embedding_dimension": self.embedding_dimension,
            "embedding_model": self.embedding_model,
            "embedding_provider": self.embedding_provider,
            "knowledge_schema_version": self.schema_version,
            "lexical_index_version": self.lexical_index_version,
            "sparse_vector_name": self.sparse_vector_name,
            "snapshot_spec_version": self.spec_version,
        }


def compute_snapshot_spec_hash(spec: KnowledgeSnapshotSpec) -> str:
    """Hash stable semantic fields, excluding timestamps and operational settings."""

    encoded = json.dumps(spec.canonical_payload(), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def build_dense_vector_params(dimension: int) -> Any:
    """Build the one unnamed dense-vector schema used by snapshots and readiness."""

    from qdrant_client.http import models

    return models.VectorParams(size=dimension, distance=models.Distance.COSINE)


def build_sparse_vector_params() -> Any:
    """Build the named sparse index used by production lexical retrieval."""

    from qdrant_client.http import models

    return models.SparseVectorParams(index=models.SparseIndexParams())


class QdrantKnowledgeStore:
    """Build immutable Qdrant corpus snapshots and atomically activate their alias."""

    def __init__(
        self,
        url: str,
        collection_name: str,
        embedding_provider: EmbeddingProvider,
        *,
        timeout_seconds: float = 10.0,
        client: Any | None = None,
        embedding_model: str | None = None,
        schema_version: int = KNOWLEDGE_SCHEMA_VERSION,
        chunking_version: int = CHUNKING_VERSION,
        lexical_index_version: int = LEXICAL_SCHEMA_VERSION,
    ) -> None:
        if client is None:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=url, timeout=_native_timeout(timeout_seconds))
        self.client = client
        self.collection_name = collection_name
        self.embedding_provider = embedding_provider
        self.embedding_provider_name = _canonical_provider_identity(
            str(embedding_provider.provider_type)
        )
        self.embedding_model = _canonical_model_identity(
            str(
                embedding_model
                or getattr(
                    embedding_provider, "model", f"{embedding_provider.provider_type}-deterministic"
                )
            )
        )
        self.schema_version = schema_version
        self.chunking_version = chunking_version
        self.lexical_index_version = lexical_index_version
        self.timeout_seconds = timeout_seconds
        self.last_build_action = "none"

    def upsert(self, chunks: Sequence[DocumentChunk]) -> int:
        """Compatibility entry point: always builds and activates a complete snapshot."""

        if self._physical_collection_exists_without_alias():
            raise RuntimeError(
                "Qdrant collection is not production hybrid and exists without the managed "
                "logical alias; refusing to mutate "
                "a legacy or operator-managed collection. Build a fresh snapshot instead."
            )
        snapshot = self.build_snapshot(chunks)
        return snapshot.chunk_count

    def build_snapshot(
        self,
        chunks: Sequence[DocumentChunk],
        *,
        corpus_version: str | None = None,
        activate: bool = True,
    ) -> KnowledgeSnapshot:
        canonical_chunks = _canonical_chunks(chunks)
        if not canonical_chunks:
            raise ValueError("At least one knowledge chunk is required for a snapshot.")
        if len({chunk.chunk_id for chunk in canonical_chunks}) != len(canonical_chunks):
            raise ValueError("Knowledge snapshot contains duplicate chunk IDs.")

        corpus_hash = _corpus_hash(canonical_chunks)
        vectors = self.embedding_provider.embed_documents(
            [chunk.content for chunk in canonical_chunks]
        )
        dimension = _validate_vectors(vectors, self.embedding_provider)
        lexical_index = build_lexical_index(canonical_chunks)
        lexical_metadata = lexical_index.to_metadata(version=self.lexical_index_version)
        lexical_index_hash = _metadata_hash(lexical_metadata)
        snapshot_spec = KnowledgeSnapshotSpec(
            corpus_hash=corpus_hash,
            embedding_provider=self.embedding_provider_name,
            embedding_model=self.embedding_model,
            embedding_dimension=dimension,
            schema_version=self.schema_version,
            chunking_version=self.chunking_version,
            lexical_index_version=self.lexical_index_version,
        )
        snapshot_spec_hash = compute_snapshot_spec_hash(snapshot_spec)
        snapshot_id = snapshot_spec_hash
        physical_name = f"{self.collection_name}_v_{snapshot_spec_hash[:16]}"
        created_at = datetime.now(UTC).isoformat()
        build_id = str(uuid4())
        provenance = {
            "snapshot_id": snapshot_id,
            "snapshot_spec_hash": snapshot_spec_hash,
            "snapshot_spec_version": SNAPSHOT_SPEC_VERSION,
            "corpus_hash": corpus_hash,
            "corpus_version": corpus_hash,
            "chunk_count": len(canonical_chunks),
            "expected_chunk_count": len(canonical_chunks),
            "embedding_provider": self.embedding_provider_name,
            "embedding_model": self.embedding_model,
            "embedding_dimension": dimension,
            "lexical_index_version": self.lexical_index_version,
            "lexical_index_hash": lexical_index_hash,
            "schema_version": self.schema_version,
            "chunking_version": self.chunking_version,
            "dense_distance": QDRANT_DENSE_DISTANCE,
            "sparse_vector_name": LEXICAL_VECTOR_NAME,
            "created_at": created_at,
            "build_state": SNAPSHOT_BUILD_STATE_BUILDING,
            "build_id": build_id,
        }

        if self.client.collection_exists(physical_name):
            existing = _collection_metadata(self.client.get_collection(physical_name))
            existing_provenance = existing.get(SNAPSHOT_METADATA_KEY)
            if not isinstance(existing_provenance, dict):
                raise RuntimeError(
                    "A physical snapshot collection exists without managed provenance; "
                    "refusing automatic deletion."
                )
            if not _same_provenance(existing_provenance, provenance):
                raise RuntimeError(
                    "A physical snapshot collection already exists with incompatible provenance."
                )
            existing_state = existing_provenance.get("build_state")
            active_collection = _alias_target(self.client, self.collection_name)
            if existing_state == SNAPSHOT_BUILD_STATE_BUILDING:
                raise RuntimeError(
                    "The requested snapshot is already building; refusing a concurrent rebuild."
                )
            if existing_state == SNAPSHOT_BUILD_STATE_COMPLETE:
                existing_snapshot = _snapshot_from_collection(self.client, physical_name)
                try:
                    self.validate_snapshot(existing_snapshot)
                except Exception as error:
                    if active_collection == physical_name:
                        raise RuntimeError(
                            "The active snapshot is invalid; refusing automatic rebuild."
                        ) from error
                    self._delete_inactive_managed_snapshot(
                        physical_name, existing_provenance, provenance
                    )
                    self.last_build_action = "rebuilt"
                else:
                    self.last_build_action = "reused"
                    if activate and active_collection != physical_name:
                        self.activate(physical_name)
                    return existing_snapshot
            elif existing_state == SNAPSHOT_BUILD_STATE_FAILED:
                if active_collection == physical_name:
                    raise RuntimeError("The active snapshot is failed; refusing automatic rebuild.")
                self._delete_inactive_managed_snapshot(
                    physical_name, existing_provenance, provenance
                )
                self.last_build_action = "rebuilt"
            else:
                if active_collection == physical_name:
                    raise RuntimeError(
                        "The active snapshot has incomplete provenance; refusing automatic rebuild."
                    )
                self._delete_inactive_managed_snapshot(
                    physical_name, existing_provenance, provenance
                )
                self.last_build_action = "rebuilt"
        else:
            self.last_build_action = "created"

        from qdrant_client.http import models

        try:
            self.client.create_collection(
                collection_name=physical_name,
                vectors_config=build_dense_vector_params(dimension),
                sparse_vectors_config={LEXICAL_VECTOR_NAME: build_sparse_vector_params()},
                metadata={
                    LEXICAL_METADATA_KEY: lexical_metadata,
                    SNAPSHOT_METADATA_KEY: provenance,
                },
                timeout=_native_timeout(self.timeout_seconds),
            )
            points = [
                _point(
                    models=models,
                    chunk=chunk,
                    vector=vector,
                    lexical_index=lexical_index,
                )
                for chunk, vector in zip(canonical_chunks, vectors, strict=True)
            ]
            for offset in range(0, len(points), SNAPSHOT_UPSERT_BATCH_SIZE):
                self.client.upsert(
                    collection_name=physical_name,
                    points=points[offset : offset + SNAPSHOT_UPSERT_BATCH_SIZE],
                    timeout=_native_timeout(self.timeout_seconds),
                )
        except Exception:
            self._mark_build_failed(physical_name, provenance)
            raise

        snapshot = KnowledgeSnapshot(
            snapshot_id=snapshot_id,
            snapshot_spec_hash=snapshot_spec_hash,
            collection_name=physical_name,
            corpus_hash=corpus_hash,
            chunk_count=len(canonical_chunks),
            embedding_provider=self.embedding_provider_name,
            embedding_model=self.embedding_model,
            embedding_dimension=dimension,
            schema_version=self.schema_version,
            chunking_version=self.chunking_version,
            lexical_index_version=self.lexical_index_version,
            lexical_index_hash=lexical_index_hash,
            created_at=created_at,
            build_state=SNAPSHOT_BUILD_STATE_BUILDING,
        )
        try:
            self.validate_snapshot(snapshot, require_complete=False)
            completed_at = datetime.now(UTC).isoformat()
            provenance = {
                **provenance,
                "build_state": SNAPSHOT_BUILD_STATE_COMPLETE,
                "completed_at": completed_at,
            }
            self._update_provenance(physical_name, provenance)
            snapshot = replace(snapshot, build_state=SNAPSHOT_BUILD_STATE_COMPLETE)
            self.validate_snapshot(snapshot)
        except Exception:
            self._mark_build_failed(physical_name, provenance)
            raise
        if activate:
            self.activate(snapshot.collection_name)
        return snapshot

    def validate_snapshot(
        self, snapshot: KnowledgeSnapshot, *, require_complete: bool = True
    ) -> None:
        collection = self.client.get_collection(snapshot.collection_name)
        metadata = _collection_metadata(collection)
        provenance = metadata.get(SNAPSHOT_METADATA_KEY)
        if not isinstance(provenance, dict):
            raise RuntimeError("Qdrant snapshot provenance validation failed.")
        expected_spec = KnowledgeSnapshotSpec(
            corpus_hash=snapshot.corpus_hash,
            embedding_provider=snapshot.embedding_provider,
            embedding_model=snapshot.embedding_model,
            embedding_dimension=snapshot.embedding_dimension,
            schema_version=snapshot.schema_version,
            chunking_version=snapshot.chunking_version,
            lexical_index_version=snapshot.lexical_index_version,
        )
        expected_spec_hash = compute_snapshot_spec_hash(expected_spec)
        if snapshot.snapshot_spec_hash != expected_spec_hash:
            raise RuntimeError("Qdrant snapshot provenance validation failed.")
        required_provenance = {
            "snapshot_id",
            "snapshot_spec_hash",
            "snapshot_spec_version",
            "corpus_hash",
            "chunk_count",
            "expected_chunk_count",
            "embedding_provider",
            "embedding_model",
            "embedding_dimension",
            "lexical_index_version",
            "lexical_index_hash",
            "schema_version",
            "chunking_version",
            "dense_distance",
            "sparse_vector_name",
            "created_at",
            "build_state",
        }
        if not required_provenance.issubset(provenance):
            raise RuntimeError("Qdrant snapshot provenance validation failed.")
        build_state = provenance.get("build_state")
        if build_state not in {
            SNAPSHOT_BUILD_STATE_BUILDING,
            SNAPSHOT_BUILD_STATE_FAILED,
            SNAPSHOT_BUILD_STATE_COMPLETE,
        }:
            raise RuntimeError("Qdrant snapshot build state is invalid.")
        if require_complete and build_state != SNAPSHOT_BUILD_STATE_COMPLETE:
            raise RuntimeError("Qdrant snapshot is not complete.")
        if (
            provenance.get("expected_chunk_count") != snapshot.chunk_count
            or provenance.get("chunk_count") != snapshot.chunk_count
        ):
            raise RuntimeError("Qdrant snapshot provenance validation failed.")
        if require_complete and not isinstance(provenance.get("completed_at"), str):
            raise RuntimeError("Qdrant snapshot completion provenance is missing.")
        expected_provenance = {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_spec_hash": expected_spec_hash,
            "snapshot_spec_version": SNAPSHOT_SPEC_VERSION,
            "corpus_hash": snapshot.corpus_hash,
            "embedding_provider": snapshot.embedding_provider,
            "embedding_model": snapshot.embedding_model,
            "embedding_dimension": snapshot.embedding_dimension,
            "lexical_index_version": snapshot.lexical_index_version,
            "lexical_index_hash": snapshot.lexical_index_hash,
            "schema_version": snapshot.schema_version,
            "chunking_version": snapshot.chunking_version,
            "dense_distance": QDRANT_DENSE_DISTANCE,
            "sparse_vector_name": LEXICAL_VECTOR_NAME,
            "build_state": snapshot.build_state,
        }
        if any(provenance.get(key) != value for key, value in expected_provenance.items()):
            raise RuntimeError("Qdrant snapshot provenance validation failed.")
        lexical_metadata = metadata.get(LEXICAL_METADATA_KEY)
        if (
            not isinstance(lexical_metadata, dict)
            or _metadata_hash(lexical_metadata) != snapshot.lexical_index_hash
            or lexical_metadata.get("version") != snapshot.lexical_index_version
        ):
            raise RuntimeError("Qdrant snapshot lexical provenance validation failed.")
        point_count = _field(collection, "points_count")
        if not isinstance(point_count, int) or point_count != snapshot.chunk_count:
            raise RuntimeError("Qdrant snapshot point-count validation failed.")
        params = _field(_field(collection, "config"), "params")
        vectors = _field(params, "vectors")
        sparse_vectors = _field(params, "sparse_vectors")
        sparse_config = (
            sparse_vectors.get(LEXICAL_VECTOR_NAME) if isinstance(sparse_vectors, dict) else None
        )
        if (
            isinstance(vectors, dict)
            or _field(vectors, "size") != snapshot.embedding_dimension
            or _enum_value(_field(vectors, "distance")) != QDRANT_DENSE_DISTANCE
            or sparse_config is None
            or _field(sparse_config, "index") is None
        ):
            raise RuntimeError("Qdrant snapshot schema validation failed.")
        expected_dimension = getattr(self.embedding_provider, "dimension", None)
        if (
            snapshot.embedding_provider != self.embedding_provider_name
            or snapshot.embedding_model != self.embedding_model
            or (
                expected_dimension is not None
                and snapshot.embedding_dimension != expected_dimension
            )
            or snapshot.schema_version != self.schema_version
            or snapshot.chunking_version != self.chunking_version
            or snapshot.lexical_index_version != self.lexical_index_version
        ):
            raise RuntimeError("Qdrant snapshot is incompatible with the configured runtime.")

    def activate(self, physical_collection: str) -> None:
        """Atomically switch the logical alias, preserving the prior target on failure."""

        from qdrant_client.http import models

        if not self.client.collection_exists(physical_collection):
            raise ValueError("Cannot activate a missing Qdrant snapshot collection.")
        snapshot = _snapshot_from_collection(self.client, physical_collection)
        self.validate_snapshot(snapshot)
        current = _alias_target(self.client, self.collection_name)
        operations: list[Any] = []
        if current is not None:
            operations.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=self.collection_name)
                )
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=physical_collection,
                    alias_name=self.collection_name,
                )
            )
        )
        self.client.update_collection_aliases(
            operations,
            timeout=_native_timeout(self.timeout_seconds),
        )

    def rollback(self, snapshot_collection: str) -> None:
        """Switch to an existing validated snapshot without rebuilding it."""

        metadata = _collection_metadata(self.client.get_collection(snapshot_collection))
        provenance = metadata.get(SNAPSHOT_METADATA_KEY)
        if not isinstance(provenance, dict):
            raise ValueError("Rollback target is not a managed knowledge snapshot.")
        try:
            snapshot = KnowledgeSnapshot(
                snapshot_id=str(provenance["snapshot_id"]),
                snapshot_spec_hash=str(provenance["snapshot_spec_hash"]),
                collection_name=snapshot_collection,
                corpus_hash=str(provenance["corpus_hash"]),
                chunk_count=int(provenance["chunk_count"]),
                embedding_provider=str(provenance["embedding_provider"]),
                embedding_model=str(provenance["embedding_model"]),
                embedding_dimension=int(provenance["embedding_dimension"]),
                schema_version=int(provenance["schema_version"]),
                chunking_version=int(provenance["chunking_version"]),
                lexical_index_version=int(provenance["lexical_index_version"]),
                lexical_index_hash=str(provenance["lexical_index_hash"]),
                created_at=str(provenance["created_at"]),
                build_state=str(provenance["build_state"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Rollback target has invalid snapshot provenance.") from error
        self.validate_snapshot(snapshot)
        self.activate(snapshot_collection)

    def list_snapshots(self) -> list[dict[str, object]]:
        collections = self.client.get_collections().collections
        active = _alias_target(self.client, self.collection_name)
        snapshots: list[dict[str, object]] = []
        for item in collections:
            name = str(_field(item, "name"))
            if not name.startswith(f"{self.collection_name}_v_"):
                continue
            info = self.client.get_collection(name)
            metadata = _collection_metadata(info).get(SNAPSHOT_METADATA_KEY, {})
            if not isinstance(metadata, dict):
                continue
            snapshots.append(
                {
                    "collection_name": name,
                    "active": name == active,
                    "points_count": _field(info, "points_count"),
                    **metadata,
                }
            )
        return sorted(snapshots, key=lambda value: str(value.get("created_at", "")))

    def close(self) -> None:
        self.client.close()

    def _update_provenance(self, physical_collection: str, provenance: dict[str, object]) -> None:
        metadata = _collection_metadata(self.client.get_collection(physical_collection))
        metadata[SNAPSHOT_METADATA_KEY] = provenance
        self.client.update_collection(
            collection_name=physical_collection,
            metadata=metadata,
            timeout=_native_timeout(self.timeout_seconds),
        )

    def _mark_build_failed(
        self, physical_collection: str, expected_provenance: dict[str, object]
    ) -> None:
        try:
            if not self.client.collection_exists(physical_collection):
                return
            if _alias_target(self.client, self.collection_name) == physical_collection:
                return
            existing = _collection_metadata(self.client.get_collection(physical_collection)).get(
                SNAPSHOT_METADATA_KEY
            )
            if not isinstance(existing, dict) or not _same_provenance(
                existing, expected_provenance
            ):
                return
            if existing.get("build_id") != expected_provenance.get("build_id"):
                return
            self._update_provenance(
                physical_collection,
                {**existing, "build_state": SNAPSHOT_BUILD_STATE_FAILED},
            )
        except Exception:
            return

    def _delete_inactive_managed_snapshot(
        self,
        physical_collection: str,
        existing_provenance: dict[str, object],
        expected_provenance: dict[str, object],
    ) -> None:
        if _alias_target(self.client, self.collection_name) == physical_collection:
            raise RuntimeError("Refusing to delete the active Qdrant snapshot.")
        if not _same_provenance(existing_provenance, expected_provenance):
            raise RuntimeError("Qdrant snapshot provenance conflict; refusing automatic deletion.")
        self.client.delete_collection(
            collection_name=physical_collection,
            timeout=_native_timeout(self.timeout_seconds),
        )

    def _physical_collection_exists_without_alias(self) -> bool:
        return (
            self.client.collection_exists(self.collection_name)
            and _alias_target(self.client, self.collection_name) is None
        )


def _canonical_chunks(chunks: Sequence[DocumentChunk]) -> list[DocumentChunk]:
    return sorted(chunks, key=lambda chunk: chunk.chunk_id)


def _corpus_hash(chunks: Sequence[DocumentChunk]) -> str:
    payload = [chunk.model_dump(mode="json") for chunk in chunks]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical_provider_identity(value: str) -> str:
    return value.strip().casefold()


def _canonical_model_identity(value: str) -> str:
    return value.strip()


def _metadata_hash(metadata: dict[str, object]) -> str:
    encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _same_provenance(first: dict[str, object], second: dict[str, object]) -> bool:
    # Lifecycle fields may be absent on a Fix 15 collection. The semantic
    # snapshot identity and corpus size still have to match before recovery is
    # allowed; lifecycle metadata is repaired by the rebuild. The lexical index
    # hash is derived from the corpus and lexical version (both compared); its
    # float encoding changed once, so each collection validates its own hash instead.
    ignored = {
        "build_id",
        "created_at",
        "completed_at",
        "build_state",
        "expected_chunk_count",
        "lexical_index_hash",
    }
    return {key: value for key, value in first.items() if key not in ignored} == {
        key: value for key, value in second.items() if key not in ignored
    }


def _snapshot_from_collection(client: Any, collection_name: str) -> KnowledgeSnapshot:
    metadata = _collection_metadata(client.get_collection(collection_name))
    provenance = metadata.get(SNAPSHOT_METADATA_KEY)
    if not isinstance(provenance, dict):
        raise ValueError("Qdrant collection is missing managed snapshot provenance.")
    try:
        return KnowledgeSnapshot(
            snapshot_id=str(provenance["snapshot_id"]),
            snapshot_spec_hash=str(provenance["snapshot_spec_hash"]),
            collection_name=collection_name,
            corpus_hash=str(provenance["corpus_hash"]),
            chunk_count=int(provenance["chunk_count"]),
            embedding_provider=str(provenance["embedding_provider"]),
            embedding_model=str(provenance["embedding_model"]),
            embedding_dimension=int(provenance["embedding_dimension"]),
            schema_version=int(provenance["schema_version"]),
            chunking_version=int(provenance["chunking_version"]),
            lexical_index_version=int(provenance["lexical_index_version"]),
            lexical_index_hash=str(provenance["lexical_index_hash"]),
            created_at=str(provenance["created_at"]),
            build_state=str(provenance["build_state"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Qdrant collection has invalid snapshot provenance.") from error


def _validate_vectors(vectors: Sequence[Sequence[float]], provider: EmbeddingProvider) -> int:
    dimension = getattr(provider, "dimension", None)
    if not vectors:
        raise ValueError("Embedding provider returned no vectors.")
    actual = len(vectors[0])
    if dimension is not None and actual != dimension:
        raise ValueError("Embedding provider returned an unexpected vector dimension.")
    if any(len(vector) != actual for vector in vectors):
        raise ValueError("Embedding provider returned inconsistent vector dimensions.")
    return actual


def _native_timeout(seconds: float) -> int:
    """Qdrant's HTTP API accepts whole-second server deadlines only."""

    return max(1, int(seconds))


def _field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _collection_metadata(collection: object) -> dict[str, object]:
    metadata = _field(_field(collection, "config"), "metadata")
    return metadata if isinstance(metadata, dict) else {}


def _alias_target(client: Any, alias_name: str) -> str | None:
    get_aliases = getattr(client, "get_aliases", None)
    if not callable(get_aliases):
        return None
    response = get_aliases()
    for alias in getattr(response, "aliases", []):
        if _field(alias, "alias_name") == alias_name:
            target = _field(alias, "collection_name")
            return str(target) if target is not None else None
    return None


def _point(
    *, models: Any, chunk: DocumentChunk, vector: list[float], lexical_index: LexicalIndex
) -> Any:
    indices, values = lexical_index.encode(chunk.content)
    return models.PointStruct(
        id=str(uuid5(NAMESPACE_URL, chunk.chunk_id)),
        vector={
            "": vector,
            LEXICAL_VECTOR_NAME: models.SparseVector(indices=indices, values=values),
        },
        payload=chunk.model_dump(),
    )
