from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, read from environment variables."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql://postgres:postgres@db:5432/shift"
    jwt_secret: str = "dev-only-secret-change-me-in-production-0001"
    jwt_ttl_min: int = 480
    dev_auth: bool = True
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    llm_provider: Literal["auto", "groq", "offline", "fake"] = "auto"
    demo_start: str = "17:30"  # "HH:MM" local time the demo clock starts at; "" = real clock
    seed_on_start: bool = True
    turn_budget_s: float = 4.5
    static_dir: str = "/app/static"


@lru_cache
def get_settings() -> Settings:
    return Settings()
