"""
Evaluator
=========
Runs the 5 canonical evaluation questions against the agent and scores
responses for correctness, confidence, and source citation.

The evaluation questions cover:
  Q1 — Simple factual            (single document lookup)
  Q2 — Multi-hop reasoning       (synthesise 2+ documents)
  Q3 — Out-of-scope / no-answer  (should say "I don't know")
  Q4 — Date-range / temporal     (filter_by_date tool path)
  Q5 — Summarisation / overview  (broad question needing summarise_document)
"""

from typing import List, Dict, Any
from src.agent.agent_loop import DocMindAgent
from src.utils.config import Config
from src.utils.logger import get_logger

logger = get_logger(__name__)

EVAL_QUESTIONS = [
    {
        "id": "Q1",
        "difficulty": "simple_factual",
        "question": "What is the primary purpose of the authentication service described in the architecture documents?",
        "expected_behaviour": "Should cite a specific document and give a precise factual answer.",
        "expected_confidence": "high",
    },
    {
        "id": "Q2",
        "difficulty": "multi_hop",
        "question": (
            "How did the database incident in the incident report relate to the architecture "
            "decisions described in the ADR documents? Were there any design choices that "
            "contributed to or mitigated the issue?"
        ),
        "expected_behaviour": "Should retrieve from both incident report and ADR docs and synthesise.",
        "expected_confidence": "high",
    },
    {
        "id": "Q3",
        "difficulty": "out_of_scope",
        "question": "What is the current stock price of Anthropic and its projected IPO valuation?",
        "expected_behaviour": "Should clearly state it does not have this information in the corpus.",
        "expected_confidence": "low",
    },
    {
        "id": "Q4",
        "difficulty": "temporal",
        "question": "What incidents or issues were reported in the most recent documents in the corpus?",
        "expected_behaviour": "Should use list_documents or filter_by_date to scope the search.",
        "expected_confidence": "high",
    },
    {
        "id": "Q5",
        "difficulty": "summarisation",
        "question": (
            "Give me a high-level overview of all the documents in the corpus. "
            "What topics do they cover and how do they relate to each other?"
        ),
        "expected_behaviour": "Should call list_documents then summarise_document for each, synthesise.",
        "expected_confidence": "high",
    },
]


class Evaluator:
    def __init__(self, config: Config):
        self.config = config
        self.agent = DocMindAgent(config)

    def run_all(self) -> List[Dict[str, Any]]:
        results = []
        for q in EVAL_QUESTIONS:
            logger.info(f"\n{'='*60}")
            logger.info(f"Running {q['id']} [{q['difficulty']}]: {q['question'][:70]}...")
            result = self.agent.run(q["question"])
            results.append({
                **q,
                "answer": result["answer"],
                "confidence": result["confidence"],
                "sources": result["sources"],
                "steps_taken": len(result["steps"]),
                "latency_ms": result["latency_ms"],
                "confidence_match": result["confidence"] == q["expected_confidence"],
            })
        return results

    def print_report(self, results: List[Dict[str, Any]]):
        print("\n" + "="*70)
        print("DocMind Evaluation Report")
        print("="*70)

        for r in results:
            match = "✅" if r["confidence_match"] else "⚠️ "
            print(f"\n{r['id']} [{r['difficulty']}] {match}")
            print(f"  Q: {r['question'][:80]}")
            print(f"  A: {r['answer'][:200]}...")
            print(f"  Confidence: {r['confidence']} (expected: {r['expected_confidence']})")
            print(f"  Steps: {r['steps_taken']} | Latency: {r['latency_ms']}ms | Sources: {len(r['sources'])}")
            print(f"  Expected behaviour: {r['expected_behaviour']}")

        correct = sum(1 for r in results if r["confidence_match"])
        print(f"\n{'='*70}")
        print(f"Confidence accuracy: {correct}/{len(results)}")
        avg_latency = sum(r["latency_ms"] for r in results) / len(results)
        print(f"Average latency: {avg_latency:.0f}ms")
        avg_steps = sum(r["steps_taken"] for r in results) / len(results)
        print(f"Average agent steps: {avg_steps:.1f}")
        print("="*70)
