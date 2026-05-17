"""
Central configuration — all tunables in one place.
Values are read from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    # ── Corpus ────────────────────────────────────────────────────────────────
    corpus_dir: str = "corpus"

    # ── Embedding ─────────────────────────────────────────────────────────────
    # "sentence-transformers" (free, local) or "openai"
    embedding_provider: str = field(default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "sentence-transformers"))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))

    # ── Vector Store ──────────────────────────────────────────────────────────
    vector_store_path: str = field(default_factory=lambda: os.getenv("VECTOR_STORE_PATH", ".vectorstore"))
    collection_name: str = "docmind"

    # ── Chunking ──────────────────────────────────────────────────────────────
    chunk_size: int = 512          # tokens (approximate characters / 4)
    chunk_overlap: int = 64        # overlap to preserve context across boundaries
    min_chunk_size: int = 100      # discard chunks shorter than this

    # ── Retrieval ─────────────────────────────────────────────────────────────
    top_k: int = 5
    # Minimum similarity score (cosine) to include a chunk. Below this → low confidence.
    min_score_threshold: float = 0.30
    # If fewer than this many chunks meet the threshold → low confidence
    low_confidence_chunk_count: int = 2

    # ── LLM ───────────────────────────────────────────────────────────────────
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    llm_model: Optional[str] = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-3-5-haiku-20241022"))
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1024

    # API keys — NEVER hard-code; always from env
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))

    # ── Agent ─────────────────────────────────────────────────────────────────
    agent_max_steps: int = 6       # guard against infinite loops
    agent_model: Optional[str] = None  # falls back to llm_model if None

    # ── Observability ─────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    trace_file: str = "logs/agent_traces.jsonl"

    def __post_init__(self):
        # Allow constructor override for llm_model
        if self.llm_model is None:
            self.llm_model = os.getenv("LLM_MODEL", "claude-3-5-haiku-20241022")
        if self.agent_model is None:
            self.agent_model = self.llm_model
        os.makedirs("logs", exist_ok=True)
