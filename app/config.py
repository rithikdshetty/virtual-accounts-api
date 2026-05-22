from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    App config. Read from environment variables, with .env as fallback for
    local dev.

    Why pydantic-settings: type-checked config at startup. If anything
    required is missing or malformed, the app refuses to boot. Fail fast.
    """

    database_url: str
    test_database_url: str | None = None
    api_key: str
    api_version: str = "0.1.2"
    livemode: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
