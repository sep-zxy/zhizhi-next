"""Same-origin private Wiki assets, never an authentication or data boundary."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

if TYPE_CHECKING:
    from starlette.types import Scope

PRIVATE_WIKI_CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    )
)
_ASSET_SUFFIXES = frozenset({".js", ".css", ".svg", ".png", ".webp", ".ico", ".woff", ".woff2"})


class PrivateWikiStaticFiles(StaticFiles):
    """Serve the compiled hash-router UI without source files or SPA fake 200s."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_absolute():
            raise ValueError("Private Wiki assets require an explicit absolute directory")
        root = directory.resolve(strict=True)
        index = root / "index.html"
        if not root.is_dir() or not index.is_file() or index.is_symlink():
            raise ValueError("Private Wiki compiled index is missing or unsafe")
        super().__init__(directory=str(root), html=True, check_dir=True, follow_symlink=False)

    async def get_response(self, path: str, scope: Scope) -> Response:
        # Vite emits index.html and assets/*. No .vite manifest, source map,
        # source tree, arbitrary HTML or state/config file is exposed.
        portable_path = path.replace("\\", "/")
        parts = portable_path.split("/")
        root_index = portable_path in {"", ".", "index.html"}
        asset = (
            portable_path.startswith("assets/")
            and not any(part in {"", ".", ".."} or part.startswith(".") for part in parts)
            and Path(portable_path).suffix.lower() in _ASSET_SUFFIXES
        )
        try:
            if not (root_index or asset):
                raise HTTPException(status_code=404)
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            response = Response(
                status_code=exc.status_code,
                headers={"Allow": "GET, HEAD"} if exc.status_code == 405 else None,
            )
        response.headers.update(
            {
                "Content-Security-Policy": PRIVATE_WIKI_CSP,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Cache-Control": "no-store",
            }
        )
        return response


def create_private_wiki_mount(directory: str | Path) -> Mount:
    """Mount only generic UI assets at /wiki/; API identity remains fail-closed."""
    return Mount("/wiki", app=PrivateWikiStaticFiles(Path(directory)), name="private-wiki")
