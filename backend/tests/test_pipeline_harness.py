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
    """Captures the messages it's asked to complete; returns a canned reply."""
    def __init__(self, reply="Готовый ответ."):
        super().__init__("http://mock.test")
        self.calls = []
        self._reply = reply

    async def chat_completion(self, messages, model, temperature=0.7, max_tokens=4096, tools=None, **kw):
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return LLMResponse(content=self._reply, prompt_tokens=10, completion_tokens=5, total_tokens=15,
                           finish_reason="stop", tool_calls=None)

    async def healthcheck(self) -> bool:
        return True

    async def list_models(self):
        return ["mock"]


def _seed(db, tenant_id, chat_id, suffix, system_prompt):
    return [
        db.execute(text(
            "INSERT INTO tenants (id,name,slug,is_active,created_at,updated_at) "
            "VALUES (:id,:n,:s,true,now(),now())"), {"id": tenant_id, "n": f"ph-{suffix}", "s": f"ph-{suffix}"}),
        db.execute(text(
            "INSERT INTO tenant_shell_configs "
            "(id,tenant_id,provider_type,model_name,temperature,max_context_messages,max_tokens,"
            " memory_enabled,knowledge_base_enabled,tools_policy,context_mode,system_prompt,created_at,updated_at) "
            "VALUES (gen_random_uuid(),:t,'openai_compatible','mock',0.2,20,256,"
            " false,false,'never','recent_only',:sp,now(),now())"), {"t": tenant_id, "sp": system_prompt}),
        db.execute(text(
            "INSERT INTO chats (id,tenant_id,title,status,created_at,updated_at) "
            "VALUES (:id,:t,'ph','active',now(),now())"), {"id": chat_id, "t": tenant_id}),
    ]


def run_pipeline(event_loop, user_content, *, system_prompt="Ты — тестовый ассистент.", reply="Готовый ответ."):
    """Seed a tenant+chat, run chat_completion with a recording provider, clean up.
    Returns (result_dict, provider)."""
    tid, cid = uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    provider = RecordingProvider(reply=reply)

    async def fake_resolve(tenant_id, user_q, db, shell_config):
        return ResolvedModel(provider=provider, provider_type="openai_compatible", model_name="mock",
                             supports_tools=False, supports_vision=False, source="shell_config")

    orig = pl.resolve_model
    pl.resolve_model = fake_resolve

    async def _scenario():
        async with async_session() as db:
            for stmt in _seed(db, tid, cid, suffix, system_prompt):
                await stmt
            await db.commit()
        try:
            async with async_session() as db:
                return await pl.chat_completion(str(tid), str(cid), user_content, db)
        finally:
            async with async_session() as db:
                await db.execute(text("DELETE FROM llm_request_logs WHERE tenant_id=:t"), {"t": tid})
                await db.execute(text("DELETE FROM background_jobs WHERE tenant_id=:t"), {"t": tid})
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
