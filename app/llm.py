"""Claude clients, wrapped so every call is traced in LangSmith."""

from functools import lru_cache

import anthropic
from langsmith.wrappers import wrap_anthropic


@lru_cache
def get_claude() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY; tracing is on when LANGSMITH_TRACING=true.
    return wrap_anthropic(anthropic.Anthropic())
