from pydantic_settings import BaseSettings
from typing import List
import json


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://ai_platform:ai_platform_secret@localhost:5432/ai_platform"
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    ENCRYPTION_KEY: str = "change-me"
    CORS_ORIGINS: str = '["http://localhost:5173"]'
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    ADMIN_LOGIN: str = "admin"
    ADMIN_PASSWORD: str = "admin"
    LOG_RETENTION_DAYS: int = 90

    @property
    def cors_origins_list(self) -> List[str]:
        return json.loads(self.CORS_ORIGINS)

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
