import httpx


def test_health(base_url):
    r = httpx.get(f"{base_url}/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"


def test_ready(base_url):
    r = httpx.get(f"{base_url}/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
