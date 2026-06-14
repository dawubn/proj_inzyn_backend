from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    APP_ENV: str = "development"
    APP_DEBUG: bool = False
    APP_SECRET_KEY: str
    APP_ALLOWED_HOSTS: list[str] = ["*"]

    # Database
    DATABASE_URL: str
    POSTGRES_USER: str = "cerberdoc"
    POSTGRES_PASSWORD: str = "cerberdoc"
    POSTGRES_DB: str = "cerber_doc"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432

    # Redis / Celery
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"

    # JWT
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Azure AI Document Intelligence
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT: str
    AZURE_DOCUMENT_INTELLIGENCE_KEY: str

    # Azure OpenAI (for legal analysis)
    AZURE_OPENAI_ENDPOINT: str | None = None
    AZURE_OPENAI_KEY: str | None = None

    # File storage
    STORAGE_PATH: str = "/app/storage"
    MAX_UPLOAD_SIZE_MB: int = 20
    ALLOWED_EXTENSIONS: list[str] = ["pdf", "jpg", "jpeg", "png"]

    # Logging
    LOG_LEVEL: str = "INFO"

    # Local OCR / Redaction
    LOCAL_OCR_LANG: str = "pol+eng"
    LOCAL_OCR_DPI: int = 300
    LOCAL_OCR_MIN_CONFIDENCE: float = 30.0
    REDACTION_BOX_PADDING_PX: int = 8
    REDACTION_TIMEOUT_SECONDS: int = 120

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql"):
            raise ValueError("Only PostgreSQL is supported")
        return v

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


settings = Settings()
