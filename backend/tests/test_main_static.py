"""The SPA fallback must never serve files outside frontend/dist."""

from fastapi.testclient import TestClient

from app.main import app


def test_spa_route_blocks_path_traversal():
    client = TestClient(app)
    for path in ("/..%2F..%2FREADME.md", "/..%2F..%2F.env", "/%2Fetc%2Fhosts", "/..%2Fbackend%2Fpyproject.toml"):
        r = client.get(path)
        assert "OPENAI_API_KEY" not in r.text
        assert "[project]" not in r.text
        assert "localhost" not in r.text or r.headers.get("content-type", "").startswith("text/html")


def test_unknown_api_path_is_404():
    assert TestClient(app).get("/api/does-not-exist").status_code == 404
