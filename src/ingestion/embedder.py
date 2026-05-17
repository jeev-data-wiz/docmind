"""
Embedder
========
Converts text into dense vector embeddings.

Supports:
  • sentence-transformers  (local, free, default)
  • openai                 (API-based, requires OPENAI_API_KEY)

Design decision: we wrap both under a common EmbedderBase interface so the
rest of the system is provider-agnostic. Swapping providers = one env var change.

Tradeoff (for README):
  sentence-transformers all-MiniLM-L6-v2:
    Pro:  Free, runs locally, 384-dim, fast on CPU, no API key needed
    Con:  Lower quality than OpenAI ada-002 / text-embedding-3-small on complex
          domain-specific queries
  openai text-embedding-3-small:
    Pro:  Higher quality, 1536-dim, better multilingual support
    Con:  Costs money, requires internet, adds latency, API key management
"""

import time
from abc import ABC, abstractmethod
from typing import List

import numpy as np

from src.utils.logger import get_logger, TraceLogger

logger = get_logger(__name__)


class EmbedderBase(ABC):
    @abstractmethod
    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """Return (N, D) float32 array of embeddings."""
        ...

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single query string."""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        ...


class SentenceTransformerEmbedder(EmbedderBase):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed.\n"
                "Run: pip install sentence-transformers"
            )
        logger.info(f"Loading sentence-transformer model: {model_name}")
        self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension()
        logger.info(f"Embedder ready (dim={self._dim})")

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        t0 = time.time()
        vecs = self._model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,  # cosine similarity works correctly with L2-normalised vecs
        )
        logger.debug(f"Embedded {len(texts)} texts in {time.time()-t0:.2f}s")
        return vecs.astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_texts([text])[0]

    @property
    def dimension(self) -> int:
        return self._dim


class OpenAIEmbedder(EmbedderBase):
    def __init__(self, model_name: str = "text-embedding-3-small", api_key: str = ""):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai not installed. Run: pip install openai")
        import os
        key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        self._client = OpenAI(api_key=key)
        self._model = model_name
        # text-embedding-3-small → 1536 dims; text-embedding-3-large → 3072
        self._dim = 1536

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        # OpenAI API max batch = 2048 inputs
        all_vecs = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            vecs = [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]
            all_vecs.extend(vecs)
        return np.array(all_vecs, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_texts([text])[0]

    @property
    def dimension(self) -> int:
        return self._dim


def build_embedder(provider: str, model: str, api_key: str = "") -> EmbedderBase:
    """Factory — returns the right embedder based on config."""
    if provider == "openai":
        return OpenAIEmbedder(model_name=model, api_key=api_key)
    elif provider == "sentence-transformers":
        return SentenceTransformerEmbedder(model_name=model)
    else:
        raise ValueError(f"Unknown embedding provider: {provider}. Choose 'sentence-transformers' or 'openai'.")
