from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения или файла .env (см. .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://assistant:assistant@localhost:5432/assistant"
    redis_url: str = "redis://localhost:6379/0"

    # Часовой пояс, в котором трактуется время без явного смещения («завтра в 10»).
    default_tz: str = "Europe/Moscow"

    # GigaChat: ключ авторизации из личного кабинета developers.sber.ru.
    # Пусто — эмбеддинги выключены, поиск работает только по тексту.
    gigachat_credentials: str | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_verify_ssl: bool = False
    gigachat_embeddings_model: str = "Embeddings"

    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001
    mcp_path: str = "/mcp"

    api_cors_origins: list[str] = ["*"]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.default_tz)


@lru_cache
def get_settings() -> Settings:
    return Settings()
