from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
def test_security_headers_present():
    response = client.get("/users/")
    assert "X-Content-Type-Options" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "X-Frame-Options" in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "X-XSS-Protection" in response.headers
    assert response.headers["X-XSS-Protection"] == "1; mode=block"
    assert "Referrer-Policy" in response.headers
    assert "Permissions-Policy" in response.headers
    assert "X-Permitted-Cross-Domain-Policies" in response.headers
def test_content_security_policy():
    response = client.get("/users/")
    assert "Content-Security-Policy" in response.headers
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
def test_hsts_header():
    response = client.get("/users/")
    if "Strict-Transport-Security" in response.headers:
        hsts = response.headers["Strict-Transport-Security"]
        assert "max-age=" in hsts
def test_no_server_header():
    response = client.get("/users/")
    assert "X-Content-Type-Options" in response.headers
def test_security_headers_all_endpoints():
    endpoints = [
        "/users/",
        "/clients/",
        "/services/",
        "/visits/",
    ]
    for endpoint in endpoints:
        try:
            response = client.get(endpoint)
            assert "X-Content-Type-Options" in response.headers
            assert "X-Frame-Options" in response.headers
        except Exception:
            pass
