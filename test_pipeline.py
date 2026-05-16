"""
Basic unit tests for the ingestion pipeline.
Run with: pytest tests/ -v
"""

import pytest
import numpy as np


# ── Chunker tests ─────────────────────────────────────────────────────────────

def test_chunker_splits_long_text():
    from src.ingestion.chunker import RecursiveChunker
    from src.ingestion.loader import RawDocument
    import time

    doc = RawDocument(
        doc_id="test_doc",
        source_path="/tmp/test.txt",
        filename="test.txt",
        extension=".txt",
        text="This is sentence one. This is sentence two. " * 200,
        created_at=time.time(),
    )
    chunker = RecursiveChunker(chunk_size=100, chunk_overlap=10)
    chunks = chunker.chunk_document(doc)
    assert len(chunks) > 1, "Long document should produce multiple chunks"


def test_chunker_preserves_doc_id():
    from src.ingestion.chunker import RecursiveChunker
    from src.ingestion.loader import RawDocument
    import time

    doc = RawDocument(
        doc_id="my_doc",
        source_path="/tmp/t.txt",
        filename="t.txt",
        extension=".txt",
        text="Hello world. " * 100,
        created_at=time.time(),
    )
    chunker = RecursiveChunker(chunk_size=50, chunk_overlap=5)
    chunks = chunker.chunk_document(doc)
    for c in chunks:
        assert c.doc_id == "my_doc"
        assert c.chunk_id.startswith("my_doc_")


def test_chunker_min_size_respected():
    from src.ingestion.chunker import RecursiveChunker
    from src.ingestion.loader import RawDocument
    import time

    doc = RawDocument(
        doc_id="test",
        source_path="/tmp/t.txt",
        filename="t.txt",
        extension=".txt",
        text="Short. " * 50,
        created_at=time.time(),
    )
    chunker = RecursiveChunker(chunk_size=50, chunk_overlap=5, min_chunk_size=10)
    chunks = chunker.chunk_document(doc)
    for c in chunks:
        assert len(c.text.strip()) >= 10


# ── Loader tests ──────────────────────────────────────────────────────────────

def test_loader_reads_markdown(tmp_path):
    from src.ingestion.loader import DocumentLoader
    md_file = tmp_path / "test.md"
    md_file.write_text("# Title\n\nThis is a **bold** paragraph.\n\n- item 1\n- item 2\n")
    loader = DocumentLoader()
    docs = loader.load_directory(str(tmp_path))
    assert len(docs) == 1
    assert "Title" in docs[0].text
    assert "bold" in docs[0].text
    assert docs[0].extension == ".md"


def test_loader_reads_txt(tmp_path):
    from src.ingestion.loader import DocumentLoader
    txt_file = tmp_path / "test.txt"
    txt_file.write_text("Hello world. This is a test document with enough content.")
    loader = DocumentLoader()
    docs = loader.load_directory(str(tmp_path))
    assert len(docs) == 1
    assert "Hello world" in docs[0].text


def test_loader_skips_unsupported_files(tmp_path):
    from src.ingestion.loader import DocumentLoader
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "data.csv").write_text("a,b,c\n1,2,3\n")
    loader = DocumentLoader()
    docs = loader.load_directory(str(tmp_path))
    assert len(docs) == 0


# ── Config tests ──────────────────────────────────────────────────────────────

def test_config_defaults():
    from src.utils.config import Config
    cfg = Config()
    assert cfg.embedding_provider in ("sentence-transformers", "openai")
    assert cfg.top_k > 0
    assert cfg.chunk_size > 0
    assert cfg.chunk_overlap < cfg.chunk_size


def test_config_override():
    from src.utils.config import Config
    cfg = Config(top_k=10, chunk_size=256)
    assert cfg.top_k == 10
    assert cfg.chunk_size == 256


# ── VectorStore tests (requires chromadb) ────────────────────────────────────

def test_vector_store_add_and_search(tmp_path):
    pytest.importorskip("chromadb")
    from src.ingestion.vector_store import VectorStore
    from src.ingestion.chunker import Chunk
    import time

    store = VectorStore(persist_dir=str(tmp_path), collection_name="test")

    chunks = [
        Chunk(
            chunk_id=f"doc1_{i}",
            doc_id="doc1",
            doc_name="test.txt",
            text=f"This is test chunk number {i} about database architecture.",
            char_start=i * 100,
            char_end=(i + 1) * 100,
            chunk_index=i,
            source_path="/tmp/test.txt",
            created_at=time.time(),
        )
        for i in range(5)
    ]

    embeddings = np.random.rand(5, 384).astype(np.float32)
    # Normalise
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    store.add_chunks(chunks, embeddings)
    assert store.count() == 5

    query_vec = np.random.rand(384).astype(np.float32)
    query_vec = query_vec / np.linalg.norm(query_vec)
    results = store.search(query_vec, top_k=3)
    assert len(results) == 3
    assert all("chunk_id" in r for r in results)
    assert all("score" in r for r in results)


def test_vector_store_list_documents(tmp_path):
    pytest.importorskip("chromadb")
    from src.ingestion.vector_store import VectorStore
    from src.ingestion.chunker import Chunk
    import time

    store = VectorStore(persist_dir=str(tmp_path / "vs2"), collection_name="test2")

    for doc_id in ("doc_a", "doc_b"):
        chunks = [
            Chunk(
                chunk_id=f"{doc_id}_{i}",
                doc_id=doc_id,
                doc_name=f"{doc_id}.txt",
                text=f"Content from {doc_id} chunk {i}",
                char_start=i * 50,
                char_end=(i + 1) * 50,
                chunk_index=i,
                source_path=f"/tmp/{doc_id}.txt",
                created_at=time.time(),
            )
            for i in range(3)
        ]
        embeddings = np.random.rand(3, 384).astype(np.float32)
        store.add_chunks(chunks, embeddings)

    docs = store.list_documents()
    assert len(docs) == 2
    doc_ids = {d["doc_id"] for d in docs}
    assert "doc_a" in doc_ids
    assert "doc_b" in doc_ids
