import os
import httpx
import uuid

from app.core.database import async_session
from app.services.llm.pipeline import _public_tool_def

TEST_OPENAI_BASE_URL = os.getenv("TEST_OPENAI_BASE_URL") or "http://172.10.100.9:8000/v1"
TEST_OPENAI_MODEL = os.getenv("TEST_OPENAI_MODEL") or "qwen2.5-32b"


def _create_tenant_and_key(client, slug: str, *, shell_payload: dict | None = None):
    r = client.post("/api/admin/tenants/", json={"name": slug, "slug": slug})
    assert r.status_code in (200, 201), r.text
    tenant_id = r.json()["id"]

    shell_payload = shell_payload or {
        "provider_type": "openai_compatible",
        "provider_base_url": TEST_OPENAI_BASE_URL,
        "model_name": TEST_OPENAI_MODEL,
        "system_prompt": "You are a helpful assistant. Reply briefly.",
        "temperature": 0.2,
        "max_tokens": 32,
    }
    r = client.put(f"/api/admin/tenants/{tenant_id}/shell/", json=shell_payload)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/admin/tenants/{tenant_id}/keys/", json={"name": "test-key"})
    assert r.status_code in (200, 201), r.text
    raw_key = r.json()["raw_key"]
    return tenant_id, raw_key


def _create_chat(base_url: str, tenant_id: str, raw_key: str, title: str):
    with httpx.Client(base_url=base_url, headers={"X-API-Key": raw_key}, timeout=180, follow_redirects=True) as tc:
        r = tc.post(f"/api/tenants/{tenant_id}/chats/", json={"title": title})
        assert r.status_code in (200, 201), r.text
        return r.json()["id"]


def test_idempotency_is_scoped_per_chat(client, base_url):
    slug = f"idem-scope-{uuid.uuid4().hex[:8]}"
    tenant_id, raw_key = _create_tenant_and_key(client, slug)
    idem_key = f"idem-{uuid.uuid4().hex[:8]}"

    try:
        chat_a = _create_chat(base_url, tenant_id, raw_key, "A")
        chat_b = _create_chat(base_url, tenant_id, raw_key, "B")

        with httpx.Client(base_url=base_url, headers={"X-API-Key": raw_key}, timeout=180, follow_redirects=True) as tc:
            r1 = tc.post(
                f"/api/tenants/{tenant_id}/chats/{chat_a}/messages/",
                json={"content": "Say hello in one word", "idempotency_key": idem_key},
            )
            assert r1.status_code in (200, 201), r1.text
            m1 = r1.json()

            r2 = tc.post(
                f"/api/tenants/{tenant_id}/chats/{chat_b}/messages/",
                json={"content": "Say hello in one word", "idempotency_key": idem_key},
            )
            assert r2.status_code in (200, 201), r2.text
            m2 = r2.json()

            assert m1["id"] != m2["id"]
    finally:
        client.delete(f"/api/admin/tenants/{tenant_id}")


def test_user_message_persists_when_llm_call_fails(client, base_url):
    slug = f"llm-fail-{uuid.uuid4().hex[:8]}"
    tenant_id, raw_key = _create_tenant_and_key(
        client,
        slug,
        shell_payload={
            "provider_type": "openai_compatible",
            "provider_base_url": "http://127.0.0.1:9",
            "model_name": "broken-model",
            "system_prompt": "Reply briefly.",
            "temperature": 0.2,
            "max_tokens": 64,
        },
    )

    try:
        chat_id = _create_chat(base_url, tenant_id, raw_key, "Failure Chat")
        with httpx.Client(base_url=base_url, headers={"X-API-Key": raw_key}, timeout=60, follow_redirects=True) as tc:
            content = "This request should fail upstream"
            r = tc.post(
                f"/api/tenants/{tenant_id}/chats/{chat_id}/messages/",
                json={"content": content, "idempotency_key": f"fail-{uuid.uuid4().hex[:8]}"},
            )
            assert r.status_code in (200, 201), r.text
            msg = r.json()
            assert msg["role"] == "assistant"
            assert msg["status"] == "error"

            history = tc.get(f"/api/tenants/{tenant_id}/chats/{chat_id}/messages/")
            assert history.status_code == 200, history.text
            items = history.json()["items"]
            assert len(items) >= 2
            assert items[-2]["role"] == "user"
            assert items[-2]["content"] == content
            assert items[-1]["role"] == "assistant"
            assert items[-1]["status"] == "error"

        logs = client.get(f"/api/admin/tenants/{tenant_id}/logs/")
        assert logs.status_code == 200, logs.text
        assert logs.json()["total_count"] >= 1
    finally:
        client.delete(f"/api/admin/tenants/{tenant_id}")


