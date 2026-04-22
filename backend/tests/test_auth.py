import httpx


def test_login_success(base_url):
    r = httpx.post(f"{base_url}/api/admin/auth/login", json={"login": "admin", "password": "admin"})
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password(base_url):
    r = httpx.post(f"{base_url}/api/admin/auth/login", json={"login": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_me(base_url, auth_headers):
    r = httpx.get(f"{base_url}/api/admin/auth/me", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["login"] == "admin"
    assert data["role"] == "superadmin"


def test_me_no_token(base_url):
    r = httpx.get(f"{base_url}/api/admin/auth/me", headers={"Authorization": "Bearer invalid"})
    assert r.status_code in (401, 403)
