"""Embeddings: turns texts into vectors (lists of 1024 numbers) so similar meanings are close together.

- Embedder: the interface. Any provider is a class with embed(texts, input_type).
- VoyageEmbedder: Voyage AI (POST https://api.voyageai.com/v1/embeddings), model voyage-4.
  Anthropic has no embedding model and recommends Voyage.
  input_type matters: "document" for chunks being stored, "query" for the search text. Voyage
  adds a different hidden instruction to each, which makes the search more accurate.
  Voyage vectors have length 1, so cosine similarity is simply their dot product.
- get_embedder(): returns the provider chosen by EMBEDDING_PROVIDER (created once).

Texts are sent in batches of 128, as Voyage recommends. Rate limits: a Voyage account without a
payment method is limited to 3 requests and 10K tokens per minute. When Voyage answers 429
("too many requests"), document embedding waits and retries (up to about a minute); a search
query does not wait, so an interview turn is never held up: the search is skipped instead.
Each Voyage request is recorded with its token count and cost (app/costs/usage.py, Phase 4.5).
"""

import time
from functools import lru_cache
from typing import Literal, Protocol

import httpx

from app.costs import usage

from app.config import get_settings

DIMENSIONS = 1024  # must match vector(1024) in the database (migration 0004)
InputType = Literal["document", "query"]


class EmbeddingError(RuntimeError):
    """The embedding provider failed or is not configured."""


class Embedder(Protocol):
    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]: ...


class VoyageEmbedder:
    URL = "https://api.voyageai.com/v1/embeddings"
    BATCH = 128
    RATE_LIMIT_RETRIES = 3  # for documents only
    RATE_LIMIT_WAIT_S = 21  # a free account without a payment method allows 3 requests per minute

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not set in .env")
        self.model = model
        self.client = httpx.Client(timeout=60.0, headers={"Authorization": f"Bearer {api_key}"})

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH):
            batch = texts[start:start + self.BATCH]
            # Documents can wait for a rate limit to clear; a search during an interview cannot.
            retries = self.RATE_LIMIT_RETRIES if input_type == "document" else 0
            for attempt in range(retries + 1):
                try:
                    resp = self.client.post(self.URL, json={
                        "input": batch,
                        "model": self.model,
                        "input_type": input_type,
                        "output_dimension": DIMENSIONS,
                    })
                except httpx.HTTPError as exc:
                    raise EmbeddingError(f"Voyage unreachable: {exc}") from exc
                if resp.status_code == 429 and attempt < retries:
                    time.sleep(self.RATE_LIMIT_WAIT_S)
                    continue
                break
            if resp.status_code != 200:
                raise EmbeddingError(f"Voyage error {resp.status_code}: {resp.text[:300]}")
            body = resp.json()
            usage.record_units("voyage", self.model, "tokens", float(body.get("usage", {}).get("total_tokens") or 0))
            data = sorted(body["data"], key=lambda d: d["index"])
            vectors.extend(d["embedding"] for d in data)
        return vectors


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "voyage":
        return VoyageEmbedder(s.voyage_api_key, s.embedding_model)
    raise EmbeddingError(f"Unknown EMBEDDING_PROVIDER '{s.embedding_provider}'")
