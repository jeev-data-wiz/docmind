"""
Agent Tools
===========
Each tool is a self-contained callable with a name, description, and
input/output schema.  The agent uses the description to decide WHEN to
call each tool.

Tools implemented:
  1. retrieve          — semantic vector search (always available)
  2. summarise_document — returns a concise summary of one document
  3. filter_by_date    — narrows retrieval to a date range
  4. list_documents    — returns the corpus index

Design pattern: each tool returns a ToolResult with a standard shape so
the agent loop can process them uniformly regardless of which tool ran.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: Any                          # tool-specific payload
    summary: str                         # 1-line human-readable summary
    error: Optional[str] = None
    latency_ms: float = 0.0


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "retrieve",
        "description": (
            "Search the document corpus for chunks relevant to a query. "
            "Use this as your primary information-gathering tool. "
            "Input: {\"query\": \"<search query>\", \"top_k\": <int, optional>}"
        ),
    },
    {
        "name": "summarise_document",
        "description": (
            "Get a concise summary of an entire document by its doc_id. "
            "Useful when you need high-level context about what a document covers "
            "before diving into specific chunks. "
            "Input: {\"doc_id\": \"<document id from list_documents>\"}"
        ),
    },
    {
        "name": "filter_by_date",
        "description": (
            "Search the corpus restricted to documents ingested within a date range. "
            "Use when the question involves time-bounded context (e.g. 'incidents last month'). "
            "Input: {\"query\": \"<search query>\", \"start_date\": \"YYYY-MM-DD\", \"end_date\": \"YYYY-MM-DD\"}"
        ),
    },
    {
        "name": "list_documents",
        "description": (
            "Return the full index of documents in the corpus (doc_id, filename, format, chunk count). "
            "Use this first when you are unsure which documents exist or need to plan a multi-hop search. "
            "Input: {} (no parameters required)"
        ),
    },
    {
        "name": "finish",
        "description": (
            "Signal that you have gathered enough information and are ready to produce the final answer. "
            "Input: {\"answer\": \"<your final answer>\", \"confidence\": \"high\" | \"low\"}"
        ),
    },
]


class ToolExecutor:
    """
    Executes agent tool calls.
    Holds references to the embedder, vector store, and LLM needed by tools.
    """

    def __init__(self, embedder, store, llm, config):
        self.embedder = embedder
        self.store = store
        self.llm = llm
        self.config = config

    def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> ToolResult:
        t0 = time.time()
        try:
            if tool_name == "retrieve":
                result = self._retrieve(tool_input)
            elif tool_name == "summarise_document":
                result = self._summarise_document(tool_input)
            elif tool_name == "filter_by_date":
                result = self._filter_by_date(tool_input)
            elif tool_name == "list_documents":
                result = self._list_documents(tool_input)
            elif tool_name == "finish":
                result = self._finish(tool_input)
            else:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    summary=f"Unknown tool: {tool_name}",
                    error=f"Tool '{tool_name}' is not registered.",
                )
            result.latency_ms = round((time.time() - t0) * 1000, 1)
            return result
        except Exception as e:
            logger.error(f"Tool '{tool_name}' failed: {e}")
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                summary=f"Tool failed: {e}",
                error=str(e),
                latency_ms=round((time.time() - t0) * 1000, 1),
            )

    # ── Tool implementations ──────────────────────────────────────────────────

    def _retrieve(self, inp: Dict) -> ToolResult:
        query = inp.get("query", "")
        top_k = int(inp.get("top_k", self.config.top_k))
        if not query:
            return ToolResult("retrieve", False, [], "Empty query", error="No query provided")

        vec = self.embedder.embed_query(query)
        chunks = self.store.search(vec, top_k=top_k)
        summary = f"Retrieved {len(chunks)} chunks for query: '{query[:50]}'"
        logger.debug(summary)
        return ToolResult("retrieve", True, chunks, summary)

    def _summarise_document(self, inp: Dict) -> ToolResult:
        doc_id = inp.get("doc_id", "")
        if not doc_id:
            return ToolResult("summarise_document", False, None, "No doc_id provided", error="Missing doc_id")

        chunks = self.store.get_document_chunks(doc_id)
        if not chunks:
            return ToolResult(
                "summarise_document", False, None,
                f"No chunks found for doc_id: {doc_id}",
                error="Document not found"
            )

        # Use first 8 chunks as representative sample (fits in ~4k tokens)
        sample_text = "\n\n".join(c["text"] for c in chunks[:8])
        prompt = (
            f"Please provide a concise 3-5 sentence summary of the following document "
            f"(doc_id: {doc_id}).\n\nDocument content:\n{sample_text}\n\nSummary:"
        )
        summary_text = self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
        )
        summary = f"Summarised document '{doc_id}' ({len(chunks)} chunks)"
        return ToolResult("summarise_document", True, {"doc_id": doc_id, "summary": summary_text.strip()}, summary)

    def _filter_by_date(self, inp: Dict) -> ToolResult:
        query = inp.get("query", "")
        start_str = inp.get("start_date", "")
        end_str = inp.get("end_date", "")

        try:
            start_ts = datetime.strptime(start_str, "%Y-%m-%d").timestamp() if start_str else 0.0
            end_ts = datetime.strptime(end_str, "%Y-%m-%d").timestamp() if end_str else time.time()
        except ValueError as e:
            return ToolResult("filter_by_date", False, [], f"Date parse error: {e}", error=str(e))

        vec = self.embedder.embed_query(query)
        chunks = self.store.filter_by_date(vec, start_ts, end_ts, top_k=self.config.top_k)
        summary = f"Date-filtered retrieval [{start_str} → {end_str}]: {len(chunks)} chunks"
        return ToolResult("filter_by_date", True, chunks, summary)

    def _list_documents(self, inp: Dict) -> ToolResult:
        docs = self.store.list_documents()
        summary = f"Corpus index: {len(docs)} documents"
        return ToolResult("list_documents", True, docs, summary)

    def _finish(self, inp: Dict) -> ToolResult:
        answer = inp.get("answer", "")
        confidence = inp.get("confidence", "high")
        return ToolResult(
            "finish", True,
            {"answer": answer, "confidence": confidence},
            f"Agent finished (confidence={confidence})"
        )