def test_model_without_tool_support_suppresses_tool_definitions(client, base_url):
    slug = f"no-tools-{uuid.uuid4().hex[:8]}"
    tenant_id, raw_key = _create_tenant_and_key(client, slug)
    model_id = None

    try:
        r = client.post(
            "/api/admin/models/",
            json={
                "name": f"NoTools-{uuid.uuid4().hex[:6]}",
                "provider_type": "openai_compatible",
                "base_url": TEST_OPENAI_BASE_URL,
                "model_id": TEST_OPENAI_MODEL,
                "tier": "medium",
                "supports_tools": False,
                "supports_vision": False,
                "is_active": True,
            },
        )
        assert r.status_code in (200, 201), r.text
        model_id = r.json()["id"]

        r = client.put(
            f"/api/admin/tenants/{tenant_id}/model-config/",
            json={"mode": "manual", "manual_model_id": model_id},
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/admin/tenants/{tenant_id}/tools/",
            json={
                "name": "find_status",
                "description": "найди статус сервиса и покажи данные",
                "config_json": {
                    "type": "function",
                    "function": {
                        "name": "find_status",
                        "description": "найди статус сервиса",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"}
                            },
                            "required": ["query"],
                        },
                    },
                },
            },
        )
        assert r.status_code in (200, 201), r.text

        chat_id = _create_chat(base_url, tenant_id, raw_key, "No Tools Chat")
        with httpx.Client(base_url=base_url, headers={"X-API-Key": raw_key}, timeout=180, follow_redirects=True) as tc:
            r = tc.post(
                f"/api/tenants/{tenant_id}/chats/{chat_id}/messages/",
                json={
                    "content": "Найди статус сервиса и кратко опиши его",
                    "idempotency_key": f"tools-{uuid.uuid4().hex[:8]}",
                },
            )
            assert r.status_code in (200, 201), r.text

        logs = client.get(f"/api/admin/tenants/{tenant_id}/logs/", params={"chat_id": chat_id, "page_size": 5})
        assert logs.status_code == 200, logs.text
        items = logs.json()["items"]
        assert items, logs.text
        log_id = items[0]["id"]

        detail = client.get(f"/api/admin/tenants/{tenant_id}/logs/{log_id}")
        assert detail.status_code == 200, detail.text
        data = detail.json()
        assert data["normalized_request"]["tools_count"] == 0
        assert data["context_tools_count"] == 0
    finally:
        client.delete(f"/api/admin/tenants/{tenant_id}")
        if model_id:
            client.delete(f"/api/admin/models/{model_id}")


