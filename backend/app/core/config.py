from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    database_url: str  # asyncpg URI

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    groq_api_key: str = ""
    gemini_api_key: str = ""
    llm_provider: str = "groq"   # "groq" | "gemini"
    llm_model: str = "llama-3.3-70b-versatile"

    # Security
    webhook_secret: str

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    @property
    def sync_database_url(self) -> str:
        """Alembic requires a synchronous driver; swap asyncpg → psycopg2."""
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
