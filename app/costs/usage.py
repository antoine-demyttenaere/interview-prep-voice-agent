"""Usage recording: writes one row to the usage table for every paid API call, with its cost.

Called right after each successful paid call: record_claude() from app/llm.py, and
record_units() from app/voice/stt.py (Deepgram), app/voice/tts.py (ElevenLabs) and
app/rag/embeddings.py (Voyage). Prices come from app/costs/pricing.py (pricing.yaml).

Whose call is it? Those functions don't know which user or session they work for: they run deep
inside the graph or the voice turn. So the API routes wrap their work in

    with usage.attribute(user_id, session_id):
        ...  # graph.invoke(...), transcribe(...), ingest(...)

attribute() stores the ids in a ContextVar, a variable private to the current request (each
request, thread or task sees its own value; LangGraph copies it into the threads that run the
nodes). record_*() reads it. Calls made outside any attribute() block (seeding the question bank,
evaluations, the smoke test) are recorded with no user: they still count in the global budget.

Recording must never break an interview: if writing the row fails, it logs a warning and the
call's result is still used.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy.orm import Session as DB

from app.costs import pricing
from app.db.models import Usage
from app.db.session import get_engine

log = logging.getLogger(__name__)

_who: ContextVar[tuple] = ContextVar("usage_attribution", default=(None, None))


@contextmanager
def attribute(user_id=None, session_id=None):
    """Inside this block, every paid call is recorded for this user and session."""
    token = _who.set((user_id, session_id))
    try:
        yield
    finally:
        _who.reset(token)


def current() -> tuple:
    """(user_id, session_id) of the calls made right now, or (None, None)."""
    return _who.get()


def _write(provider: str, model: str, unit: str, quantity: float, cost: float, details: dict) -> None:
    user_id, session_id = _who.get()
    try:
        with DB(get_engine()) as db:
            db.add(Usage(provider=provider, model=model, unit=unit, quantity=quantity,
                         cost_usd=round(cost, 6), details=details, user_id=user_id, session_id=session_id))
            db.commit()
    except Exception as exc:  # never let bookkeeping stop the interview
        log.warning("Could not record %s usage (%s %s): %s", provider, quantity, unit, exc)


def record_claude(model: str, usage: dict) -> float:
    """One Claude response. `usage` has the four token counts of response.usage. Returns the cost."""
    cost = pricing.claude_cost(model, usage)
    total = sum(usage.get(k) or 0 for k in
                ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    _write("claude", model, "tokens", total, cost, usage)
    return cost


def record_units(provider: str, model: str, unit: str, quantity: float) -> float:
    """One Deepgram (seconds), ElevenLabs (characters) or Voyage (tokens) call. Returns the cost."""
    cost = pricing.unit_cost(provider, model, quantity)
    _write(provider, model, unit, quantity, cost, {})
    return cost
