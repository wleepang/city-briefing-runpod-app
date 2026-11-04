"""Configuration comes from the environment. Secrets (LLM_API_KEY) never reach the browser."""
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "none"
    model: str = "llama3.1:8b"
    max_tokens: int = 1024  # reasoning models spend part of this on hidden thinking
    timeout_s: float = 240.0  # longest wait for data from the model (a serverless cold start is 80-90s)


@dataclass(frozen=True)
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    llm_daily_cap: int = 500  # global cap on LLM calls per 24h across all users; 0 = unlimited
    geo_ttl_s: float = 60 * 60
    weather_ttl_s: float = 10 * 60
    wiki_ttl_s: float = 60 * 60


def load_config(env=None) -> Config:
    env = os.environ if env is None else env
    llm = LLMConfig()
    return Config(
        llm=LLMConfig(
            base_url=env.get("LLM_BASE_URL", llm.base_url).rstrip("/"),
            api_key=env.get("LLM_API_KEY", llm.api_key),
            model=env.get("LLM_MODEL", llm.model),
            max_tokens=int(env.get("LLM_MAX_TOKENS") or llm.max_tokens),
            timeout_s=int(env.get("LLM_TIMEOUT_MS") or llm.timeout_s * 1000) / 1000,
        ),
        llm_daily_cap=int(env.get("LLM_DAILY_CAP") or Config.llm_daily_cap),
    )
