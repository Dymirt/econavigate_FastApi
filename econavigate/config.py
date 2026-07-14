from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Eco Navigate API"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"

    warsaw_api_token: SecretStr | None = None
    warsaw_api_url: str = "https://api.um.warszawa.pl/api/action"
    nominatim_api_url: str = "https://nominatim.openstreetmap.org"
    valhalla_api_url: str = "https://valhalla1.openstreetmap.de/route"
    project_user_agent: str = (
        "EcoNavigateFastAPI/0.1 (https://github.com/Dymirt/econavigate_FastApi)"
    )

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,https://warsaw-moss.vercel.app"

    cache_dir: Path = Path(".cache/econavigate")
    cache_size_limit_bytes: int = 1_073_741_824
    air_cache_ttl_seconds: int = 300
    route_cache_ttl_seconds: int = 600
    geocode_cache_ttl_seconds: int = 2_592_000
    greenery_cache_ttl_seconds: int = 604_800

    request_timeout_seconds: float = 70.0
    connect_timeout_seconds: float = 10.0
    http_max_connections: int = 30
    http_max_keepalive_connections: int = 15
    nominatim_min_interval_seconds: float = 1.1
    max_greenery_records: int = 50_000

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def warsaw_token(self) -> str | None:
        if self.warsaw_api_token is None:
            return None
        value = self.warsaw_api_token.get_secret_value().strip()
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
