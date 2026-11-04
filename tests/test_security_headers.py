"""Round 2: browser security headers on every response."""
from fastapi.testclient import TestClient

from kareshafaa.main import create_app


def _client():
    return TestClient(create_app())


def test_headers_on_pages_and_api():
    with _client() as c:
        for path in ("/login", "/healthz", "/fhir/metadata"):
            r = c.get(path)
            h = r.headers
            assert "frame-ancestors 'none'" in h["content-security-policy"], path
            assert "object-src 'none'" in h["content-security-policy"]
            assert "default-src 'self'" in h["content-security-policy"]
            assert "camera=()" in h["permissions-policy"]
            assert h["x-frame-options"] == "DENY"
            assert h["x-content-type-options"] == "nosniff"
            assert h["referrer-policy"] == "no-referrer"
            assert "strict-transport-security" not in h  # plain http in tests


def test_hsts_only_over_https():
    with _client() as c:
        r = c.get("/healthz", headers={"x-forwarded-proto": "https"})
        assert r.headers["strict-transport-security"].startswith("max-age=31536000")


def test_session_cookie_flags():
    with _client() as c:
        r = c.get("/login")
        raw = ";".join(v for k, v in r.headers.multi_items() if k.lower() == "set-cookie").lower()
        if raw:
            assert "httponly" in raw and "samesite" in raw