def test_search_records_tool_definition_prefers_filters_for_explicit_fields():
    tool_config = {
        "type": "function",
        "function": {
            "name": "search_addresses",
            "description": "Ищет адреса по улице, дому, литере, клиенту и квартире.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {"type": "object"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        "x_backend_config": {
            "handler": "search_records",
            "filter_fields": {
                "street": {"column": "s.name", "mode": "contains"},
                "house": {"column": "sb.num", "mode": "exact"},
                "client_name": {"column": "c.name", "mode": "contains"},
            },
            "search_columns": ["s.name", "sb.litera", "c.name", "c.apart"],
        },
    }

    public = _public_tool_def(tool_config)
    function_def = public["function"]
    props = function_def["parameters"]["properties"]

    assert "Используй filters для явных полей" in function_def["description"]
    assert "query оставляй только для неструктурированного поиска" in function_def["description"]
    assert "Используй query только когда запрос нельзя выразить через filters" in props["query"]["description"]
    assert "street: Название улицы или её часть" in props["filters"]["description"]
    assert props["filters"]["properties"]["street"]["description"] == "Название улицы или её часть"
    assert props["filters"]["properties"]["house"]["description"] == "Номер дома"


def test_legacy_admin_created_chat_is_visible_via_tenant_key(client, base_url):
    slug = f"legacy-chat-{uuid.uuid4().hex[:8]}"
    tenant_id, raw_key = _create_tenant_and_key(client, slug)

    try:
        r = client.post(
            f"/api/admin/tenants/{tenant_id}/chats/",
            json={"title": "Legacy Chat"},
        )
        assert r.status_code in (200, 201), r.text
        chat_id = r.json()["id"]
        assert r.json()["api_key_id"] is None

        with httpx.Client(base_url=base_url, headers={"X-API-Key": raw_key}, timeout=180, follow_redirects=True) as tc:
            r = tc.get(f"/api/tenants/{tenant_id}/chats/")
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            assert any(item["id"] == chat_id for item in items), r.text

            r = tc.get(f"/api/tenants/{tenant_id}/chats/{chat_id}")
            assert r.status_code == 200, r.text
            assert r.json()["id"] == chat_id
    finally:
        client.delete(f"/api/admin/tenants/{tenant_id}")


def test_rotated_key_keeps_access_to_existing_chat(client, base_url, event_loop):
    slug = f"rotate-chat-{uuid.uuid4().hex[:8]}"
    tenant_id, raw_key = _create_tenant_and_key(client, slug)

    try:
        chat_id = _create_chat(base_url, tenant_id, raw_key, "Rotating Chat")
        keys_resp = client.get(f"/api/admin/tenants/{tenant_id}/keys/")
        assert keys_resp.status_code == 200, keys_resp.text
        key_id = keys_resp.json()["items"][0]["id"]

        rotate_resp = client.post(f"/api/admin/tenants/{tenant_id}/keys/{key_id}/rotate")
        assert rotate_resp.status_code == 200, rotate_resp.text
        new_raw_key = rotate_resp.json()["raw_key"]

        with httpx.Client(base_url=base_url, headers={"X-API-Key": new_raw_key}, timeout=180, follow_redirects=True) as tc:
            r = tc.get(f"/api/tenants/{tenant_id}/chats/{chat_id}")
            assert r.status_code == 200, r.text
            assert r.json()["id"] == chat_id

        async def _fetch_api_key_id() -> str | None:
            async with async_session() as db:
                from sqlalchemy import select
                from app.models.chat import Chat

                chat = (
                    await db.execute(select(Chat).where(Chat.id == uuid.UUID(chat_id)))
                ).scalars().first()
                return str(chat.api_key_id) if chat and chat.api_key_id else None

        rebound_api_key_id = event_loop.run_until_complete(_fetch_api_key_id())
        assert rebound_api_key_id == rotate_resp.json()["id"]
    finally:
        client.delete(f"/api/admin/tenants/{tenant_id}")


