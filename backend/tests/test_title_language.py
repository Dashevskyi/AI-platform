import asyncio

from app.providers.base import BaseProvider, LLMResponse


class DummyProvider(BaseProvider):
    def __init__(self):
        super().__init__("http://example.test")
        self.last_messages = None

    async def chat_completion(self, messages, model, temperature=0.7, max_tokens=4096, tools=None):
        self.last_messages = messages
        return LLMResponse(content="Тестовый заголовок")

    async def healthcheck(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return ["dummy"]


def test_summarize_requests_title_in_user_language():
    provider = DummyProvider()

    result = asyncio.run(provider.summarize(
        "User: Привет, у меня не работает API\nAssistant: Давай разберёмся",
        "dummy-model",
    ))

    assert result == "Тестовый заголовок"
    assert provider.last_messages is not None
    prompt = provider.last_messages[0]["content"]
    assert "SAME language as the user's message" in prompt
    assert "If the user wrote in Ukrainian, return Ukrainian" in prompt
    assert "If the user wrote in Russian, return Russian" in prompt
    assert "Return ONLY the title" in prompt
