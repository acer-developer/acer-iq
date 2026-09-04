from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_places_api_key: str = ""
    hunter_api_key: str = ""
    openrouter_api_key: str = ""  # primary LLM — free models available
    # Fallback LLM. Any OpenAI-compatible endpoint works; used when OpenRouter
    # has no key or its free tier is rate-limited.
    tokenrouter_api_key: str = ""
    tokenrouter_base_url: str = "https://api.tokenrouter.com/v1"
    tokenrouter_model: str = "z-ai/glm-5.3-free"
    supabase_url: str = ""
    supabase_key: str = ""
    # Comma-separated exact origins. Empty = fall back to the regex below
    # (any *.vercel.app deploy + localhost dev), which is what prod uses.
    allowed_origins: str = ""
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False
        # Retired keys (ANTHROPIC_API_KEY, GOOGLE_MAPS_API_KEY) are still set in
        # Render/.env — without this the app refuses to boot on unknown env vars.
        extra = "ignore"


settings = Settings()
