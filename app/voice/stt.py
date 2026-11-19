"""Speech-to-text: turns your recorded answer (browser audio) into text for the graph.

- SpeechToText: the interface. Any provider is a class with transcribe(audio, mime_type, language).
- DeepgramSTT: Deepgram's pre-recorded API. The whole recording is sent in one HTTP request
  (POST https://api.deepgram.com/v1/listen) and the transcript comes back as JSON.
  model=nova-3 handles English, French and Spanish; smart_format adds punctuation, numbers
  and capitals so the transcript reads like normal text.
- get_stt(): returns the provider chosen by STT_PROVIDER (created once, then reused).

The browser records WebM/Opus (Chrome, Edge), Ogg (Firefox) or MP4 (Safari); Deepgram detects
the format itself, we only pass the Content-Type. Streaming recognition (words appearing while
you speak) is Phase 6.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import get_settings
from app.voice import SpeechError


@dataclass
class Transcript:
    text: str
    confidence: float | None  # 0-1, how sure the provider is; None if unknown


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes, mime_type: str, language: str) -> Transcript: ...


class DeepgramSTT:
    URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise SpeechError("DEEPGRAM_API_KEY is not set in .env")
        self.model = model
        self.client = httpx.Client(timeout=30.0, headers={"Authorization": f"Token {api_key}"})

    def transcribe(self, audio: bytes, mime_type: str, language: str) -> Transcript:
        content_type = (mime_type or "").split(";")[0].strip() or "application/octet-stream"
        try:
            resp = self.client.post(
                self.URL,
                params={"model": self.model, "language": language, "smart_format": "true", "punctuate": "true"},
                content=audio,
                headers={"Content-Type": content_type},
            )
        except httpx.HTTPError as exc:
            raise SpeechError(f"Deepgram unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise SpeechError(f"Deepgram error {resp.status_code}: {resp.text[:300]}")
        alt = resp.json()["results"]["channels"][0]["alternatives"][0]
        return Transcript(text=(alt.get("transcript") or "").strip(), confidence=alt.get("confidence"))


@lru_cache
def get_stt() -> SpeechToText:
    s = get_settings()
    if s.stt_provider == "deepgram":
        return DeepgramSTT(s.deepgram_api_key, s.stt_model)
    raise SpeechError(f"Unknown STT_PROVIDER '{s.stt_provider}'")
