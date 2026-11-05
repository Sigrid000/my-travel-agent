from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    opentripmap_api_key: str = ""
    amap_api_key: str = ""  # 高德 Web 服务 Key（地理编码 / POI）
    nominatim_user_agent: str = "my-travel-agent/0.1 (local-dev; contact=dev@example.com)"
    tool_timeout_sec: float = 12.0
    max_agent_steps: int = 20
    max_tool_calls: int = 30
    trace_dir: str = "traces"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