def test_delete_key_nulls_chat_binding_and_preserves_chat_access(client, base_url, event_loop):
    slug = f"delete-key-chat-{uuid.uuid4().hex[:8]}"
    tenant_id, raw_key = _create_tenant_and_key(client, slug)

    try:
        chat_id = _create_chat(base_url, tenant_id, raw_key, "Delete Key Chat")

        with httpx.Client(base_url=base_url, headers={"X-API-Key": raw_key}, timeout=180, follow_redirects=True) as tc:
            r = tc.post(
                f"/api/tenants/{tenant_id}/chats/{chat_id}/messages/",
                json={"content": "Say hello", "idempotency_key": f"delete-{uuid.uuid4().hex[:8]}"},
            )
            assert r.status_code in (200, 201), r.text

        extra_key_resp = client.post(f"/api/admin/tenants/{tenant_id}/keys/", json={"name": "spare-key"})
        assert extra_key_resp.status_code in (200, 201), extra_key_resp.text
        spare_raw_key = extra_key_resp.json()["raw_key"]

        keys_resp = client.get(f"/api/admin/tenants/{tenant_id}/keys/")
        assert keys_resp.status_code == 200, keys_resp.text
        items = keys_resp.json()["items"]
        key_to_delete = next(item for item in items if item["name"] == "test-key")

        delete_resp = client.delete(f"/api/admin/tenants/{tenant_id}/keys/{key_to_delete['id']}")
        assert delete_resp.status_code == 204, delete_resp.text

        with httpx.Client(base_url=base_url, headers={"X-API-Key": spare_raw_key}, timeout=180, follow_redirects=True) as tc:
            r = tc.get(f"/api/tenants/{tenant_id}/chats/{chat_id}")
            assert r.status_code == 200, r.text

        async def _fetch_chat_and_log_binding() -> tuple[str | None, int]:
            async with async_session() as db:
                from sqlalchemy import select
                from app.models.chat import Chat
                from app.models.llm_request_log import LLMRequestLog

                chat = (
                    await db.execute(select(Chat).where(Chat.id == uuid.UUID(chat_id)))
                ).scalars().first()
                log_count = (
                    await db.execute(
                        select(LLMRequestLog).where(
                            LLMRequestLog.tenant_id == uuid.UUID(tenant_id),
                            LLMRequestLog.api_key_id.is_(None),
                        )
                    )
                ).scalars().all()
                return (str(chat.api_key_id) if chat and chat.api_key_id else None, len(log_count))

        chat_api_key_id, null_log_count = event_loop.run_until_complete(_fetch_chat_and_log_binding())
        assert chat_api_key_id is None
        assert null_log_count >= 1
    finally:
        client.delete(f"/api/admin/tenants/{tenant_id}")


def test_delete_group_preserves_inherited_tool_restrictions(client):
    slug = f"group-delete-{uuid.uuid4().hex[:8]}"
    tenant_id, _raw_key = _create_tenant_and_key(client, slug)

    try:
        tool_resp = client.post(
            f"/api/admin/tenants/{tenant_id}/tools/",
            json={
                "name": "find_status",
                "description": "find status",
                "config_json": {
                    "type": "function",
                    "function": {
                        "name": "find_status",
                        "description": "find status",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            },
        )
        assert tool_resp.status_code in (200, 201), tool_resp.text
        tool_id = tool_resp.json()["id"]

        group_resp = client.post(
            f"/api/admin/tenants/{tenant_id}/key-groups/",
            json={"name": "restricted", "allowed_tool_ids": [tool_id]},
        )
        assert group_resp.status_code in (200, 201), group_resp.text
        group_id = group_resp.json()["id"]

        key_resp = client.post(
            f"/api/admin/tenants/{tenant_id}/keys/",
            json={"name": "group-key", "group_id": group_id},
        )
        assert key_resp.status_code in (200, 201), key_resp.text
        key_id = key_resp.json()["id"]

        delete_resp = client.delete(f"/api/admin/tenants/{tenant_id}/key-groups/{group_id}")
        assert delete_resp.status_code == 204, delete_resp.text

        keys_resp = client.get(f"/api/admin/tenants/{tenant_id}/keys/")
        assert keys_resp.status_code == 200, keys_resp.text
        key_item = next(item for item in keys_resp.json()["items"] if item["id"] == key_id)
        assert key_item["group_id"] is None
        assert key_item["allowed_tool_ids"] == [tool_id]
    finally:
        client.delete(f"/api/admin/tenants/{tenant_id}")
