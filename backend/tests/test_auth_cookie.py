"""HttpOnly-cookie auth + JWT revocation (token_version)."""
import httpx

from app.core.config import settings

CREDS = {"login": settings.ADMIN_LOGIN, "password": settings.ADMIN_PASSWORD}


def test_login_sets_httponly_cookie(base_url):
    r = httpx.post(f"{base_url}/api/admin/auth/login", json=CREDS)
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert "HttpOnly" in set_cookie


def test_me_works_via_cookie_only(base_url):
    """A cookie jar with no Authorization header must authenticate /me."""
    with httpx.Client(base_url=base_url) as c:
        r = c.post("/api/admin/auth/login", json=CREDS)
        assert r.status_code == 200
        # No bearer header — only the cookie the client stored.
        me = c.get("/api/admin/auth/me")
        assert me.status_code == 200
        assert me.json()["login"] == settings.ADMIN_LOGIN


def test_logout_revokes_token(base_url):
    with httpx.Client(base_url=base_url) as c:
        c.post("/api/admin/auth/login", json=CREDS)
        assert c.get("/api/admin/auth/me").status_code == 200
        assert c.post("/api/admin/auth/logout").status_code == 200
        # Same (now-revoked) cookie must no longer authenticate.
        assert c.get("/api/admin/auth/me").status_code == 401


def test_header_bearer_still_works(base_url):
    """Back-compat: the token returned in the body authenticates via header."""
    token = httpx.post(f"{base_url}/api/admin/auth/login", json=CREDS).json()["access_token"]
    r = httpx.get(f"{base_url}/api/admin/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
