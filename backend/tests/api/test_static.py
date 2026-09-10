"""Serving the Next.js static export (API_CONTRACT §8)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.static import DEFAULT_STATIC_DIR, mount_static, resolve_static_dir


@pytest.fixture
def export_dir(tmp_path, monkeypatch):
    """A stand-in for the built frontend.

    The `404.html` matters and is not decoration: `next build` emits one, and
    StaticFiles(html=True) *returns* it instead of raising HTTPException. An
    earlier version of this fixture omitted it, so the unknown-/api/ test below
    exercised a code path that could not run in production -- the real export
    always has a 404.html -- and passed against a mount that answered every
    unmatched /api/ path with HTML. Keep this file list faithful to a real
    export.
    """
    (tmp_path / "index.html").write_text("<html>finally</html>")
    (tmp_path / "404.html").write_text("<html>next-not-found</html>")
    (tmp_path / "_next").mkdir()
    (tmp_path / "_next" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def static_client(export_dir):
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    assert mount_static(app) is True
    with TestClient(app) as client:
        yield client


def test_default_dir_is_the_backend_package_root():
    assert DEFAULT_STATIC_DIR.name == "static"
    assert DEFAULT_STATIC_DIR.parent.name == "backend"


def test_env_var_overrides_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path))

    assert resolve_static_dir() == tmp_path


def test_blank_env_var_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("FINALLY_STATIC_DIR", "   ")

    assert resolve_static_dir() == DEFAULT_STATIC_DIR


def test_missing_directory_does_not_crash_the_app(tmp_path, monkeypatch, caplog):
    """Local backend-only development must still boot, with one warning."""
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path / "nope"))
    app = FastAPI()

    with caplog.at_level("WARNING"):
        mounted = mount_static(app)

    assert mounted is False
    assert "No static frontend" in caplog.text
    assert [route for route in app.routes if getattr(route, "name", "") == "static"] == []


def test_index_is_served_at_the_root(static_client):
    response = static_client.get("/")

    assert response.status_code == 200
    assert "finally" in response.text


def test_assets_are_served(static_client):
    assert static_client.get("/_next/app.js").status_code == 200


def test_unknown_path_serves_the_next_404(static_client):
    """API_CONTRACT §8: a missing page is a real 404, not the dashboard.

    This export has no client-side routes, so there is no deep link that an
    index.html-with-200 fallback would rescue.
    """
    response = static_client.get("/some/deep/route")

    assert response.status_code == 404
    assert "next-not-found" in response.text


def test_unknown_path_falls_back_to_index_when_the_export_has_no_404(
    tmp_path, monkeypatch
):
    """Without a 404.html, StaticFiles raises and the index fallback runs."""
    (tmp_path / "index.html").write_text("<html>finally</html>")
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path))

    app = FastAPI()
    assert mount_static(app) is True

    with TestClient(app) as client:
        response = client.get("/some/deep/route")

    assert response.status_code == 200
    assert "finally" in response.text


def test_api_routes_take_precedence(static_client):
    assert static_client.get("/api/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/api/does-not-exist", "/api/", "/api/a/b/c"])
def test_unknown_api_path_gets_json_not_html(static_client, path):
    """Answering /api/* with the HTML shell would make the caller try to
    JSON.parse a document.

    Runs against an export that *has* a 404.html, which is what makes this
    meaningful: that file is precisely what stops StaticFiles raising, and so
    is what made the previous exception-handler implementation dead code.
    """
    response = static_client.get(path)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["code"] == "NOT_FOUND"
