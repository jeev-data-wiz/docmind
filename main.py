"""
DocMind AI Agent — Main Entry Point
=====================================
Run modes:
  python main.py ingest           — ingest corpus directory
  python main.py query "..."      — single query (RAG only)
  python main.py agent "..."      — agentic reasoning loop
  python main.py eval             — run evaluation suite
  python main.py interactive      — interactive REPL
"""

import sys
import argparse

# Load .env file before importing anything that reads env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; user can set env vars directly

from src.utils.logger import get_logger
from src.utils.config import Config

logger = get_logger(__name__)


def run_ingest(config: Config):
    from src.ingestion.pipeline import IngestionPipeline
    pipeline = IngestionPipeline(config)
    stats = pipeline.ingest_directory(config.corpus_dir)
    print(f"\n✅ Ingestion complete: {stats['total_chunks']} chunks from {stats['total_docs']} documents")
    print(f"   Skipped: {stats.get('skipped', 0)} | Failed: {stats.get('failed', 0)}")


def run_query(config: Config, query: str):
    from src.retrieval.query_engine import QueryEngine
    engine = QueryEngine(config)
    result = engine.query(query)
    print(f"\n🔍 Query: {query}")
    print(f"\n📝 Answer:\n{result['answer']}")
    print(f"\n📚 Sources ({len(result['sources'])}):")
    for s in result["sources"]:
        print(f"  • [{s['doc_name']}] chunk {s['chunk_id']} (score: {s['score']:.3f})")


def run_agent(config: Config, query: str):
    from src.agent.agent_loop import DocMindAgent
    agent = DocMindAgent(config)
    result = agent.run(query)
    print(f"\n🤖 Agent Query: {query}")
    print(f"\n📝 Answer:\n{result['answer']}")
    print(f"\n🔄 Steps taken: {len(result['steps'])}")
    for i, step in enumerate(result["steps"], 1):
        print(f"  Step {i}: [{step['action']}] {step.get('summary', '')}")
    if result.get("confidence") == "low":
        print("\n⚠️  Low confidence — retrieved evidence was sparse or tangential.")
    print(f"\n📚 Sources ({len(result['sources'])}):")
    for s in result["sources"]:
        print(f"  • [{s['doc_name']}] chunk {s['chunk_id']} (score: {s['score']:.3f})")


def run_eval(config: Config):
    from eval.evaluator import Evaluator
    evaluator = Evaluator(config)
    results = evaluator.run_all()
    evaluator.print_report(results)


def run_interactive(config: Config):
    from src.agent.agent_loop import DocMindAgent
    agent = DocMindAgent(config)
    print("\n🧠 DocMind AI Agent — Interactive Mode")
    print("Type 'exit' or 'quit' to stop.\n")
    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if query.lower() in ("exit", "quit", "q"):
            print("Goodbye!")
            break
        if not query:
            continue
        result = agent.run(query)
        print(f"\nAgent: {result['answer']}")
        if result.get("confidence") == "low":
            print("⚠️  (Low confidence answer)")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="DocMind — AI Document Intelligence Agent"
    )
    parser.add_argument(
        "mode",
        choices=["ingest", "query", "agent", "eval", "interactive"],
        help="Run mode",
    )
    parser.add_argument("input", nargs="?", help="Query string for query/agent modes")
    parser.add_argument("--corpus-dir", default="corpus", help="Path to corpus directory")
    parser.add_argument("--top-k", type=int, default=5, help="Number of chunks to retrieve")
    parser.add_argument("--model", default=None, help="Override LLM model name")

    args = parser.parse_args()

    config = Config(
        corpus_dir=args.corpus_dir,
        top_k=args.top_k,
        llm_model=args.model,
    )

    if args.mode == "ingest":
        run_ingest(config)
    elif args.mode == "query":
        if not args.input:
            parser.error("query mode requires an input query string")
        run_query(config, args.input)
    elif args.mode == "agent":
        if not args.input:
            parser.error("agent mode requires an input query string")
        run_agent(config, args.input)
    elif args.mode == "eval":
        run_eval(config)
    elif args.mode == "interactive":
        run_interactive(config)


if __name__ == "__main__":
    main()
