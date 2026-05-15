import asyncio
import time
import pytest
import httpx

BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def admin_token(base_url):
    import httpx as _httpx
    deadline = time.time() + 45
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            r = _httpx.post(
                f"{base_url}/api/admin/auth/login",
                json={"login": "admin", "password": "admin"},
                timeout=10,
            )
            assert r.status_code == 200, r.text
            return r.json()["access_token"]
        except Exception as exc:  # pragma: no cover - integration bootstrap path
            last_error = exc
            time.sleep(1)
    raise AssertionError(f"admin login did not become ready in time: {last_error}")


@pytest.fixture(scope="session")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def client(base_url, auth_headers):
    with httpx.Client(base_url=base_url, headers=auth_headers, timeout=60, follow_redirects=True) as c:
        yield c
