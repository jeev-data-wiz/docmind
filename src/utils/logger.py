"""
Structured logger — writes human-readable logs to stdout and JSON traces
to logs/agent_traces.jsonl for observability.
"""

import logging
import json
import time
import os
from typing import Any, Dict, Optional


def get_logger(name: str) -> logging.Logger:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level, logging.INFO))
    return logger


class TraceLogger:
    """Writes structured JSONL traces for agent steps — supports observability."""

    def __init__(self, trace_file: str = "logs/agent_traces.jsonl"):
        self.trace_file = trace_file
        os.makedirs(os.path.dirname(trace_file), exist_ok=True)

    def log(self, event_type: str, data: Dict[str, Any], query: Optional[str] = None):
        record = {
            "ts": time.time(),
            "event": event_type,
            "query": query,
            **data,
        }
        with open(self.trace_file, "a") as f:
            f.write(json.dumps(record) + "\n")
