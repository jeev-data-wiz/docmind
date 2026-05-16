"""
Ingestion Pipeline
==================
Orchestrates: Load → Chunk → Embed → Store

This is the "write path" of the system.  Run once (or whenever the corpus
changes).  Results are persisted in the vector store for repeated queries.
"""

import time
from typing import Dict, Any

from src.ingestion.loader import DocumentLoader
from src.ingestion.chunker import RecursiveChunker
from src.ingestion.embedder import build_embedder
from src.ingestion.vector_store import VectorStore
from src.utils.config import Config
from src.utils.logger import get_logger, TraceLogger

logger = get_logger(__name__)


class IngestionPipeline:
    def __init__(self, config: Config):
        self.config = config
        self.loader = DocumentLoader()
        self.chunker = RecursiveChunker(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
            min_chunk_size=config.min_chunk_size,
        )
        self.embedder = build_embedder(
            provider=config.embedding_provider,
            model=config.embedding_model,
            api_key=config.openai_api_key,
        )
        self.store = VectorStore(
            persist_dir=config.vector_store_path,
            collection_name=config.collection_name,
        )
        self.tracer = TraceLogger(config.trace_file)

    def ingest_directory(self, directory: str) -> Dict[str, Any]:
        """Full pipeline for a directory of documents."""
        t_start = time.time()
        stats = {"total_docs": 0, "total_chunks": 0, "skipped": 0, "failed": 0}

        # 1. Load
        logger.info(f"[1/3] Loading documents from: {directory}")
        documents = self.loader.load_directory(directory)
        if not documents:
            logger.warning("No documents loaded — check the corpus directory.")
            return stats

        stats["total_docs"] = len(documents)

        # 2. Chunk
        logger.info(f"[2/3] Chunking {len(documents)} documents...")
        all_chunks = []
        for doc in documents:
            chunks = self.chunker.chunk_document(doc)
            all_chunks.extend(chunks)
            logger.info(f"  {doc.filename}: {len(chunks)} chunks")

        stats["total_chunks"] = len(all_chunks)

        # 3. Embed + Store (batch for efficiency)
        logger.info(f"[3/3] Embedding {len(all_chunks)} chunks...")
        BATCH = 64
        for i in range(0, len(all_chunks), BATCH):
            batch = all_chunks[i: i + BATCH]
            texts = [c.text for c in batch]
            t_embed = time.time()
            embeddings = self.embedder.embed_texts(texts)
            embed_ms = (time.time() - t_embed) * 1000
            self.store.add_chunks(batch, embeddings)
            logger.debug(
                f"  Batch {i//BATCH + 1}: {len(batch)} chunks embedded in {embed_ms:.0f}ms"
            )

        elapsed = time.time() - t_start
        self.tracer.log("ingest_complete", {
            "directory": directory,
            "total_docs": stats["total_docs"],
            "total_chunks": stats["total_chunks"],
            "elapsed_s": round(elapsed, 2),
        })

        logger.info(
            f"Ingestion done in {elapsed:.1f}s — "
            f"{stats['total_docs']} docs, {stats['total_chunks']} chunks stored"
        )
        return stats
