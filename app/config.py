"""Settings: every configurable value of the app, read from environment variables or .env.

pydantic-settings matches each field below to the environment variable of the same name,
ignoring case: MODEL_TURN fills model_turn, NUM_QUESTIONS fills num_questions, and so on.
When a variable is not set, the default written here is used.

Other code calls get_settings() instead of reading os.environ directly. @lru_cache makes it
build the Settings object once and return the same one afterwards.

To change a model, a voice or a limit: set it in .env (e.g. NUM_QUESTIONS=3), then
`docker compose up -d --force-recreate api` so the container picks up the new value.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All config comes from env vars (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5433/interview"

    anthropic_api_key: str = ""
    model_turn: str = "claude-haiku-5-5"  # each interview turn
    model_reasoning: str = "claude-sonnet-5-5"  # brief + final feedback

    langsmith_project: str = "interview-prep-voice-agent"

    num_questions: int = 5  # planned main questions per session
    max_followups: int = 2  # follow-ups allowed per main question

    # Speech (Phase 2). Providers sit behind interfaces in app/voice/.
    stt_provider: str = "deepgram"  # speech-to-text
    deepgram_api_key: str = ""
    stt_model: str = "nova-3"
    tts_provider: str = "elevenlabs"  # text-to-speech
    elevenlabs_api_key: str = ""
    tts_model: str = "eleven_flash_v2_5"  # low latency; speaks en, fr, es
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # a premade voice; any voice id works
    tts_output_format: str = "mp3_44100_64"
    save_audio: bool = False  # keep each recorded answer in evals/audio/ for replay tests

    # Documents search (Phase 3). Embeddings sit behind an interface in app/rag/embeddings.py.
    embedding_provider: str = "voyage"
    voyage_api_key: str = ""
    embedding_model: str = "voyage-4"  # 1024 numbers per text, multilingual
    retrieval_k: int = 3  # document passages added to each question

    # Accounts (Phase 4.6). The login cookie: how long it lasts, and whether the browser may send
    # it only over HTTPS. Keep cookie_secure false on http://localhost, set it true on AWS.
    login_days: int = 30
    cookie_secure: bool = False
    # Guessing limits (app/auth/limits.py): failed logins per email and per IP address in a
    # window, and registration attempts per IP address per hour.
    login_max_failures: int = 5
    login_max_failures_per_ip: int = 20
    login_window_minutes: int = 15
    register_max_per_hour: int = 10

    @property
    def psycopg_url(self) -> str:
        """Plain libpq URL (no SQLAlchemy driver suffix) for psycopg / the checkpointer."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
