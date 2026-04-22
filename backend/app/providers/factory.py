from app.providers.base import BaseProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.deepseek_compatible import DeepseekCompatibleProvider


def get_provider(
    provider_type: str,
    base_url: str | None = None,
    api_key: str | None = None,
) -> BaseProvider:
    """Instantiate the appropriate LLM provider based on provider_type."""
    if provider_type == "ollama":
        return OllamaProvider(base_url or "http://localhost:11434")
    elif provider_type == "openai_compatible":
        return OpenAICompatibleProvider(base_url or "https://api.openai.com", api_key)
    elif provider_type == "deepseek_compatible":
        return DeepseekCompatibleProvider(base_url or "https://api.deepseek.com", api_key)
    raise ValueError(f"Unknown provider: {provider_type}")
