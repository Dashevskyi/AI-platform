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


# Function-scoped (not session): the logout / change-password tests bump the
# admin's token_version, which revokes any previously-issued token. Re-logging
# in per test keeps every test's token at the current version regardless of order.
@pytest.fixture
def admin_token(base_url):
    import httpx as _httpx
    from app.core.config import settings

    deadline = time.time() + 45
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            r = _httpx.post(
                f"{base_url}/api/admin/auth/login",
                json={"login": settings.ADMIN_LOGIN, "password": settings.ADMIN_PASSWORD},
                timeout=10,
            )
            assert r.status_code == 200, r.text
            return r.json()["access_token"]
        except Exception as exc:  # pragma: no cover - integration bootstrap path
            last_error = exc
            time.sleep(1)
    raise AssertionError(f"admin login did not become ready in time: {last_error}")


@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def client(base_url, auth_headers):
    with httpx.Client(base_url=base_url, headers=auth_headers, timeout=60, follow_redirects=True) as c:
        yield c
