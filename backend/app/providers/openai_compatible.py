import httpx

from app.providers.base import BaseProvider, LLMResponse


class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, base_url: str = "https://api.openai.com", api_key: str | None = None):
        super().__init__(base_url, api_key)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _completions_url(self) -> str:
        """Return the chat completions endpoint URL.

        Handles providers that already include /v1 in their base_url.
        """
        if self.base_url.endswith("/v1") or "/v1/" in self.base_url:
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def _models_url(self) -> str:
        if self.base_url.endswith("/v1") or "/v1/" in self.base_url:
            return f"{self.base_url}/models"
        return f"{self.base_url}/v1/models"

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                self._completions_url(),
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "") or ""

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        finish_reason = choice.get("finish_reason")
        tool_calls = message.get("tool_calls") or None

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            raw_response=data,
        )

    def _embeddings_url(self) -> str:
        if self.base_url.endswith("/v1") or "/v1/" in self.base_url:
            return f"{self.base_url}/embeddings"
        return f"{self.base_url}/v1/embeddings"

    async def embed(self, text: str | list[str], model: str) -> list[list[float]]:
        inputs = [text] if isinstance(text, str) else text
        payload = {"model": model, "input": inputs}
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                self._embeddings_url(),
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return [item["embedding"] for item in data["data"]]

    async def healthcheck(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    self._models_url(),
                    headers=self._headers(),
                )
                return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                self._models_url(),
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()

        models = data.get("data", [])
        return [m["id"] for m in models if "id" in m]
