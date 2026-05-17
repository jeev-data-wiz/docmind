"""
Chunker
=======
Splits raw document text into overlapping chunks suitable for embedding.

Strategy chosen: Recursive Character Text Splitting with semantic boundary awareness.

WHY THIS STRATEGY (for the README / panel presentation):
  • Fixed-size word/char splits risk cutting sentences mid-thought, degrading
    retrieval precision.
  • Pure sentence splitting creates chunks that are too short for complex
    questions — the model loses context.
  • Recursive splitting tries larger separators first (double-newline → single
    newline → sentence → word) so chunks break at the most natural boundary
    available.
  • Overlap (default 64 tokens ≈ ~256 chars) ensures that answers near a chunk
    boundary appear in at least one chunk fully, preventing split-answer misses.
  • We target ~512 tokens per chunk. This fits comfortably in most embedding
    models' max context (512–8192 tokens) and leaves room in the LLM context
    for many chunks simultaneously.

TRADEOFFS:
  Pro:  Simple, deterministic, no ML model needed at ingest time.
  Con:  Doesn't capture semantic paragraphs as well as semantic chunking
        (e.g. with a sentence-transformer to detect topic shifts).
  Future: Switch to semantic chunking via cosine-similarity between
          consecutive sentence embeddings.
"""

from dataclasses import dataclass, field
from typing import List
import re
import time

from src.ingestion.loader import RawDocument
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Separators tried in order — most preferred first
SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]


@dataclass
class Chunk:
    """A single piece of a document, ready for embedding."""
    chunk_id: str          # e.g. "incident_report_2023_3"
    doc_id: str
    doc_name: str          # human-friendly filename
    text: str
    char_start: int
    char_end: int
    chunk_index: int       # 0-based index within the document
    source_path: str
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class RecursiveChunker:
    """
    Splits documents with recursive character-level splitting.
    Mirrors the logic of LangChain's RecursiveCharacterTextSplitter
    but implemented from scratch so we can explain every line.
    """

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64, min_chunk_size: int = 100):
        # chunk_size in *characters* (approx 1 token ≈ 4 chars, so 512 chars ≈ 128 tokens)
        # We deliberately use characters not tokens to avoid a tokenizer dependency at ingest.
        self.chunk_size = chunk_size * 4        # convert token-estimate → chars
        self.chunk_overlap = chunk_overlap * 4
        self.min_chunk_size = min_chunk_size

    def chunk_document(self, doc: RawDocument) -> List[Chunk]:
        raw_chunks = self._split(doc.text, SEPARATORS)
        # Merge tiny fragments with the previous chunk
        merged = self._merge_small_chunks(raw_chunks)

        chunks: List[Chunk] = []
        pos = 0
        for i, text in enumerate(merged):
            if len(text.strip()) < self.min_chunk_size:
                continue
            # Locate approximate char position (fast scan)
            start = doc.text.find(text[:50], pos)
            if start == -1:
                start = pos
            end = start + len(text)
            pos = max(pos, end - self.chunk_overlap)

            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}_{i}",
                doc_id=doc.doc_id,
                doc_name=doc.filename,
                text=text.strip(),
                char_start=start,
                char_end=end,
                chunk_index=i,
                source_path=doc.source_path,
                metadata={**doc.metadata, "chunk_index": i, "total_chunks": 0},  # patched below
            ))

        # Patch total_chunks now we know it
        for c in chunks:
            c.metadata["total_chunks"] = len(chunks)

        logger.debug(f"  {doc.filename}: {len(chunks)} chunks")
        return chunks

    # ── Private helpers ───────────────────────────────────────────────────────

    def _split(self, text: str, separators: List[str]) -> List[str]:
        """Recursively split text using the first separator that produces manageable chunks."""
        if not text:
            return []
        if len(text) <= self.chunk_size:
            return [text]

        sep = self._pick_separator(text, separators)
        parts = text.split(sep) if sep else list(text)

        chunks = []
        current = ""
        for part in parts:
            candidate = (current + sep + part) if current else part
            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                # If part itself is too big, recurse with next separator
                if len(part) > self.chunk_size:
                    remaining_seps = separators[separators.index(sep) + 1:] if sep in separators else []
                    chunks.extend(self._split(part, remaining_seps or [""]))
                    current = ""
                else:
                    current = part

        if current:
            chunks.append(current)

        return chunks

    def _pick_separator(self, text: str, separators: List[str]) -> str:
        for sep in separators:
            if sep and sep in text:
                return sep
        return ""

    def _merge_small_chunks(self, chunks: List[str]) -> List[str]:
        """Merge fragments smaller than min_chunk_size into the preceding chunk."""
        merged = []
        for chunk in chunks:
            if merged and len(chunk) < self.min_chunk_size:
                merged[-1] += " " + chunk
            else:
                merged.append(chunk)
        return merged
