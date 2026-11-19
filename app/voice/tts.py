"""Text-to-speech: turns what the interviewer says into audio the browser plays.

- TextToSpeech: the interface. Any provider is a class with synthesize(text, language) -> Speech.
- ElevenLabsTTS: ElevenLabs' convert API (POST /v1/text-to-speech/{voice_id}). It returns one
  complete MP3 file. The default model, eleven_flash_v2_5, is their low-latency model and speaks
  English, French and Spanish; language_code tells it which one to use.
- get_tts(): returns the provider chosen by TTS_PROVIDER (created once, then reused).

The voice is set by ELEVENLABS_VOICE_ID (a premade voice by default). Sentence-by-sentence
streaming, so the interviewer starts talking sooner, is Phase 6.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import get_settings
from app.voice import SpeechError


@dataclass
class Speech:
    audio: bytes
    mime_type: str  # e.g. "audio/mpeg", for the browser


class TextToSpeech(Protocol):
    def synthesize(self, text: str, language: str) -> Speech: ...


class ElevenLabsTTS:
    BASE = "https://api.elevenlabs.io/v1/text-to-speech"

    def __init__(self, api_key: str, voice_id: str, model: str, output_format: str):
        if not api_key:
            raise SpeechError("ELEVENLABS_API_KEY is not set in .env")
        self.voice_id, self.model, self.output_format = voice_id, model, output_format
        self.client = httpx.Client(timeout=30.0, headers={"xi-api-key": api_key})

    def synthesize(self, text: str, language: str) -> Speech:
        try:
            resp = self.client.post(
                f"{self.BASE}/{self.voice_id}",
                params={"output_format": self.output_format},
                json={"text": text, "model_id": self.model, "language_code": language},
            )
        except httpx.HTTPError as exc:
            raise SpeechError(f"ElevenLabs unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise SpeechError(f"ElevenLabs error {resp.status_code}: {resp.text[:300]}")
        mime = resp.headers.get("content-type", "").split(";")[0]
        if not mime.startswith("audio/"):
            mime = "audio/mpeg" if self.output_format.startswith("mp3") else "application/octet-stream"
        return Speech(audio=resp.content, mime_type=mime)


@lru_cache
def get_tts() -> TextToSpeech:
    s = get_settings()
    if s.tts_provider == "elevenlabs":
        return ElevenLabsTTS(s.elevenlabs_api_key, s.elevenlabs_voice_id, s.tts_model, s.tts_output_format)
    raise SpeechError(f"Unknown TTS_PROVIDER '{s.tts_provider}'")
