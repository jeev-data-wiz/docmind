"""
Document Loader
===============
Loads PDF, Markdown (.md), and plain text (.txt) files into a
normalised internal format: list of RawDocument objects.

Design decision: keep loaders thin — extract raw text + metadata only.
Chunking is a separate concern (see chunker.py).
"""

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".text", ".markdown"}


@dataclass
class RawDocument:
    """Normalised representation of a loaded document."""
    doc_id: str                          # unique stable identifier
    source_path: str                     # absolute path on disk
    filename: str                        # basename only
    extension: str                       # e.g. ".pdf"
    text: str                            # full extracted text
    total_pages: int = 1
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class DocumentLoader:
    """
    Loads all supported documents from a directory.

    Why not one huge class per format?  Because each format needs its own
    dependency (pypdf, markdown, etc.).  We isolate that with small private
    methods so failures in one format don't block others.
    """

    def load_directory(self, directory: str) -> List[RawDocument]:
        docs: List[RawDocument] = []
        path = Path(directory)
        if not path.exists():
            raise FileNotFoundError(f"Corpus directory not found: {directory}")

        files = [
            p for p in path.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        logger.info(f"Found {len(files)} supported files in {directory}")

        for fp in sorted(files):
            try:
                doc = self._load_file(fp)
                if doc and len(doc.text.strip()) > 50:
                    docs.append(doc)
                    logger.info(f"Loaded: {fp.name} ({len(doc.text)} chars)")
                else:
                    logger.warning(f"Skipping (too short or empty): {fp.name}")
            except Exception as e:
                logger.error(f"Failed to load {fp.name}: {e}")

        return docs

    def _load_file(self, path: Path) -> Optional[RawDocument]:
        ext = path.suffix.lower()
        if ext == ".pdf":
            return self._load_pdf(path)
        elif ext in (".md", ".markdown"):
            return self._load_markdown(path)
        elif ext in (".txt", ".text"):
            return self._load_text(path)
        return None

    def _load_pdf(self, path: Path) -> RawDocument:
        try:
            import pypdf
        except ImportError:
            raise ImportError("pypdf not installed. Run: pip install pypdf")

        pages = []
        with open(path, "rb") as f:
            reader = pypdf.PdfReader(f)
            total_pages = len(reader.pages)
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"[Page {i+1}]\n{text}")

        full_text = "\n\n".join(pages)
        return RawDocument(
            doc_id=self._make_id(path),
            source_path=str(path.resolve()),
            filename=path.name,
            extension=".pdf",
            text=full_text,
            total_pages=total_pages,
            metadata={"format": "pdf", "pages": total_pages},
        )

    def _load_markdown(self, path: Path) -> RawDocument:
        # Strip markdown syntax for cleaner embedding; keep section headers as
        # natural sentence boundaries.
        raw = path.read_text(encoding="utf-8", errors="replace")

        # Convert headers to plain text so embeddings pick up topic signals
        import re
        text = re.sub(r"^#{1,6}\s+", "", raw, flags=re.MULTILINE)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # bold
        text = re.sub(r"\*(.+?)\*", r"\1", text)       # italic
        text = re.sub(r"`{1,3}[^`]*`{1,3}", "", text)  # inline code
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)     # images
        text = re.sub(r"\[(.+?)\]\(.*?\)", r"\1", text) # links → anchor text

        return RawDocument(
            doc_id=self._make_id(path),
            source_path=str(path.resolve()),
            filename=path.name,
            extension=".md",
            text=text,
            metadata={"format": "markdown"},
        )

    def _load_text(self, path: Path) -> RawDocument:
        text = path.read_text(encoding="utf-8", errors="replace")
        return RawDocument(
            doc_id=self._make_id(path),
            source_path=str(path.resolve()),
            filename=path.name,
            extension=".txt",
            text=text,
            metadata={"format": "text"},
        )

    @staticmethod
    def _make_id(path: Path) -> str:
        """Stable ID: relative path with slashes replaced."""
        return path.stem.lower().replace(" ", "_")
