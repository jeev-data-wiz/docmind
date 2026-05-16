"""
RAG Query Engine
================
The "read path" of the system.

Flow:
  1. Embed the user query
  2. Retrieve top-k chunks from the vector store
  3. Assess confidence (score distribution)
  4. Build a grounded prompt with retrieved context
  5. Call the LLM and return answer + sources

Prompt Engineering Notes (for the panel):
  - We use a strict "answer ONLY from the context" instruction to prevent
    hallucination. The model is explicitly told to say "I don't know" if
    the context doesn't contain the answer.
  - Sources are injected as numbered XML-style blocks so the model can
    reference them clearly without confusing them with its own knowledge.
  - Temperature is set to 0.1 — we want deterministic factual answers,
    not creative generation.
  - We include chunk metadata (doc name, chunk index) in the context block
    so the model can cite sources inline if needed.
"""

import time
from typing import List, Dict, Any, Optional

from src.ingestion.embedder import build_embedder
from src.ingestion.vector_store import VectorStore
from src.retrieval.llm_client import build_llm
from src.utils.config import Config
from src.utils.logger import get_logger, TraceLogger

logger = get_logger(__name__)

# ── Prompt templates ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are DocMind, an expert document intelligence assistant.
Your job is to answer questions STRICTLY based on the provided document context.

Rules:
1. ONLY use information present in the <context> blocks below.
2. If the context does not contain enough information to answer confidently, say:
   "I don't have enough information in the provided documents to answer this question."
3. NEVER fabricate facts, dates, names, or numbers.
4. Always cite which document your answer came from (use the [Source: ...] label).
5. If multiple documents are relevant, synthesise them clearly.
6. Be concise and precise. Avoid padding."""

QUERY_PROMPT_TEMPLATE = """<context>
{context_blocks}
</context>

Question: {question}

Answer (citing sources):"""


def build_context_blocks(chunks: List[Dict[str, Any]]) -> str:
    """Format retrieved chunks into numbered context blocks for the prompt."""
    blocks = []
    for i, chunk in enumerate(chunks, 1):
        doc_name = chunk.get("doc_name", "unknown")
        chunk_idx = chunk.get("chunk_index", "?")
        score = chunk.get("score", 0)
        blocks.append(
            f"[Source {i}: {doc_name}, chunk {chunk_idx}, relevance {score:.2f}]\n"
            f"{chunk['text'].strip()}"
        )
    return "\n\n---\n\n".join(blocks)


class QueryEngine:
    """
    RAG query engine: retrieve → prompt → answer.
    """

    def __init__(self, config: Config):
        self.config = config
        self.embedder = build_embedder(
            provider=config.embedding_provider,
            model=config.embedding_model,
            api_key=config.openai_api_key,
        )
        self.store = VectorStore(
            persist_dir=config.vector_store_path,
            collection_name=config.collection_name,
        )
        self.llm = build_llm(
            provider=config.llm_provider,
            model=config.llm_model,
            anthropic_api_key=config.anthropic_api_key,
            openai_api_key=config.openai_api_key,
        )
        self.tracer = TraceLogger(config.trace_file)

    def query(
        self,
        question: str,
        top_k: Optional[int] = None,
        where: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Execute a RAG query.

        Returns:
            {
                "answer": str,
                "sources": [...],
                "confidence": "high" | "low",
                "latency_ms": float,
            }
        """
        t_start = time.time()
        k = top_k or self.config.top_k

        # 1. Embed query
        query_vec = self.embedder.embed_query(question)

        # 2. Retrieve chunks
        chunks = self.store.search(query_vec, top_k=k, where=where)
        logger.info(f"Retrieved {len(chunks)} chunks for: '{question[:60]}...'")

        # 3. Assess confidence
        confidence = self._assess_confidence(chunks)

        # 4. Build prompt
        context_blocks = build_context_blocks(chunks)
        user_message = QUERY_PROMPT_TEMPLATE.format(
            context_blocks=context_blocks,
            question=question,
        )

        # 5. Call LLM
        answer = self.llm.complete(
            messages=[{"role": "user", "content": user_message}],
            system=SYSTEM_PROMPT,
            temperature=self.config.llm_temperature,
            max_tokens=self.config.llm_max_tokens,
        )

        latency_ms = (time.time() - t_start) * 1000

        sources = [
            {
                "doc_name": c.get("doc_name", ""),
                "chunk_id": c.get("chunk_id", ""),
                "score": c.get("score", 0),
                "chunk_index": c.get("chunk_index", 0),
            }
            for c in chunks
        ]

        result = {
            "answer": answer.strip(),
            "sources": sources,
            "confidence": confidence,
            "latency_ms": round(latency_ms, 1),
            "chunks_used": len(chunks),
        }

        self.tracer.log("rag_query", {
            "question": question,
            "confidence": confidence,
            "chunks_used": len(chunks),
            "latency_ms": round(latency_ms, 1),
        })

        return result

    def _assess_confidence(self, chunks: List[Dict[str, Any]]) -> str:
        """
        Heuristic confidence assessment:
          - Low if fewer than threshold chunks meet the minimum score
          - Low if the top chunk score is below the threshold
        """
        if not chunks:
            return "low"

        above_threshold = [
            c for c in chunks
            if c.get("score", 0) >= self.config.min_score_threshold
        ]

        if len(above_threshold) < self.config.low_confidence_chunk_count:
            return "low"

        top_score = chunks[0].get("score", 0)
        if top_score < self.config.min_score_threshold:
            return "low"

        return "high"
