import json
import logging
import re

import httpx

from app.providers.base import BaseProvider, LLMResponse

logger = logging.getLogger(__name__)

# Pattern to strip <think>...</think> blocks from model output
_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL)


def _raise_ollama_http_error(response: httpx.Response) -> None:
    """Raise a clearer error that includes Ollama's response body."""
    try:
        payload = response.json()
        detail = payload.get("error") or payload
    except Exception:
        detail = response.text.strip()

    detail_text = str(detail).strip() if detail is not None else ""
    message = f"Ollama API {response.status_code}"
    if detail_text:
        message += f": {detail_text}"

    raise RuntimeError(message)


class OllamaProvider(BaseProvider):
    def __init__(self, base_url: str = "http://localhost:11434", api_key: str | None = None):
        super().__init__(base_url, api_key)

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        on_chunk=None,
        extra_body: dict | None = None,  # noqa: ARG002 — accepted for interface parity, ignored
    ) -> LLMResponse:
        if on_chunk is not None:
            return await self._chat_completion_stream(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                on_chunk=on_chunk,
            )
        payload: dict = {
            "model": model.strip(),
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": False,
            "keep_alive": -1,  # never unload from RAM
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            if response.is_error:
                _raise_ollama_http_error(response)
            data = response.json()

        message = data.get("message", {})
        raw_content = message.get("content", "")

        # Capture <think>...</think> blocks (Qwen3 thinking mode) as reasoning,
        # then strip them from the user-visible content.
        reasoning_parts = _THINK_BLOCK_RE.findall(raw_content)
        content = _THINK_BLOCK_RE.sub("", raw_content)
        if "<think>" in content:
            # Unclosed think (truncated): keep as reasoning, drop trailing tail.
            tail = content[content.index("<think>") + len("<think>"):]
            if tail.strip():
                reasoning_parts.append(tail)
            content = content[:content.index("<think>")]
        content = content.strip()
        # Some Ollama builds also expose `thinking` field directly.
        thinking_field = message.get("thinking")
        if thinking_field:
            reasoning_parts.append(str(thinking_field))
        reasoning = "\n\n".join(p.strip() for p in reasoning_parts if p and p.strip()).strip() or None

        prompt_tokens = data.get("prompt_eval_count")
        completion_tokens = data.get("eval_count")
        total_tokens = None
        if prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens

        finish_reason = data.get("done_reason")
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
    ) -> LLMResponse:
        payload: dict = {
            "model": model.strip(),
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": True,
            "keep_alive": -1,
        }
        if tools:
            payload["tools"] = tools

        accumulated_content: list[str] = []
        accumulated_thinking: list[str] = []
        accumulated_tool_calls = None  # ollama returns tool_calls whole, not incrementally
        finish_reason: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    try:
                        detail = json.loads(body.decode("utf-8")).get("error") or body.decode("utf-8")
                    except Exception:
                        detail = body.decode("utf-8", errors="replace")
                    raise RuntimeError(f"Ollama API {response.status_code}: {detail}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("ollama stream: bad json chunk: %r", line[:120])
                        continue

                    msg = chunk.get("message") or {}
                    delta_content = msg.get("content") or ""
                    if delta_content:
                        accumulated_content.append(delta_content)
                        await on_chunk({"type": "content", "text": delta_content})
                    delta_thinking = msg.get("thinking") or ""
                    if delta_thinking:
                        accumulated_thinking.append(delta_thinking)
                        await on_chunk({"type": "reasoning", "text": delta_thinking})
                    tcs = msg.get("tool_calls")
                    if tcs:
                        accumulated_tool_calls = tcs

                    if chunk.get("done"):
                        finish_reason = chunk.get("done_reason") or finish_reason
                        if chunk.get("prompt_eval_count") is not None:
                            prompt_tokens = chunk["prompt_eval_count"]
                        if chunk.get("eval_count") is not None:
                            completion_tokens = chunk["eval_count"]

        # Post-process: capture <think> blocks from streamed content as reasoning
        raw_content = "".join(accumulated_content)
        reasoning_parts = list(accumulated_thinking)
        # Extract complete <think>...</think> blocks
        for m in _THINK_BLOCK_RE.findall(raw_content):
            reasoning_parts.append(m)
        cleaned = _THINK_BLOCK_RE.sub("", raw_content)
        # Handle unclosed <think> at the end
        if "<think>" in cleaned:
            tail = cleaned[cleaned.index("<think>") + len("<think>"):]
            if tail.strip():
                reasoning_parts.append(tail)
            cleaned = cleaned[:cleaned.index("<think>")]
        reasoning = "\n\n".join(p.strip() for p in reasoning_parts if p and p.strip()).strip() or None

        total_tokens = None
        if prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens

        return LLMResponse(
            content=cleaned.strip(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            tool_calls=accumulated_tool_calls,
            reasoning=reasoning,
            raw_response=None,
        )

    def format_tool_result_turn(self, *, tool_call_id: str | None, content: str) -> dict:
        # Ollama matches tool results to calls by order — no tool_call_id needed.
        _ = tool_call_id
        return {"role": "tool", "content": content}

    async def embed(self, text: str | list[str], model: str) -> list[list[float]]:
        inputs = [text] if isinstance(text, str) else text
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": model.strip(), "input": inputs},
            )
            if response.is_error:
                _raise_ollama_http_error(response)
            data = response.json()
        return data["embeddings"]

    async def healthcheck(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/")
                return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()

        models = data.get("models", [])
        return [m["name"] for m in models if "name" in m]
