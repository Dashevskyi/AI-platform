"""Mock-provider harness for the chat pipeline.

Drives the real _chat_completion_inner against a seeded tenant with a fake
provider that records the assembled prompt — no LLM server needed. This is the
safety net for refactoring the pipeline: it pins what the model actually
receives (static system blocks, the user's message) and the response shape.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.database import async_session
from app.providers.base import BaseProvider, LLMResponse
from app.services.llm import pipeline as pl
from app.services.llm.model_resolver import ResolvedModel


class RecordingProvider(BaseProvider):
    """Captures the messages it's asked to complete and returns scripted replies.

    `scripted` is a list of LLMResponse returned one per call (e.g. a tool_call
    turn followed by a final answer); once exhausted it falls back to `reply`."""
    def __init__(self, reply="Готовый ответ.", scripted=None):
        super().__init__("http://mock.test")
        self.calls = []
        self._reply = reply
        self._scripted = list(scripted or [])

    async def chat_completion(self, messages, model, temperature=0.7, max_tokens=4096, tools=None, **kw):
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        if self._scripted:
            return self._scripted.pop(0)
        return LLMResponse(content=self._reply, prompt_tokens=10, completion_tokens=5, total_tokens=15,
                           finish_reason="stop", tool_calls=None)

    async def healthcheck(self) -> bool:
        return True

    async def list_models(self):
        return ["mock"]


async def _seed(db, tenant_id, chat_id, suffix, system_prompt, *, memory_enabled=False, pinned_memory=None,
                tools_policy="never"):
    await db.execute(text(
        "INSERT INTO tenants (id,name,slug,is_active,created_at,updated_at) "
        "VALUES (:id,:n,:s,true,now(),now())"), {"id": tenant_id, "n": f"ph-{suffix}", "s": f"ph-{suffix}"})
    await db.execute(text(
        "INSERT INTO tenant_shell_configs "
        "(id,tenant_id,provider_type,model_name,temperature,max_context_messages,max_tokens,"
        " memory_enabled,knowledge_base_enabled,tools_policy,context_mode,system_prompt,created_at,updated_at) "
        "VALUES (gen_random_uuid(),:t,'openai_compatible','mock',0.2,20,256,"
        " :mem,false,:tp,'recent_only',:sp,now(),now())"),
        {"t": tenant_id, "sp": system_prompt, "mem": memory_enabled, "tp": tools_policy})
    await db.execute(text(
        "INSERT INTO chats (id,tenant_id,title,status,created_at,updated_at) "
        "VALUES (:id,:t,'ph','active',now(),now())"), {"id": chat_id, "t": tenant_id})
    if pinned_memory:
        await db.execute(text(
            "INSERT INTO memory_entries (id,tenant_id,chat_id,memory_type,content,priority,is_pinned,created_at,updated_at) "
            "VALUES (gen_random_uuid(),:t,NULL,'long_term',:c,100,true,now(),now())"),
            {"t": tenant_id, "c": pinned_memory})


def run_pipeline(event_loop, user_content, *, system_prompt="Ты — тестовый ассистент.", reply="Готовый ответ.",
                 memory_enabled=False, pinned_memory=None, tools_policy="never", supports_tools=False,
                 scripted=None):
    """Seed a tenant+chat, run chat_completion with a recording provider, clean up.
    Returns (result_dict, provider)."""
    tid, cid = uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    provider = RecordingProvider(reply=reply, scripted=scripted)

    async def fake_resolve(tenant_id, user_q, db, shell_config):
        return ResolvedModel(provider=provider, provider_type="openai_compatible", model_name="mock",
                             supports_tools=supports_tools, supports_vision=False, source="shell_config")

    orig = pl.resolve_model
    pl.resolve_model = fake_resolve

    async def _scenario():
        async with async_session() as db:
            await _seed(db, tid, cid, suffix, system_prompt, memory_enabled=memory_enabled,
                        pinned_memory=pinned_memory, tools_policy=tools_policy)
            await db.commit()
        try:
            async with async_session() as db:
                return await pl.chat_completion(str(tid), str(cid), user_content, db)
        finally:
            async with async_session() as db:
                await db.execute(text("DELETE FROM llm_request_logs WHERE tenant_id=:t"), {"t": tid})
                await db.execute(text("DELETE FROM background_jobs WHERE tenant_id=:t"), {"t": tid})
                await db.execute(text("DELETE FROM artifacts WHERE tenant_id=:t"), {"t": tid})
                await db.execute(text("DELETE FROM memory_entries WHERE tenant_id=:t"), {"t": tid})
                await db.execute(text("DELETE FROM messages WHERE tenant_id=:t"), {"t": tid})
                await db.execute(text("DELETE FROM chats WHERE id=:c"), {"c": cid})
                await db.execute(text("DELETE FROM tenant_shell_configs WHERE tenant_id=:t"), {"t": tid})
                await db.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": tid})
                await db.commit()

    try:
        result = event_loop.run_until_complete(_scenario())
    finally:
        pl.resolve_model = orig
    return result, provider


def test_harness_runs_and_returns_reply(event_loop):
    result, provider = run_pipeline(event_loop, "Привет, как дела?")
    assert result["content"] == "Готовый ответ."
    assert provider.calls, "provider should have been called"


def test_assembled_prompt_has_system_and_user(event_loop):
    _result, provider = run_pipeline(event_loop, "Тестовый вопрос пользователя", system_prompt="МАРКЕР-СИСТЕМЫ-42")
    messages = provider.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    system_text = messages[0]["content"]
    # Tenant system prompt is included…
    assert "МАРКЕР-СИСТЕМЫ-42" in system_text
    # …along with the static instruction blocks (system_blocks.py).
    assert "Источники истины" in system_text
    assert "Правила работы с tools" in system_text
    # The user's message reaches the model.
    assert any("Тестовый вопрос пользователя" in str(m.get("content", "")) for m in messages)


def test_response_payload_shape(event_loop):
    result, _provider = run_pipeline(event_loop, "Вопрос")
    for key in ("content", "prompt_tokens", "completion_tokens", "total_tokens", "model_name", "correlation_id"):
        assert key in result, f"missing {key} in pipeline result"


def test_pinned_memory_reaches_prompt(event_loop):
    """BLOCK-MEMORY-B: a pinned tenant-wide memory fact is injected into the
    system prompt (no embedding search needed for pinned entries)."""
    _result, provider = run_pipeline(
        event_loop, "Любой вопрос",
        memory_enabled=True, pinned_memory="ВАЖНЫЙ-ФАКТ-О-КЛИЕНТЕ-777",
    )
    system_text = provider.calls[0]["messages"][0]["content"]
    assert "ВАЖНЫЙ-ФАКТ-О-КЛИЕНТЕ-777" in system_text


def test_tool_loop_executes_then_finalizes(event_loop):
    """Cover the tool-execution loop: the model emits a `plan` tool_call, the
    loop runs it and feeds the result back, the model then returns a final
    answer. Pins: two provider calls, a tool result reaches round 2, and the
    final content is returned."""
    plan_call = LLMResponse(
        content="",
        tool_calls=[{"id": "c1", "type": "function",
                     "function": {"name": "plan", "arguments": {"steps": ["шаг один", "шаг два"]}}}],
        finish_reason="tool_calls",
    )
    final = LLMResponse(content="Готово, выполнил план.", prompt_tokens=10, completion_tokens=5, total_tokens=15,
                        finish_reason="stop")
    result, provider = run_pipeline(
        event_loop, "Сделай A потом B",
        tools_policy="always", supports_tools=True, scripted=[plan_call, final],
    )
    # ≥2 calls: tool round + final round (a 3rd call is the auto-title summary).
    assert len(provider.calls) >= 2
    assert provider.calls[0]["tools"], "tools should be offered on the first round"
    assert result["content"] == "Готово, выполнил план."
    assert result.get("tool_calls_count", 0) >= 1
    # The plan tool result was fed back into the second (final) call's messages.
    second_round_msgs = provider.calls[1]["messages"]
    assert any(m.get("role") == "tool" or "План" in str(m.get("content", "")) for m in second_round_msgs)


def _plan_call(cid, steps):
    return LLMResponse(content="", finish_reason="tool_calls", tool_calls=[
        {"id": cid, "type": "function", "function": {"name": "plan", "arguments": {"steps": steps}}}])


def test_tool_loop_feeds_error_back(event_loop):
    """A failing tool (plan with <2 steps) must not abort the loop — the error
    is fed back so the model can recover, then it answers."""
    bad = _plan_call("c1", ["единственный шаг"])  # plan requires >=2 steps → error
    final = LLMResponse(content="Понял, ошибка обработана.", finish_reason="stop")
    result, provider = run_pipeline(
        event_loop, "Сделай что-то",
        tools_policy="always", supports_tools=True, scripted=[bad, final],
    )
    assert result["content"] == "Понял, ошибка обработана."
    # The tool error text is fed into the next round's messages.
    assert any("шаг" in str(m.get("content", "")).lower() or "ошибк" in str(m.get("content", "")).lower()
               for m in provider.calls[1]["messages"])


def test_tool_loop_multi_round(event_loop):
    """Two tool rounds before the final answer — exercises the loop iterating."""
    final = LLMResponse(content="Оба шага выполнены.", finish_reason="stop")
    result, provider = run_pipeline(
        event_loop, "Сделай A, потом B",
        tools_policy="always", supports_tools=True,
        scripted=[_plan_call("c1", ["a1", "a2"]), _plan_call("c2", ["b1", "b2"]), final],
    )
    assert result["content"] == "Оба шага выполнены."
    assert result.get("tool_calls_count", 0) >= 2   # two tool rounds happened
    # At least 3 provider rounds (2 tool + 1 final; a 4th may be the auto-title).
    assert len(provider.calls) >= 3
