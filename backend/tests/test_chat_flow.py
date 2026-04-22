import httpx
import uuid


def test_full_chat_flow(client):
    """End-to-end: create tenant -> create key -> create chat -> send message -> check logs."""
    slug = f"chat-e2e-{uuid.uuid4().hex[:8]}"
    idem_key = f"idem-{uuid.uuid4().hex[:8]}"

    # Create tenant
    r = client.post("/api/admin/tenants/", json={"name": "Chat Test", "slug": slug})
    assert r.status_code in (200, 201), f"Got {r.status_code}: {r.text}"
    tenant_id = r.json()["id"]

    # Configure shell for Ollama
    r = client.put(f"/api/admin/tenants/{tenant_id}/shell/", json={
        "provider_type": "ollama",
        "provider_base_url": "http://localhost:11434",
        "model_name": "qwen2.5:32b",
        "system_prompt": "You are a helpful assistant. Reply briefly.",
        "temperature": 0.5,
        "max_tokens": 256,
    })
    assert r.status_code == 200, f"Shell config: {r.status_code}: {r.text}"

    # Create API key
    r = client.post(f"/api/admin/tenants/{tenant_id}/keys/", json={"name": "test-key"})
    assert r.status_code in (200, 201), f"Key create: {r.status_code}: {r.text}"
    raw_key = r.json()["raw_key"]
    assert raw_key.startswith("aip_")

    # Use tenant API to create chat
    tenant_headers = {"X-API-Key": raw_key}
    with httpx.Client(base_url="http://localhost:8000", headers=tenant_headers, timeout=180, follow_redirects=True) as tc:
        r = tc.post(f"/api/tenants/{tenant_id}/chats/", json={"title": "Test Chat"})
        assert r.status_code in (200, 201), f"Chat create: {r.status_code}: {r.text}"
        chat_id = r.json()["id"]

        # Send message
        r = tc.post(f"/api/tenants/{tenant_id}/chats/{chat_id}/messages/", json={
            "content": "Say hello in one word",
            "idempotency_key": idem_key,
        })
        assert r.status_code in (200, 201), f"Send msg: {r.status_code}: {r.text}"
        msg = r.json()
        assert msg["role"] == "assistant"
        assert len(msg["content"]) > 0

        # Idempotency: same key should return same message
        r2 = tc.post(f"/api/tenants/{tenant_id}/chats/{chat_id}/messages/", json={
            "content": "Say hello in one word",
            "idempotency_key": idem_key,
        })
        assert r2.status_code == 200
        assert r2.json()["id"] == msg["id"]

        # List messages
        r = tc.get(f"/api/tenants/{tenant_id}/chats/{chat_id}/messages/")
        assert r.status_code == 200
        assert r.json()["total_count"] >= 2  # user + assistant

    # Check logs
    r = client.get(f"/api/admin/tenants/{tenant_id}/logs/")
    assert r.status_code == 200
    assert r.json()["total_count"] >= 1

    # Cleanup
    client.delete(f"/api/admin/tenants/{tenant_id}")
