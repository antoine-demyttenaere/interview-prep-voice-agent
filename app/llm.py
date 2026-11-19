"""Claude helpers: every call to Claude in the app goes through this file.

- get_claude(): one shared Anthropic client, wrapped by LangSmith so every call is recorded
  (prompt, reply, tokens, time) inside the trace of the graph node that made it.
- structured(): asks for an answer that must match a Pydantic schema (e.g. InterviewBrief) and
  returns it as a validated Python object. Used for the brief, question plan, scores, feedback.
- text_reply(): asks for plain text. Used for what the interviewer says.

Both send the system prompt with cache_control: a long system prompt repeated on every turn
(like the brief) is cached by Claude, so later calls are cheaper and faster. `effort`
(low / medium / high) trades thinking depth for speed. Both raise LLMError when Claude refuses,
runs out of tokens or returns nothing usable; the API turns that into an error you can retry.
fallback=True (Sonnet only) lets the API retry on a backup model if Sonnet declines a request.
"""

from functools import lru_cache
from typing import TypeVar

import anthropic
from langsmith.wrappers import wrap_anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Server-side refusal fallback (Sonnet 5.5 / Opus / Fable only; not Haiku 5.5).
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """Claude returned no usable answer (refusal, truncation, unparsable output)."""


@lru_cache
def get_claude() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY; tracing is on when LANGSMITH_TRACING=true.
    return wrap_anthropic(anthropic.Anthropic())


def _system(text: str) -> list[dict]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _check(response, model: str) -> None:
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise LLMError(f"{model} refused: {details}")
    if response.stop_reason == "max_tokens":
        raise LLMError(f"{model} hit max_tokens; raise the limit for this call")


def structured(
    *,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    max_tokens: int,
    effort: str,
    fallback: bool = False,
) -> T:
    """One call whose answer is validated against a Pydantic schema."""
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=_system(system),
        messages=[{"role": "user", "content": user}],
        output_format=schema,
        output_config={"effort": effort},
    )
    if fallback:
        kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")

    response = get_claude().beta.messages.parse(**kwargs)
    _check(response, model)
    if response.parsed_output is None:
        raise LLMError(f"{model} returned no parsable {schema.__name__}")
    return response.parsed_output


def text_reply(*, model: str, system: str, user: str, max_tokens: int, effort: str) -> str:
    """One call that returns plain text (thinking blocks are skipped)."""
    response = get_claude().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_system(system),
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
    )
    _check(response, model)
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise LLMError(f"{model} returned an empty reply")
    return text
