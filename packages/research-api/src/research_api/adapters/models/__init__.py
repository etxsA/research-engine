"""Adapter: OpenAI-compatible servers (models, speech, reading), called with httpx."""

from .openai import OpenAiModels
from .speech import OpenAiTranscriber
from .synthesis import OpenAiSynthesizer

__all__ = ["OpenAiModels", "OpenAiSynthesizer", "OpenAiTranscriber"]
