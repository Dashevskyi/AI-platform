import httpx
import uuid

TENANT_SLUG = f"test-tenant-{uuid.uuid4().hex[:8]}"


def test_create_tenant(client):
    r = client.post("/api/admin/tenants/", json={
        "name": "Test Tenant", "slug": TENANT_SLUG, "description": "For testing"
    })
    assert r.status_code in (200, 201), f"Got {r.status_code}: {r.text}"
    data = r.json()
    assert data["name"] == "Test Tenant"
    assert data["slug"] == TENANT_SLUG
    assert data["is_active"] is True


def test_list_tenants(client):
    r = client.get("/api/admin/tenants/")
    assert r.status_code == 200
    data = r.json()
    assert data["total_count"] >= 1
    assert len(data["items"]) >= 1


def test_get_tenant(client):
    r = client.get("/api/admin/tenants/", params={"search": TENANT_SLUG})
    tenant_id = r.json()["items"][0]["id"]

    r = client.get(f"/api/admin/tenants/{tenant_id}")
    assert r.status_code == 200
    assert r.json()["slug"] == TENANT_SLUG


def test_update_tenant(client):
    r = client.get("/api/admin/tenants/", params={"search": TENANT_SLUG})
    tenant_id = r.json()["items"][0]["id"]

    r = client.patch(f"/api/admin/tenants/{tenant_id}", json={"description": "Updated"})
    assert r.status_code == 200
    assert r.json()["description"] == "Updated"


def test_tenant_isolation(client):
    """Create two tenants and verify they don't see each other's data."""
    uid = uuid.uuid4().hex[:6]
    r1 = client.post("/api/admin/tenants/", json={"name": "Iso A", "slug": f"iso-a-{uid}"})
    r2 = client.post("/api/admin/tenants/", json={"name": "Iso B", "slug": f"iso-b-{uid}"})
    t1 = r1.json()["id"]
    t2 = r2.json()["id"]

    # Create tool for tenant A
    client.post(f"/api/admin/tenants/{t1}/tools/", json={"name": "Tool A"})

    # Tenant B should have no tools
    r = client.get(f"/api/admin/tenants/{t2}/tools/")
    assert r.status_code == 200
    assert r.json()["total_count"] == 0

    # Tenant A should have 1 tool
    r = client.get(f"/api/admin/tenants/{t1}/tools/")
    assert r.json()["total_count"] == 1

    # Cleanup
    client.delete(f"/api/admin/tenants/{t1}")
    client.delete(f"/api/admin/tenants/{t2}")
