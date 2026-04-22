from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    tool_calls: list | None = None
    raw_response: dict | None = field(default=None, repr=False)


class BaseProvider(ABC):
    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    async def healthcheck(self) -> bool: ...

    @abstractmethod
    async def list_models(self) -> list[str]: ...

    async def summarize(self, text: str, model: str) -> str:
        """Generate a short summary of the text."""
        resp = await self.chat_completion(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Summarize the following conversation in 5-10 words. "
                        "Return ONLY the summary, nothing else:\n\n" + text
                    ),
                }
            ],
            model=model,
            temperature=0.3,
            max_tokens=50,
        )
        return resp.content.strip()

    def normalize_usage(self, raw: dict) -> dict:
        """Extract and normalize token usage from a raw provider response."""
        return {
            "prompt_tokens": raw.get("prompt_tokens") or raw.get("prompt_eval_count"),
            "completion_tokens": raw.get("completion_tokens") or raw.get("eval_count"),
            "total_tokens": raw.get("total_tokens"),
        }

    async def embed(self, text: str | list[str], model: str) -> list[list[float]]:
        """Generate embeddings for text(s). Returns list of vectors."""
        raise NotImplementedError("Provider does not support embeddings")

    def normalize_error(self, error: Exception) -> dict:
        """Normalize an exception into a serializable dict."""
        return {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
