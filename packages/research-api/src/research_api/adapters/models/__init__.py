"""Adapter: OpenAI-compatible servers (vLLM models, the speech server), called with httpx."""

from .openai import OpenAiModels
from .speech import OpenAiTranscriber

__all__ = ["OpenAiModels", "OpenAiTranscriber"]
