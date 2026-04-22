from app.providers.openai_compatible import OpenAICompatibleProvider


class DeepseekCompatibleProvider(OpenAICompatibleProvider):
    """Deepseek-compatible provider.

    Deepseek uses the same API format as OpenAI, so this class inherits
    everything from OpenAICompatibleProvider with a different default base URL.
    """

    def __init__(self, base_url: str = "https://api.deepseek.com", api_key: str | None = None):
        super().__init__(base_url, api_key)
