from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    google_places_api_key: str = ""
    hunter_api_key: str = ""
    openrouter_api_key: str = ""  # primary LLM — free models available
    supabase_url: str = ""
    supabase_key: str = ""
    # Comma-separated list of allowed CORS origins; "*" = allow all (dev only)
    cors_origins: str = "*"

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )


settings = Settings()
