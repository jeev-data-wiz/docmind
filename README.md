# DocMind — AI Document Intelligence Agent

> An AI-powered RAG agent that ingests internal technical documents (PDFs, Markdown, plain text),
> indexes them into a local vector store, and answers complex multi-hop questions through
> an agentic reasoning loop.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Key Design Decisions](#key-design-decisions)
3. [Project Structure](#project-structure)
4. [Setup & Run Instructions](#setup--run-instructions)
5. [Evaluation Questions](#evaluation-questions)
6. [What I Would Do With More Time](#what-i-would-do-with-more-time)
7. [Observability](#observability)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         WRITE PATH (Ingestion)                      │
│                                                                     │
│  corpus/                                                            │
│  ├── *.pdf  ──┐                                                     │
│  ├── *.md   ──┼──▶ DocumentLoader ──▶ RecursiveChunker ──▶ Embedder │
│  └── *.txt  ──┘         │                    │               │      │
│                   (extract text)     (split+overlap)  (all-MiniLM)  │
│                                                          │           │
│                                                          ▼           │
│                                                   VectorStore        │
│                                                  (ChromaDB local)    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         READ PATH (Query / Agent)                   │
│                                                                     │
│  User Query                                                         │
│      │                                                              │
│      ▼                                                              │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │                    DocMind Agent (ReAct Loop)              │      │
│  │                                                           │      │
│  │  Thought ──▶ Action ──▶ Observation ──▶ Thought ──▶ ...  │      │
│  │                 │                                          │      │
│  │          ┌──────┴───────────┐                             │      │
│  │          │   Tool Executor  │                             │      │
│  │          │                  │                             │      │
│  │          │ • retrieve       │◀──▶ VectorStore (ChromaDB) │      │
│  │          │ • summarise_doc  │◀──▶ LLM (Claude / GPT)     │      │
│  │          │ • filter_by_date │◀──▶ VectorStore             │      │
│  │          │ • list_documents │◀──▶ VectorStore             │      │
│  │          │ • finish         │                             │      │
│  │          └──────────────────┘                             │      │
│  │                                                           │      │
│  │  Final Answer + Sources + Confidence + Step Trace         │      │
│  └───────────────────────────────────────────────────────────┘      │
│                                                                     │
│  Also available: QueryEngine (simple single-pass RAG, no agent)     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         OBSERVABILITY                               │
│                                                                     │
│  stdout logs ──▶ Human-readable timestamped output                  │
│  logs/agent_traces.jsonl ──▶ Structured JSONL per step/event       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. Chunking Strategy: Recursive Character Splitting with Overlap

**What:** Text is split using a priority list of separators: `\n\n` → `\n` → `. ` → ` ` → character. The largest available boundary that fits within the target size is used first.

**Why:** Pure fixed-size splits cut sentences mid-thought, hurting retrieval precision. Pure sentence splitting creates chunks too small for complex questions. Recursive splitting produces natural-boundary-aligned chunks of predictable size.

**Parameters:** ~512 tokens (≈2048 chars) per chunk with 64-token (≈256 char) overlap. The overlap ensures answers near a chunk boundary appear fully in at least one chunk.

**Tradeoff:** Simpler than semantic chunking (no clustering step), but doesn't detect topic shifts within a section. For v2, I'd explore cosine-similarity-based semantic chunking.

---

### 2. Embedding Model: sentence-transformers all-MiniLM-L6-v2

**What:** A 384-dimensional embedding model, runs entirely locally on CPU.

**Why:** Free, no API key needed, fast (< 1s per batch), and good enough for English technical documents. Vectors are L2-normalised so cosine similarity = dot product (fast).

**Tradeoff vs OpenAI text-embedding-3-small:**  
OpenAI gives 1536-dim embeddings with better multilingual support and higher accuracy on domain-specific queries — but costs money and requires internet access. For a prototype, local is the right choice. The system is designed to swap providers with a single env var change.

---

### 3. Vector Store: ChromaDB (local persistent mode)

**What:** Embedded ChromaDB writing to a local directory (`.vectorstore/`).

**Why:** ChromaDB bundles vector storage, ANN index (HNSW), and metadata filtering in a single local library. No server process required. Supports cosine similarity natively. Active development with good Python SDK.

**Tradeoff vs FAISS:** FAISS is faster for pure ANN search but has no metadata storage. You'd need a parallel metadata database, adding complexity. For this prototype, ChromaDB's integrated approach is the right tradeoff.

---

### 4. LLM: Claude 3.5 Haiku (Anthropic)

**What:** Claude 3.5 Haiku via the Anthropic API.

**Why:**
- Fastest Claude model — critical for multi-step agent loops where each step adds latency
- 200k token context window — can handle many retrieved chunks simultaneously
- Excellent instruction-following for the strict JSON output format the agent requires
- Cost-effective for prototype workloads (~$0.001 per query)

**Tradeoff:** Requires an Anthropic API key. The system supports OpenAI as a drop-in alternative via one env var change.

---

### 5. Agentic Pattern: ReAct (Reason + Act)

**What:** The agent follows a Thought → Action → Observation loop. The LLM outputs JSON with a `thought`, `action`, and `action_input` on every step. The loop continues until the agent calls `finish` or hits the max-step guard.

**Why ReAct over single-pass RAG:**
- Multi-hop questions need multiple retrievals with different sub-queries
- The agent can inspect `list_documents` first, then decide where to search
- It can detect when a first retrieval is insufficient and refine its query
- The step trace provides full transparency into reasoning

**Why JSON output (not free-text tool calls):**  
JSON is deterministic to parse, catches hallucinated tool names immediately, and doesn't require a complex text parser. We strip markdown fences and fall back to regex extraction for robustness.

---

### 6. Confidence Assessment

The system flags low-confidence answers when:
- Fewer than 2 retrieved chunks score above the minimum cosine similarity threshold (0.30)
- The top chunk score is below the threshold

This is a deliberate proxy for "the corpus doesn't contain a good answer." Rather than hallucinating, the agent is instructed to say "I don't have enough information" — and the confidence flag signals to callers to show a warning in the UI.

---

## Project Structure

```
docmind/
├── main.py                         # Entry point (ingest / query / agent / eval / interactive)
├── requirements.txt
├── Makefile
├── .env.example
│
├── corpus/                         # Your documents go here
│   ├── 01_system_architecture.md
│   ├── 02_incident_report_INC2023047.md
│   ├── 03_adr_007_authentication.md
│   ├── 04_adr_012_database_scaling.md
│   └── 05_design_spec_rtns.md
│
├── src/
│   ├── ingestion/
│   │   ├── loader.py               # Load PDF, MD, TXT → RawDocument
│   │   ├── chunker.py              # Recursive character chunking
│   │   ├── embedder.py             # sentence-transformers / OpenAI embedder
│   │   ├── vector_store.py         # ChromaDB wrapper
│   │   └── pipeline.py             # Orchestrates load → chunk → embed → store
│   │
│   ├── retrieval/
│   │   ├── llm_client.py           # Anthropic / OpenAI LLM wrapper
│   │   └── query_engine.py         # RAG: embed query → retrieve → prompt → answer
│   │
│   ├── agent/
│   │   └── agent_loop.py           # ReAct agent loop
│   │
│   ├── tools/
│   │   └── tool_executor.py        # retrieve, summarise_document, filter_by_date, list_documents
│   │
│   └── utils/
│       ├── config.py               # Central configuration (env vars)
│       └── logger.py               # Structured logging + JSONL trace writer
│
├── eval/
│   └── evaluator.py                # 5 evaluation questions + report
│
└── logs/
    └── agent_traces.jsonl          # Structured traces (auto-created)
```

---

## Setup & Run Instructions

### Prerequisites

- Python 3.10 or higher
- pip
- An Anthropic API key **or** an OpenAI API key

---

### Step 1: Clone or download the repository

```bash
git clone https://github.com/YOUR_USERNAME/docmind.git
cd docmind
```

---

### Step 2: Create a virtual environment (recommended)

```bash
python -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows
```

---

### Step 3: Install dependencies

```bash
pip install -r requirements.txt
```

This installs: `sentence-transformers`, `chromadb`, `anthropic`, `pypdf`, `numpy`, `python-dotenv`.

> **Note:** The first run will download the `all-MiniLM-L6-v2` model (~80MB) automatically.

---

### Step 4: Configure your API key

```bash
cp .env.example .env
```

Open `.env` in any text editor and add your API key:

```
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx...
```

Or if using OpenAI:
```
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-xxxxxxxx...
```

---

### Step 5: Add documents to the corpus

The `corpus/` directory already contains 5 sample documents. You can add your own
PDF, Markdown (`.md`), or plain text (`.txt`) files to this directory.

---

### Step 6: Ingest the corpus

```bash
python main.py ingest
# or
make ingest
```

Expected output:
```
Loaded: 01_system_architecture.md (4823 chars)
...
Ingestion complete: 47 chunks from 5 documents
```

---

### Step 7: Run a query

**Simple RAG query (single retrieval pass):**
```bash
python main.py query "What is the purpose of the AuthCore authentication service?"
```

**Agentic reasoning (multi-hop, tool use):**
```bash
python main.py agent "How did the database incident relate to the architecture decisions in the ADR documents?"
```

**Interactive mode (chat REPL):**
```bash
python main.py interactive
# or
make interactive
```

**Run evaluation suite:**
```bash
python main.py eval
# or
make eval
```

---

## Evaluation Questions

These 5 questions test the system across difficulty levels:

| ID | Difficulty        | Question |
|----|-------------------|----------|
| Q1 | Simple factual    | What is the primary purpose of the authentication service described in the architecture documents? |
| Q2 | Multi-hop         | How did the database incident in the incident report relate to the architecture decisions in the ADR documents? |
| Q3 | Out-of-scope      | What is the current stock price of Anthropic and its projected IPO valuation? |
| Q4 | Temporal          | What incidents or issues were reported in the most recent documents in the corpus? |
| Q5 | Summarisation     | Give me a high-level overview of all the documents in the corpus. What topics do they cover and how do they relate to each other? |

**Recommended additional test questions:**
- "What JWT token expiry time does AuthCore use, and why was that value chosen?"
- "Which action items from the Black Friday incident are still in progress?"
- "What is the RTNS service and what existing InfraCore components does it depend on?"
- "Were there any architectural decisions that explicitly acknowledged risks that later materialised?"

---

## What I Would Do With More Time

### 1. Semantic Chunking
Replace recursive character chunking with embedding-based semantic chunking: embed every sentence, detect topic shifts via cosine similarity drops, and chunk at semantic boundaries. This would improve retrieval precision for documents with dense mixed content.

### 2. Hybrid Search (BM25 + Vector)
Add a BM25 sparse retriever alongside the dense vector retriever. Combine scores with Reciprocal Rank Fusion (RRF). Hybrid search is consistently better than either alone, especially for technical jargon and exact-match queries.

### 3. Query Rewriting
Add an LLM pre-pass that rewrites the user query into 2–3 reformulations before retrieval, then de-duplicates results. This significantly improves recall for vague or ambiguous questions.

### 4. Re-ranking
After retrieving the top-k chunks, apply a cross-encoder re-ranker (e.g. `cross-encoder/ms-marco-MiniLM-L-6-v2`) to re-score each chunk against the query. Cross-encoders are more accurate than bi-encoders but too slow to run over the full corpus — the bi-encoder retrieval + cross-encoder re-rank pattern is the production standard.

### 5. Token Revocation Blocklist (AuthCore gap)
ADR-007 noted that token revocation is a known gap. I'd implement a Redis-backed revocation blocklist checked at token validation time.

### 6. Proper Evaluation Metrics
Add automated scoring: use an LLM judge to score answer correctness against ground-truth answers on a labelled evaluation set. Track RAGAS metrics (faithfulness, answer relevancy, context precision, context recall).

### 7. Streaming Responses
Stream LLM tokens to the client rather than waiting for the full response. Dramatically improves perceived latency, especially for long answers.

### 8. Web UI
Add a simple FastAPI backend + React frontend for a chat interface with source citation highlighting and agent step visualisation.

---

## Observability

Every query writes structured JSON to `logs/agent_traces.jsonl`:

```json
{"ts": 1710000000.0, "event": "agent_step", "step": 1, "action": "list_documents", "summary": "Corpus index: 5 documents", "query": "How did the incident..."}
{"ts": 1710000001.2, "event": "agent_step", "step": 2, "action": "retrieve", "summary": "Retrieved 5 chunks for query: 'database incident'", "query": "How did the incident..."}
{"ts": 1710000003.1, "event": "agent_finish", "confidence": "high", "steps": 3, "latency_ms": 3100, "query": "How did the incident..."}
```

Each event includes: timestamp, event type, action taken, latency, confidence, and the original query. This trace file can be ingested into any log analytics system (e.g. OpenSearch, Datadog).

---

## Environment Variables Reference

| Variable              | Default                      | Description                         |
|-----------------------|------------------------------|-------------------------------------|
| `ANTHROPIC_API_KEY`   | —                            | Anthropic API key (required)        |
| `OPENAI_API_KEY`      | —                            | OpenAI API key (optional)           |
| `LLM_PROVIDER`        | `anthropic`                  | `anthropic` or `openai`             |
| `LLM_MODEL`           | `claude-3-5-haiku-20241022`  | Model name                          |
| `EMBEDDING_PROVIDER`  | `sentence-transformers`      | `sentence-transformers` or `openai` |
| `EMBEDDING_MODEL`     | `all-MiniLM-L6-v2`           | Embedding model name                |
| `VECTOR_STORE_PATH`   | `.vectorstore`               | Directory for ChromaDB persistence  |
| `LOG_LEVEL`           | `INFO`                       | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
