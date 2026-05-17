"""
DocMind Agent — ReAct Reasoning Loop
=====================================
Implements the ReAct (Reason + Act) pattern:
  Thought → Action → Observation → Thought → ... → Final Answer

Why ReAct over a simple one-shot RAG?
  • Multi-hop questions need multiple retrievals with different sub-queries
  • The agent can inspect the document list first, then decide WHERE to search
  • It can detect when a first retrieval is insufficient and issue follow-ups
  • It provides a transparent reasoning trace (great for debugging + eval)

Agent prompt design:
  We give the LLM a strict JSON-only output format for tool calls.
  This avoids parsing fragile free-text tool invocations.
  Format:
    {"thought": "...", "action": "<tool_name>", "action_input": {...}}
  or to finish:
    {"thought": "...", "action": "finish", "action_input": {"answer": "...", "confidence": "high|low"}}

Stopping conditions:
  1. Agent calls "finish" tool
  2. Max steps reached (config.agent_max_steps) → forced finish
  3. Two consecutive identical tool calls (loop detection)
"""

import json
import re
import time
from typing import Dict, Any, List, Optional

from src.ingestion.embedder import build_embedder
from src.ingestion.vector_store import VectorStore
from src.retrieval.llm_client import build_llm
from src.retrieval.query_engine import QueryEngine
from src.tools.tool_executor import ToolExecutor, TOOL_SCHEMAS, ToolResult
from src.utils.config import Config
from src.utils.logger import get_logger, TraceLogger

logger = get_logger(__name__)

# ── Agent system prompt ────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are DocMind, an AI document intelligence agent.
You answer questions by reasoning over a corpus of internal technical documents.

You have access to the following tools:

{tool_descriptions}

STRICT OUTPUT FORMAT — every response must be valid JSON in one of these two shapes:

To call a tool:
{{"thought": "<your reasoning>", "action": "<tool_name>", "action_input": {{...}}}}

To give a final answer:
{{"thought": "<your reasoning>", "action": "finish", "action_input": {{"answer": "<your complete answer>", "confidence": "high" or "low"}}}}

