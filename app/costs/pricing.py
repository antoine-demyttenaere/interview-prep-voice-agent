"""Prices: reads pricing.yaml and turns what one call used into dollars.

Used by app/costs/usage.py (every paid call in the app) and evals/answer_scoring/run.py (the
cost of an evaluation run). The file is read again whenever it changes on disk, so an edited
price applies to the next call without a restart.

- claude_cost(model, usage): the four kinds of tokens of one Claude response, each at its price.
- unit_cost(provider, model, quantity): Deepgram (minutes), ElevenLabs (thousands of
  characters), Voyage (millions of tokens).
A model that isn't listed uses the provider's "default" price and logs a warning, so a new
model is never counted as free.
"""

import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

PRICING_FILE = Path(__file__).resolve().parents[2] / "pricing.yaml"
_cache: dict = {"mtime": None, "data": None}


def prices() -> dict:
    mtime = PRICING_FILE.stat().st_mtime
    if _cache["mtime"] != mtime:
        _cache.update(mtime=mtime, data=yaml.safe_load(PRICING_FILE.read_text(encoding="utf-8")))
    return _cache["data"]


def _price_for(provider: str, model: str):
    table = prices()[provider]
    matches = [key for key in table if key != "default" and model.startswith(key)]
    if matches:
        return table[max(matches, key=len)]
    log.warning("No price for %s model %r in pricing.yaml: using its default price", provider, model)
    return table["default"]


def claude_cost(model: str, usage: dict) -> float:
    """Dollars for one Claude response. `usage` has input_tokens, output_tokens,
    cache_creation_input_tokens and cache_read_input_tokens (missing ones count as 0)."""
    p = _price_for("claude", model)
    tokens = {
        "input": usage.get("input_tokens") or 0,
        "cache_write": usage.get("cache_creation_input_tokens") or 0,
        "cache_read": usage.get("cache_read_input_tokens") or 0,
        "output": usage.get("output_tokens") or 0,
    }
    long = p.get("long_context")
    if long and tokens["input"] + tokens["cache_write"] + tokens["cache_read"] > long["above_tokens"]:
        p = long
    return sum(tokens[kind] * p[kind] for kind in tokens) / 1_000_000


# How each speech / embedding price is quoted: dollars per this many units.
PER = {"deepgram": 60.0, "elevenlabs": 1_000.0, "voyage": 1_000_000.0}  # seconds, characters, tokens


def unit_cost(provider: str, model: str, quantity: float) -> float:
    """Dollars for `quantity` units: seconds of audio, characters, or tokens."""
    return quantity * float(_price_for(provider, model)) / PER[provider]
