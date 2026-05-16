"""
LLM Client
==========
Thin wrapper around LLM provider APIs.

Supports:
  • anthropic  (Claude 3.5 Haiku — fast, cheap, excellent reasoning)
  • openai     (GPT-4o-mini / GPT-4o)

Design decision: use a unified LLMClient interface.  The agent and query
engine call llm.complete(messages) without caring which provider is active.

Why Claude 3.5 Haiku as default?
  • Very low latency (ideal for multi-step agent loops)
  • Strong instruction-following for RAG prompts
  • Generous context window (200k tokens)
  • Cost-effective for prototype workloads
"""

import os
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class LLMBase(ABC):
    @abstractmethod
    def complete(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> str:
        ...


class AnthropicClient(LLMBase):
    def __init__(self, model: str = "claude-3-5-haiku-20241022", api_key: str = ""):
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic not installed. Run: pip install anthropic")
        key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        import anthropic as _anthropic
        self._client = _anthropic.Anthropic(api_key=key)
        self._model = model

    def complete(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> str:
        t0 = time.time()
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)
        latency_ms = (time.time() - t0) * 1000
        text = response.content[0].text
        logger.debug(
            f"LLM call: {latency_ms:.0f}ms | "
            f"in={response.usage.input_tokens} | "
            f"out={response.usage.output_tokens} tokens"
        )
        return text


class OpenAIClient(LLMBase):
    def __init__(self, model: str = "gpt-4o-mini", api_key: str = ""):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai not installed. Run: pip install openai")
        key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        from openai import OpenAI as _OpenAI
        self._client = _OpenAI(api_key=key)
        self._model = model

    def complete(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> str:
        t0 = time.time()
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        response = self._client.chat.completions.create(
            model=self._model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = (time.time() - t0) * 1000
        text = response.choices[0].message.content
        logger.debug(f"LLM call: {latency_ms:.0f}ms")
        return text


def build_llm(provider: str, model: str, anthropic_api_key: str = "", openai_api_key: str = "") -> LLMBase:
    if provider == "anthropic":
        return AnthropicClient(model=model, api_key=anthropic_api_key)
    elif provider == "openai":
        return OpenAIClient(model=model, api_key=openai_api_key)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}. Choose 'anthropic' or 'openai'.")
