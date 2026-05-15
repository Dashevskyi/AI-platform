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
    reasoning: str | None = None
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
        on_chunk=None,
        extra_body: dict | None = None,
    ) -> LLMResponse:
        """
        If `on_chunk` is provided, the provider streams the response and calls
        the callback for each delta with `{"type": "content"|"reasoning", "text": str}`.
        The final return value still aggregates the full response.
        """
        ...

    # ----- Multi-turn message format helpers -----
    # Each provider can override to inject provider-specific fields when
    # echoing the assistant turn back (e.g. DeepSeek requires reasoning_content;
    # OpenAI o-series may require encrypted_reasoning; some local models use
    # `thinking`). Default = OpenAI/Ollama-shaped without reasoning.

    def format_assistant_turn(self, resp: "LLMResponse") -> dict:
        """Return the assistant message dict to append to `messages` after a
        provider response that requested tool calls (or to feed into the next
        turn). Subclasses extend with provider-specific fields."""
        msg: dict = {"role": "assistant", "content": resp.content or ""}
        if resp.tool_calls:
            msg["tool_calls"] = resp.tool_calls
        return msg

    def format_tool_result_turn(self, *, tool_call_id: str | None, content: str) -> dict:
        """Return the `role: tool` message after a tool execution.
        Default uses OpenAI shape with tool_call_id; Ollama omits the id."""
        msg: dict = {"role": "tool", "content": content}
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        return msg

    @abstractmethod
    async def healthcheck(self) -> bool: ...

    @abstractmethod
    async def list_models(self) -> list[str]: ...

    async def summarize(self, text: str, model: str, language_hint: str | None = None) -> str:
        """Generate a short chat title in the user's language."""
        language_clause = ""
        if language_hint:
            language_clause = f" Write the title in {language_hint}."
        resp = await self.chat_completion(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Generate a short chat title based on the user's message. "
                        "Use 3-7 words. Return ONLY the title, with no quotes, labels, or extra text."
                        f"{language_clause}\n\n"
                        + text
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
