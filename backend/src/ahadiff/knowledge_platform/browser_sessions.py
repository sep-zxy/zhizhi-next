"""Shared loopback pairing primitive with a separately restricted test policy."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import Actor, TokenAuthenticator
from .errors import DomainError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from starlette.requests import Request

TEST_OWNER = "portable-e2e-fixture"
TEST_ACTOR = "portable-e2e-browser"
TEST_SCOPES = frozenset({"knowledge:read", "knowledge:write", "knowledge:review"})
COOKIE_NAME = "wiki_local_test_session"
COOKIE_PATH = "/api/knowledge/v1"
AUTH_PREFIX = COOKIE_PATH + "/auth/local-test"
_COOKIE_VALUE = re.compile(r"[A-Za-z0-9_-]{32,128}")
_PAIRING_CODE = re.compile(r"[A-F0-9]{20}")


def local_test_actor() -> Actor:
    """One isolated test identity, without wildcard/apply/worker capabilities."""
    return Actor(TEST_OWNER, TEST_ACTOR, "user", TEST_SCOPES, frozenset({"public"}))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@dataclass
class _Pending:
    expires: float
    code_digest: str
    proof_digest: str
    approved_actor: Actor | None = None


@dataclass
class _Session:
    actor: Actor
    expires: float
    csrf: str
    proof_digest: str


class LoopbackBrowserSessions:
    """Memory-only session grants proven by an explicitly selected bearer on loopback.

    Constructing this boundary is explicit deployment configuration. It neither
    reads identity files nor issues a bearer token. A cookie with no approved
    pairing remains unauthenticated; owner headers and source IP never grant access.
    """

    cookie_name = COOKIE_NAME
    auth_prefix = AUTH_PREFIX
    fixture = True
    authentication_mode = "local_test"

    def __init__(
        self,
        origin: str,
        authenticator: TokenAuthenticator,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        url = urlsplit(origin)
        try:
            valid = (
                url.scheme == "http" and url.hostname == "127.0.0.1"
                and url.port is not None and 1024 <= url.port <= 65535
                and url.username is None and url.password is None
                and not url.path and not url.query and not url.fragment
                and origin == f"http://127.0.0.1:{url.port}"
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Browser sessions require an exact loopback HTTP origin")
        self.origin = origin
        self.host = url.netloc
        self.authenticator = authenticator
        self.clock = clock
        self._pending: dict[str, _Pending] = {}
        self._codes: dict[str, str] = {}
        self._sessions: dict[str, _Session] = {}
        self._requests: list[float] = []
        self._bound = False

    def bind_authenticator(self, authenticator: TokenAuthenticator) -> None:
        """Trusted factory binding revokes any state from an earlier identity source."""
        if self._bound:
            raise ValueError("A browser session controller belongs to one application")
        self.authenticator = authenticator
        self._pending.clear()
        self._codes.clear()
        self._sessions.clear()
        self._requests.clear()
        self._bound = True

    @staticmethod
    def _deny(code: str = "UNAUTHENTICATED", *, status: int = 401) -> DomainError:
        return DomainError(
            code, "Browser authentication is unavailable", status_code=status
        )

    @staticmethod
    def _headers(scope: Mapping[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in scope.get("headers", []):
            name = key.decode("latin-1").lower()
            if name in result and name in {
                "host", "origin", "cookie", "authorization", "x-wiki-csrf",
                "x-wiki-pairing-proof", "sec-fetch-site"
            }:
                raise LoopbackBrowserSessions._deny("BROWSER_REQUEST_REJECTED", status=403)
            result[name] = value.decode("latin-1")
        return result

    def _validate_request(
        self, scope: Mapping[str, Any], *, require_origin: bool
    ) -> dict[str, str]:
        headers = self._headers(scope)
        try:
            client_loopback = ipaddress.ip_address(scope["client"][0]).is_loopback
        except (KeyError, IndexError, TypeError, ValueError):
            client_loopback = False
        origin = headers.get("origin")
        if (
            not client_loopback or scope.get("scheme") != "http"
            or headers.get("host") != self.host
            or (origin is not None and origin != self.origin)
            or (require_origin and origin != self.origin)
            or headers.get("sec-fetch-site") not in {None, "same-origin", "none"}
        ):
            raise self._deny("BROWSER_ORIGIN_REJECTED", status=403)
        # Exempt auth endpoints must not ignore an explicitly invalid bearer.
        # A valid credential still does not replace the cookie proof or CSRF.
        if "authorization" in headers:
            self.authenticator.authenticate(headers["authorization"])
        return headers

    def _cookie(self, headers: dict[str, str]) -> str | None:
        raw = headers.get("cookie", "")
        if len(raw) > 4096:
            return None
        cookies = SimpleCookie()
        try:
            cookies.load(raw)
        except CookieError:
            return None
        morsel = cookies.get(self.cookie_name)
        value = morsel.value if morsel is not None else None
        return value if value is not None and _COOKIE_VALUE.fullmatch(value) else None

    def _expire(self) -> None:
        timestamp = self.clock()
        for key, pending in list(self._pending.items()):
            if pending.expires <= timestamp:
                self._pending.pop(key)
                self._codes.pop(pending.code_digest, None)
        self._sessions = {
            key: session for key, session in self._sessions.items() if session.expires > timestamp
        }
        self._requests = [stamp for stamp in self._requests if stamp > timestamp - 60]

    @staticmethod
    def _prove(headers: dict[str, str], expected: str) -> None:
        # Cookies are not port-isolated. A cookie stolen by another loopback port
        # must not be sufficient to obtain CSRF or any authenticated response.
        proof = headers.get("x-wiki-pairing-proof", "")
        if not _COOKIE_VALUE.fullmatch(proof) or not hmac.compare_digest(
            _digest(proof), expected
        ):
            raise LoopbackBrowserSessions._deny()

    def _response(self, value: dict[str, Any], *, cookie: str | None = None) -> JSONResponse:
        if not self.fixture:
            value = {**value, "fixture": False, "authentication_mode": self.authentication_mode}
        response = JSONResponse(value)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
            }
        )
        if cookie is not None:
            # No Max-Age/Expires: browser-session cookie, server TTL still 15 min.
            # This deliberately limited mode is HTTP loopback, never remote HTTP.
            response.set_cookie(
                self.cookie_name, cookie, httponly=True, samesite="strict", path=COOKIE_PATH
            )
        return response

    def authenticate(self, scope: Mapping[str, Any]) -> Actor:
        """Resolve only an approved session; write requests require Origin and CSRF."""
        unsafe = scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
        headers = self._validate_request(scope, require_origin=unsafe)
        self._expire()
        cookie = self._cookie(headers)
        session = self._sessions.get(_digest(cookie)) if cookie is not None else None
        if session is None:
            raise self._deny()
        self._prove(headers, session.proof_digest)
        if unsafe and not hmac.compare_digest(
            headers.get("x-wiki-csrf", "").encode("utf-8"), session.csrf.encode("ascii")
        ):
            raise self._deny("CSRF_REJECTED", status=403)
        return session.actor

    async def _body(self, request: Request) -> dict[str, Any]:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 4096:
                raise self._deny("INVALID_INPUT", status=422)
            raw.extend(chunk)
        try:
            value = json.loads(raw or b"{}")
        except (ValueError, UnicodeError):
            raise self._deny("INVALID_INPUT", status=422) from None
        if not isinstance(value, dict):
            raise self._deny("INVALID_INPUT", status=422)
        return cast("dict[str, Any]", value)

    async def request_pairing(self, request: Request) -> JSONResponse:
        headers = self._validate_request(request.scope, require_origin=True)
        if await self._body(request):
            raise self._deny("INVALID_INPUT", status=422)
        self._expire()
        if len(self._pending) >= 16 or len(self._requests) >= 20 or len(self._sessions) >= 16:
            raise self._deny("PAIRING_LIMIT", status=429)
        self._requests.append(self.clock())
        old_cookie = self._cookie(headers)
        if old_cookie is not None:
            old = self._pending.pop(_digest(old_cookie), None)
            self._sessions.pop(_digest(old_cookie), None)
            if old is not None:
                self._codes.pop(old.code_digest, None)
        cookie, code = secrets.token_urlsafe(32), secrets.token_hex(10).upper()
        proof = secrets.token_urlsafe(32)
        key, code_key = _digest(cookie), _digest(code)
        self._pending[key] = _Pending(self.clock() + 120, code_key, _digest(proof))
        self._codes[code_key] = key
        return self._response(
            {"status": "waiting_approval", "pairing_code": code,
             "pairing_proof": proof, "expires_in_seconds": 120, "fixture": self.fixture},
            cookie=cookie,
        )

    async def approve_pairing(self, request: Request) -> JSONResponse:
        headers = self._validate_request(request.scope, require_origin=False)
        actor = self.approval_actor(self.authenticator.authenticate(headers.get("authorization")))
        body = await self._body(request)
        code = body.get("pairing_code")
        if (
            set(body) != {"pairing_code"} or not isinstance(code, str)
            or not _PAIRING_CODE.fullmatch(code)
        ):
            raise self._deny("INVALID_INPUT", status=422)
        self._expire()
        key = self._codes.pop(_digest(code), None)
        pending = self._pending.get(key) if key is not None else None
        if pending is None or pending.approved_actor is not None:
            raise self._deny("PAIRING_UNAVAILABLE", status=409)
        pending.approved_actor = actor
        return self._response({"status": "pairing_approved", "fixture": self.fixture})

    def approval_actor(self, actor: Actor) -> Actor:
        """Policies must explicitly select and narrow an existing authenticated user."""
        raise self._deny("BROWSER_POLICY_REQUIRED", status=403)

    async def session_status(self, request: Request) -> JSONResponse:
        headers = self._validate_request(request.scope, require_origin=False)
        self._expire()
        cookie = self._cookie(headers)
        key = _digest(cookie) if cookie is not None else ""
        session = self._sessions.get(key)
        replacement: str | None = None
        if session is None:
            pending = self._pending.get(key)
            if pending is not None:
                self._prove(headers, pending.proof_digest)
            if pending is not None and pending.approved_actor is not None:
                if len(self._sessions) >= 16:
                    raise self._deny("PAIRING_LIMIT", status=429)
                replacement = secrets.token_urlsafe(32)
                session = _Session(
                    pending.approved_actor, self.clock() + 900, secrets.token_urlsafe(32),
                    pending.proof_digest,
                )
                self._sessions[_digest(replacement)] = session
                self._pending.pop(key)
            else:
                return self._response(
                    {"status": "waiting_approval" if pending is not None else "waiting_auth",
                     "fixture": self.fixture}
                )
        self._prove(headers, session.proof_digest)
        return self._response(
            {"status": "authenticated", "csrf_token": session.csrf,
             "expires_in_seconds": max(0, int(session.expires - self.clock())),
             "fixture": self.fixture,
             **({"scopes": sorted(session.actor.scopes)} if not self.fixture else {})},
            cookie=replacement,
        )

    async def logout(self, request: Request) -> JSONResponse:
        self.authenticate(request.scope)
        headers = self._headers(request.scope)
        cookie = self._cookie(headers)
        if cookie is not None:
            self._sessions.pop(_digest(cookie), None)
        response = self._response({"status": "waiting_auth", "fixture": self.fixture})
        response.delete_cookie(self.cookie_name, httponly=True, samesite="strict", path=COOKIE_PATH)
        return response

    def is_auth_endpoint(self, scope: Mapping[str, Any]) -> bool:
        """Only these exact routes perform their own independent authentication."""
        return (scope.get("path"), scope.get("method")) in {
            (self.auth_prefix + "/request", "POST"), (self.auth_prefix + "/approve", "POST"),
            (self.auth_prefix + "/session", "GET"), (self.auth_prefix + "/session", "HEAD"),
            (self.auth_prefix + "/logout", "POST"),
        }

    def routes(self) -> list[Route]:
        """Exact opt-in auth routes; every endpoint performs its own checks."""
        def endpoint(handler: Any) -> Any:
            async def wrapped(request: Request) -> JSONResponse:
                try:
                    return await handler(request)
                except DomainError as exc:
                    response = self._response(exc.as_dict())
                    response.status_code = exc.status_code
                    return response
            return wrapped

        prefix = self.auth_prefix
        return [
            Route(prefix + "/request", endpoint(self.request_pairing), methods=["POST"]),
            Route(prefix + "/approve", endpoint(self.approve_pairing), methods=["POST"]),
            Route(prefix + "/session", endpoint(self.session_status), methods=["GET"]),
            Route(prefix + "/logout", endpoint(self.logout), methods=["POST"]),
        ]


class LocalTestBrowserSessions(LoopbackBrowserSessions):
    """Only the isolated synthetic test identity; never an operational owner session."""

    def approval_actor(self, actor: Actor) -> Actor:
        if actor != local_test_actor():
            raise self._deny("TEST_IDENTITY_REQUIRED", status=403)
        return actor
