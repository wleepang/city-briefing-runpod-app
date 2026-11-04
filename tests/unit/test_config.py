from app.config import load_config


def test_defaults_and_environment_overrides():
    cfg = load_config({})
    assert (cfg.llm.base_url, cfg.llm.max_tokens, cfg.llm.timeout_s, cfg.llm_daily_cap) == ("http://localhost:11434/v1", 1024, 240.0, 500)

    cfg = load_config({
        "LLM_BASE_URL": "https://api.runpod.ai/v2/abc/openai/v1/", "LLM_API_KEY": "k", "LLM_MODEL": "m",
        "LLM_MAX_TOKENS": "2048", "LLM_TIMEOUT_MS": "5000", "LLM_DAILY_CAP": "0",
    })
    assert cfg.llm.base_url == "https://api.runpod.ai/v2/abc/openai/v1"  # trailing slash removed
    assert (cfg.llm.api_key, cfg.llm.model, cfg.llm.max_tokens, cfg.llm.timeout_s) == ("k", "m", 2048, 5.0)
    assert cfg.llm_daily_cap == 0  # "0" means unlimited, not "use the default"
    assert load_config({"LLM_MAX_TOKENS": ""}).llm.max_tokens == 1024  # empty means default
