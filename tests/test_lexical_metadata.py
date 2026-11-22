import json

from app.rag.retrieval.lexical import LexicalIndex, build_lexical_index
from app.rag.schemas import DocumentChunk


def _chunk(index: int, content: str) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=f"doc#s#{index}",
        document_id="doc",
        title="Doc",
        category="policy",
        section="s",
        source="test",
        chunk_index=index,
        content=content,
    )


def test_lexical_metadata_survives_a_shorter_float_round_trip() -> None:
    # Qdrant may serialise metadata floats with fewer digits; 16 significant digits must be exact.
    chunks = [_chunk(i, f"order carrier cancellation word{i} " * (i + 1)) for i in range(16)]
    metadata = build_lexical_index(chunks).to_metadata()
    shortened = json.loads(
        json.dumps(metadata), parse_float=lambda value: float(f"{float(value):.16g}")
    )
    assert shortened == metadata
    assert LexicalIndex.from_metadata(shortened).to_metadata() == metadata
