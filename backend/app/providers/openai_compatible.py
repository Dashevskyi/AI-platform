import json
import logging
import httpx

from app.providers.base import BaseProvider, LLMResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, base_url: str = "https://api.openai.com", api_key: str | None = None):
        super().__init__(base_url, api_key)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def format_assistant_turn(self, resp: LLMResponse) -> dict:
        msg = super().format_assistant_turn(resp)
        # DeepSeek-Reasoner (and similar OpenAI-compatible thinking models)
        # require `reasoning_content` echoed back. Stripping it triggers
        # 400 "reasoning_content in the thinking mode must be passed back".
        if resp.reasoning:
            msg["reasoning_content"] = resp.reasoning
        return msg

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
        on_chunk=None,
        extra_body: dict | None = None,
    ) -> LLMResponse:
        if on_chunk is not None:
            return await self._chat_completion_stream(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                on_chunk=on_chunk,
                extra_body=extra_body,
            )
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if extra_body:
            payload.update(extra_body)

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
        # Reasoning: DeepSeek (`reasoning_content`), some OpenAI-compatible
        # providers/proxies (`reasoning`), or inline <think>...</think> blocks.
        reasoning = message.get("reasoning_content") or message.get("reasoning") or None
        if not reasoning and isinstance(content, str) and "<think>" in content:
            import re
            think_blocks = re.findall(r"<think>([\s\S]*?)</think>", content, flags=re.IGNORECASE)
            if think_blocks:
                reasoning = "\n\n".join(b.strip() for b in think_blocks).strip() or None
                content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()

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
            reasoning=reasoning,
            raw_response=data,
        )

    async def _chat_completion_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict] | None,
        on_chunk,
        extra_body: dict | None = None,
    ) -> LLMResponse:
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            # Ask provider to also send a final usage chunk
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        if extra_body:
            payload.update(extra_body)

        accumulated_content: list[str] = []
        accumulated_reasoning: list[str] = []
        # Tool calls come incrementally with `index`; arguments are appended as string fragments.
        tool_calls_buf: dict[int, dict] = {}
        finish_reason: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                self._completions_url(),
                headers=self._headers(),
                json=payload,
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace")[:1000]
                    logger.error(
                        "openai stream %s %s body=%r",
                        response.status_code,
                        self._completions_url(),
                        detail,
                    )
                    raise httpx.HTTPStatusError(
                        f"OpenAI stream HTTP {response.status_code}: {detail}",
                        request=response.request,
                        response=response,
                    )
                async for raw_line in response.aiter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].lstrip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("openai stream: bad json chunk: %r", data_str[:120])
                        continue

                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage.get("completion_tokens", completion_tokens)
                        total_tokens = usage.get("total_tokens", total_tokens)

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    # Content
                    content_delta = delta.get("content") or ""
                    if content_delta:
                        accumulated_content.append(content_delta)
                        await on_chunk({"type": "content", "text": content_delta})
                    # Reasoning (DeepSeek `reasoning_content`, some proxies `reasoning`)
                    reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if reasoning_delta:
                        accumulated_reasoning.append(reasoning_delta)
                        await on_chunk({"type": "reasoning", "text": reasoning_delta})
                    # Tool calls (incremental)
                    for tc_delta in delta.get("tool_calls") or []:
                        idx = tc_delta.get("index", 0)
                        slot = tool_calls_buf.setdefault(
                            idx,
                            {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if tc_delta.get("id"):
                            slot["id"] = tc_delta["id"]
                        if tc_delta.get("type"):
                            slot["type"] = tc_delta["type"]
                        fn_delta = tc_delta.get("function") or {}
                        if fn_delta.get("name"):
                            slot["function"]["name"] += fn_delta["name"]
                        if fn_delta.get("arguments"):
                            slot["function"]["arguments"] += fn_delta["arguments"]
                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = fr

        full_content = "".join(accumulated_content)
        full_reasoning = "".join(accumulated_reasoning) or None

        # Some providers emit <think>...</think> inline in content even in streaming.
        # If so, extract them as reasoning and clean content.
        if not full_reasoning and "<think>" in full_content:
            import re
            think_blocks = re.findall(r"<think>([\s\S]*?)</think>", full_content, flags=re.IGNORECASE)
            if think_blocks:
                full_reasoning = "\n\n".join(b.strip() for b in think_blocks).strip() or None
                full_content = re.sub(r"<think>[\s\S]*?</think>", "", full_content, flags=re.IGNORECASE).strip()

        tool_calls = [tool_calls_buf[i] for i in sorted(tool_calls_buf.keys())] if tool_calls_buf else None

        return LLMResponse(
            content=full_content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            reasoning=full_reasoning,
            raw_response=None,
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
