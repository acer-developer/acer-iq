from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_maps_api_key: str = ""
    google_places_api_key: str = ""
    hunter_api_key: str = ""
    anthropic_api_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
