"""
Vector Store
============
Wraps ChromaDB for local persistent storage of embeddings + metadata.

Why ChromaDB?
  • Fully local, no server process required (embedded mode)
  • Persistent to disk out of the box
  • Fast cosine similarity via HNSW index
  • Native metadata filtering support
  • Active development, MIT license

Tradeoff vs FAISS:
  FAISS is faster for pure ANN search but has no built-in metadata
  storage or filtering — you'd need a parallel metadata DB.
  ChromaDB bundles both, which is the right tradeoff for a prototype.
"""

import json
import time
from typing import List, Dict, Any, Optional

import numpy as np

from src.ingestion.chunker import Chunk
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VectorStore:
    """
    ChromaDB-backed vector store.

    Responsibilities:
      • add_chunks(chunks, embeddings)  — insert/upsert
      • search(query_vec, top_k)        — ANN search + score
      • filter_by_date(start, end)      — metadata-filtered search
      • list_documents()                — return corpus index
      • get_document_chunks(doc_id)     — all chunks for one doc
      • reset()                         — clear collection
    """

    def __init__(self, persist_dir: str = ".vectorstore", collection_name: str = "docmind"):
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise ImportError(
                "chromadb not installed.\nRun: pip install chromadb"
            )

        self._client = chromadb.PersistentClient(
            path=persist_dir,
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},  # cosine distance
        )
        logger.info(
            f"Vector store ready at '{persist_dir}' "
            f"(collection='{collection_name}', "
            f"docs={self._collection.count()})"
        )

    # ── Write ─────────────────────────────────────────────────────────────────

    def add_chunks(self, chunks: List[Chunk], embeddings: np.ndarray) -> None:
        """Upsert chunks with their embeddings into the collection."""
        if not chunks:
            return

        ids = [c.chunk_id for c in chunks]
        docs = [c.text for c in chunks]
        metas = [self._build_meta(c) for c in chunks]
        vecs = embeddings.tolist()

        # ChromaDB upsert handles duplicates gracefully (idempotent re-ingestion)
        self._collection.upsert(
            ids=ids,
            documents=docs,
            embeddings=vecs,
            metadatas=metas,
        )
        logger.debug(f"Upserted {len(chunks)} chunks")

    # ── Read ──────────────────────────────────────────────────────────────────

    def search(
        self,
        query_vec: np.ndarray,
        top_k: int = 5,
        where: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns top_k most similar chunks.
        Each result dict has: chunk_id, text, score, doc_name, doc_id, metadata.
        Score is cosine similarity (1 = identical, 0 = orthogonal).
        """
        kwargs = {
            "query_embeddings": [query_vec.tolist()],
            "n_results": min(top_k, self._collection.count() or 1),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        hits = []
        for i in range(len(results["ids"][0])):
            distance = results["distances"][0][i]
            # ChromaDB returns cosine *distance* (0=identical) → convert to similarity
            score = 1.0 - distance
            hits.append({
                "chunk_id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "score": round(score, 4),
                **results["metadatas"][0][i],
            })

        return sorted(hits, key=lambda x: x["score"], reverse=True)

    def filter_by_date(
        self,
        query_vec: np.ndarray,
        start_ts: float,
        end_ts: float,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search restricted to chunks with created_at in [start_ts, end_ts]."""
        where = {
            "$and": [
                {"created_at": {"$gte": start_ts}},
                {"created_at": {"$lte": end_ts}},
            ]
        }
        return self.search(query_vec, top_k=top_k, where=where)

    def list_documents(self) -> List[Dict[str, Any]]:
        """Return one entry per unique document in the corpus."""
        if self._collection.count() == 0:
            return []

        # Fetch all metadata (no embeddings) and de-duplicate by doc_id
        all_items = self._collection.get(include=["metadatas"])
        seen = {}
        for meta in all_items["metadatas"]:
            doc_id = meta.get("doc_id", "unknown")
            if doc_id not in seen:
                seen[doc_id] = {
                    "doc_id": doc_id,
                    "doc_name": meta.get("doc_name", ""),
                    "format": meta.get("format", ""),
                    "total_chunks": meta.get("total_chunks", 0),
                }
        return list(seen.values())

    def get_document_chunks(self, doc_id: str) -> List[Dict[str, Any]]:
        """Fetch all chunks for a given document ID."""
        results = self._collection.get(
            where={"doc_id": {"$eq": doc_id}},
            include=["documents", "metadatas"],
        )
        chunks = []
        for i, cid in enumerate(results["ids"]):
            chunks.append({
                "chunk_id": cid,
                "text": results["documents"][i],
                **results["metadatas"][i],
            })
        return sorted(chunks, key=lambda x: x.get("chunk_index", 0))

    def count(self) -> int:
        return self._collection.count()

    def reset(self) -> None:
        self._collection.delete(where={"doc_id": {"$ne": "__never__"}})
        logger.warning("Vector store cleared")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_meta(chunk: Chunk) -> Dict[str, Any]:
        """Flatten chunk metadata to a dict of primitives (ChromaDB requirement)."""
        return {
            "doc_id": chunk.doc_id,
            "doc_name": chunk.doc_name,
            "source_path": chunk.source_path,
            "chunk_index": chunk.chunk_index,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
            "created_at": chunk.created_at,
            **{k: v for k, v in chunk.metadata.items() if isinstance(v, (str, int, float, bool))},
        }
