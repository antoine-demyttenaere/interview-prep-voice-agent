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
    # Login codes (Phase 4.7, app/auth/codes.py). secret_key signs the stored codes (HMAC): set
    # it in .env to a long random value; without it a random one is made at each start, so codes
    # sent before a restart stop working.
    secret_key: str = ""
    code_minutes: int = 10  # how long a code stays valid
    code_max_tries: int = 5  # wrong tries before a code stops working
    # Guessing limits (app/auth/limits.py), in a window of login_window_minutes: codes sent per
    # email and per IP address, and wrong codes per email and per IP address.
    login_window_minutes: int = 15
    codes_per_email: int = 3
    codes_per_ip: int = 10
    login_max_failures: int = 5
    login_max_failures_per_ip: int = 20
    # Email (app/mail/): "console" prints each email in the server logs (local use).
    mail_provider: str = "console"
    mail_from: str = "Interview Practice <no-reply@localhost>"

    # Cost control (Phase 4.5, app/costs/budget.py). Budgets in US dollars for all users
    # together; at budget_warn_ratio (80%) the page warns the admin, at 100% new interviews
    # are refused (one already running still finishes). The day is a UTC calendar day.
    budget_monthly_usd: float = 10.0
    budget_daily_usd: float = 1.5
    budget_claude_usd: float = 4.5
    budget_elevenlabs_usd: float = 4.0
    budget_deepgram_usd: float = 1.0
    budget_voyage_usd: float = 0.5
    budget_warn_ratio: float = 0.8
    # The practice (Phase 4.7): its own budget inside the global one, so sign-ups trying the app
    # can't use up the interviews' budget; and its shape (fewer questions, Haiku feedback).
    budget_practice_monthly_usd: float = 3.0
    budget_practice_daily_usd: float = 0.75
    practice_questions: int = 3
    # Limits per user, per session and per input (plan.md, "Cost control").
    interviews_per_day: int = 4  # per user: interviews whose questions were planned today
    brief_revisions_max: int = 3  # "ask for changes" per session
    max_questions: int = 8  # questions per session (num_questions is the default)
    max_recording_seconds: int = 180  # the page stops recording an answer after this
    max_recording_bytes: int = 5_000_000  # the server refuses bigger recordings
    max_spoken_chars: int = 600  # longer questions are shown as text, not spoken
    max_documents_per_interview: int = 10
    max_document_chars_per_interview: int = 200_000
    max_web_pages_per_interview: int = 3
    max_cv_chars: int = 50_000

    @property
    def psycopg_url(self) -> str:
        """Plain libpq URL (no SQLAlchemy driver suffix) for psycopg / the checkpointer."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
