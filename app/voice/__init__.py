"""Voice layer: turns your speech into text, and the interviewer's text into speech.

- stt.py: speech-to-text (STT). Interface SpeechToText + the Deepgram implementation.
- tts.py: text-to-speech (TTS). Interface TextToSpeech + the ElevenLabs implementation.

Why interfaces: the rest of the app only calls get_stt().transcribe(...) and
get_tts().synthesize(...). To try another provider, write a new class with the same method and
select it with STT_PROVIDER / TTS_PROVIDER in .env; no other code changes.

SpeechError is raised when a provider fails (missing key, HTTP error); the API turns it into a
502 error with the provider's message.
"""


class SpeechError(RuntimeError):
    """A speech provider failed or is not configured."""


LANGUAGES = {"en": "English", "fr": "French", "es": "Spanish"}
