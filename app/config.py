"""App settings loaded from env vars / .env: database URL, API keys, model IDs."""

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