Rules:
- Respond with ONLY the JSON object — no preamble, no explanation outside the JSON.
- Always start with list_documents if you are unsure what is in the corpus.
- If one retrieval pass is insufficient, run additional retrieve calls with refined sub-queries.
- If the corpus clearly does not contain the answer, set confidence to "low" and explain clearly.
- Never fabricate information. Cite document names in your final answer.
- Maximum {max_steps} steps allowed."""

OBSERVATION_TEMPLATE = """Observation from tool '{tool_name}':
{observation}"""


def format_tool_descriptions(schemas: List[Dict]) -> str:
    lines = []
    for s in schemas:
        lines.append(f"  • {s['name']}: {s['description']}")
    return "\n".join(lines)


class DocMindAgent:
    """
    Agentic reasoning loop over the document corpus.
    """

    def __init__(self, config: Config):
        self.config = config

        # Shared components (built once, reused across queries)
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
            model=config.agent_model or config.llm_model,
            anthropic_api_key=config.anthropic_api_key,
            openai_api_key=config.openai_api_key,
        )
        self.executor = ToolExecutor(
            embedder=self.embedder,
            store=self.store,
            llm=self.llm,
            config=config,
        )
        self.tracer = TraceLogger(config.trace_file)

        self._system_prompt = AGENT_SYSTEM_PROMPT.format(
            tool_descriptions=format_tool_descriptions(TOOL_SCHEMAS),
            max_steps=config.agent_max_steps,
        )

    def run(self, question: str) -> Dict[str, Any]:
        """
        Run the full agentic loop for a question.

        Returns:
            {
                "answer": str,
                "sources": [...],
                "confidence": "high" | "low",
                "steps": [...],
                "latency_ms": float,
            }
        """
        t_start = time.time()
        logger.info(f"Agent starting for: '{question}'")

        conversation: List[Dict[str, str]] = [
            {"role": "user", "content": f"Question: {question}"}
        ]

        steps = []
        all_sources: List[Dict] = []
        last_action_key = None  # for loop detection

        for step_num in range(1, self.config.agent_max_steps + 1):
            logger.info(f"[Step {step_num}/{self.config.agent_max_steps}]")

            # ── LLM decides next action ───────────────────────────────────────
            raw_response = self.llm.complete(
                messages=conversation,
                system=self._system_prompt,
                temperature=0.1,
                max_tokens=600,
            )

            # ── Parse JSON response ───────────────────────────────────────────
            parsed = self._parse_llm_response(raw_response)
            if parsed is None:
                logger.warning(f"Could not parse LLM response at step {step_num}. Forcing finish.")
                return self._forced_finish(
                    "The agent encountered a parsing error. Please try rephrasing your question.",
                    steps, all_sources, t_start, confidence="low"
                )

            thought = parsed.get("thought", "")
            action = parsed.get("action", "finish")
            action_input = parsed.get("action_input", {})

            logger.info(f"  Thought: {thought[:100]}")
            logger.info(f"  Action: {action} | Input: {str(action_input)[:80]}")

            # ── Loop detection ────────────────────────────────────────────────
            action_key = f"{action}:{json.dumps(action_input, sort_keys=True)}"
            if action_key == last_action_key:
                logger.warning("Loop detected — same action twice. Forcing finish.")
                return self._forced_finish(
                    "The agent detected a reasoning loop. "
                    "The available documents may not contain a clear answer.",
                    steps, all_sources, t_start, confidence="low"
                )
            last_action_key = action_key

            # ── Execute tool ──────────────────────────────────────────────────
            tool_result: ToolResult = self.executor.execute(action, action_input)

            steps.append({
                "step": step_num,
                "thought": thought,
                "action": action,
                "action_input": action_input,
                "summary": tool_result.summary,
                "success": tool_result.success,
                "latency_ms": tool_result.latency_ms,
            })

            self.tracer.log("agent_step", {
                "step": step_num,
                "action": action,
                "summary": tool_result.summary,
            }, query=question)

            # ── Collect sources from retrieval tools ──────────────────────────
            if action in ("retrieve", "filter_by_date") and tool_result.success:
                chunks = tool_result.output or []
                for c in chunks:
                    source = {
                        "doc_name": c.get("doc_name", ""),
                        "chunk_id": c.get("chunk_id", ""),
                        "score": c.get("score", 0),
                        "chunk_index": c.get("chunk_index", 0),
                    }
                    if source not in all_sources:
                        all_sources.append(source)

            # ── Check if agent is done ────────────────────────────────────────
            if action == "finish":
                final_answer = action_input.get("answer", "")
                confidence = action_input.get("confidence", "high")
                latency_ms = round((time.time() - t_start) * 1000, 1)

                self.tracer.log("agent_finish", {
                    "confidence": confidence,
                    "steps": step_num,
                    "latency_ms": latency_ms,
                }, query=question)

                logger.info(f"Agent finished in {step_num} steps ({latency_ms:.0f}ms)")
                return {
                    "answer": final_answer,
                    "sources": all_sources,
                    "confidence": confidence,
                    "steps": steps,
                    "latency_ms": latency_ms,
                }

            # ── Add observation to conversation for next step ─────────────────
            observation = self._format_observation(action, tool_result)
            conversation.append({"role": "assistant", "content": raw_response})
            conversation.append({"role": "user", "content": observation})

        # Max steps exceeded
        logger.warning(f"Max steps ({self.config.agent_max_steps}) reached without finish.")
        return self._forced_finish(
            "The agent reached its maximum reasoning steps. "
            "The question may require more context than currently available.",
            steps, all_sources, t_start, confidence="low"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_llm_response(self, text: str) -> Optional[Dict]:
        """Extract JSON from LLM output, handling markdown fences."""
        text = text.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object within the text
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        logger.error(f"Failed to parse JSON from LLM response:\n{text[:300]}")
        return None

    def _format_observation(self, action: str, result: ToolResult) -> str:
        """Format tool output as an observation message for the next LLM turn."""
        if not result.success:
            obs = f"ERROR: {result.error}"
        elif action == "retrieve" or action == "filter_by_date":
            chunks = result.output or []
            if not chunks:
                obs = "No relevant chunks found."
            else:
                parts = []
                for i, c in enumerate(chunks[:5], 1):
                    parts.append(
                        f"[Chunk {i} | {c.get('doc_name','?')} | score={c.get('score',0):.3f}]\n"
                        f"{c.get('text','')[:400]}"
                    )
                obs = "\n\n".join(parts)
        elif action == "summarise_document":
            data = result.output or {}
            obs = f"Summary of '{data.get('doc_id', '?')}':\n{data.get('summary', '')}"
        elif action == "list_documents":
            docs = result.output or []
            if not docs:
                obs = "The corpus is empty. Please run ingestion first."
            else:
                lines = [f"  • {d['doc_id']} ({d.get('doc_name','')}, {d.get('total_chunks',0)} chunks)" for d in docs]
                obs = "Corpus documents:\n" + "\n".join(lines)
        else:
            obs = str(result.output)

        return OBSERVATION_TEMPLATE.format(tool_name=action, observation=obs)

    def _forced_finish(
        self,
        message: str,
        steps: List,
        sources: List,
        t_start: float,
        confidence: str = "low",
    ) -> Dict[str, Any]:
        return {
            "answer": message,
            "sources": sources,
            "confidence": confidence,
            "steps": steps,
            "latency_ms": round((time.time() - t_start) * 1000, 1),
        }
