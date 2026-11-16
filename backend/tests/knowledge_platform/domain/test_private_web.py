"""Compiled-asset fixtures only; no build, browser session or live E2E claim."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from ahadiff.knowledge_platform.auth import DomainAuthenticationMiddleware, TokenAuthenticator
from ahadiff.knowledge_platform.private_web import create_private_wiki_mount


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "compiled-fixture"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<html><script type="module" src="/wiki/assets/main.js"></script></html>',
        encoding="utf-8",
    )
    (root / "assets/main.js").write_text("export const fixture = true;", encoding="utf-8")
    (root / "assets/main.css").write_text("body { color: black; }", encoding="utf-8")
    return root


def request(app, path, *, method="GET"):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://wiki.test"
        ) as client:
            return await client.request(method, path)
    return asyncio.run(run())


def test_static_shell_has_no_implicit_api_identity(web_root):
    async def forbidden_reach(request):
        raise AssertionError("Unauthenticated API must not run")

    app = Starlette(
        routes=[
            Route("/api/knowledge/v1/tasks", forbidden_reach),
            create_private_wiki_mount(web_root),
        ],
        middleware=[
            Middleware(DomainAuthenticationMiddleware, authenticator=TokenAuthenticator({}))
        ],
    )
    assert request(app, "/wiki").status_code == 307
    index = request(app, "/wiki/")
    assert index.status_code == 200 and "main.js" in index.text
    assert "set-cookie" not in index.headers
    assert "script-src 'self'" in index.headers["content-security-policy"]
    assert "unsafe-inline" not in index.headers["content-security-policy"]
    assert index.headers["x-content-type-options"] == "nosniff"
    assert index.headers["cache-control"] == "no-store"
    assert request(app, "/api/knowledge/v1/tasks").status_code == 401
    assert request(app, "/wiki/assets/main.js").status_code == 200
    assert request(app, "/wiki/assets/main.css", method="HEAD").status_code == 200
    assert request(app, "/wiki/", method="POST").status_code == 405


@pytest.mark.parametrize(
    "relative",
    ["assets/missing.js", "assets/main.js.map", ".vite/manifest.json",
     "config.json", "src/main.tsx", "extra.html", "catalog", "assets/.hidden.js"],
)
def test_missing_assets_and_source_paths_never_fallback_to_index(web_root, relative):
    path = web_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if relative != "assets/missing.js":
        path.write_text("not-for-the-browser", encoding="utf-8")
    response = request(Starlette(routes=[create_private_wiki_mount(web_root)]), "/wiki/" + relative)
    assert response.status_code == 404
    assert response.text == ""
    assert response.headers["referrer-policy"] == "no-referrer"


def test_symlink_cannot_escape_compiled_root(web_root, tmp_path):
    outside = tmp_path / "private.js"
    outside.write_text("never-leak", encoding="utf-8")
    (web_root / "assets/escape.js").symlink_to(outside)
    response = request(
        Starlette(routes=[create_private_wiki_mount(web_root)]), "/wiki/assets/escape.js"
    )
    assert response.status_code == 404 and "never-leak" not in response.text


def test_mount_requires_explicit_compiled_index(tmp_path):
    with pytest.raises(ValueError):
        create_private_wiki_mount(Path("relative"))
    with pytest.raises(ValueError):
        create_private_wiki_mount(tmp_path)


def test_mount_does_not_shadow_health_or_other_apps(web_root):
    async def health(request):
        return JSONResponse({"status": "ready"})

    app = Starlette(
        routes=[Route("/health", health), create_private_wiki_mount(web_root)]
    )
    assert request(app, "/health").json() == {"status": "ready"}
    assert request(app, "/viewer/").status_code == 404
