"""Explicit authenticated principals, separated from all request payloads."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

from .errors import DomainError

CLASSIFICATIONS = frozenset(
    {"public", "personal", "third_party_private", "employer", "secret", "unknown"}
)
_CLASSIFICATION_KEYS = frozenset(
    {"classification", "data_classification", "evidence_classification", "input_classification"}
)
_CLASSIFICATION_RANK = {
    "public": 0,
    "personal": 1,
    "third_party_private": 2,
    "employer": 3,
    "secret": 4,
    "unknown": 5,
}


def classification_labels(record: Any, *, default: str | None = "unknown") -> frozenset[str]:
    """Collect every explicit label; an outer public label cannot erase inner risks."""
    labels: set[str] = set()

    def label(value: Any) -> None:
        labels.add(value if isinstance(value, str) and value in CLASSIFICATIONS else "unknown")

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in cast("dict[str, Any]", value).items():
                if key in _CLASSIFICATION_KEYS:
                    label(item)
                elif key == "classification_labels":
                    if isinstance(item, list | tuple | set | frozenset) and item:
                        for member in cast("list[Any] | tuple[Any, ...] | set[Any]", item):
                            label(member)
                    else:
                        labels.add("unknown")
                else:
                    visit(item)
        elif isinstance(value, list | tuple):
            for item in cast("list[Any] | tuple[Any, ...]", value):
                visit(item)

    visit(record)
    if not labels and default is not None:
        labels.add(default if default in CLASSIFICATIONS else "unknown")
    return frozenset(labels)


def classify_record(record: Any, *, default: str = "unknown") -> str:
    """Return a display label, not an authorization shortcut for mixed classifications."""
    return max(classification_labels(record, default=default), key=_CLASSIFICATION_RANK.__getitem__)


def enforce_read_classification(actor: object, record: Any, *, default: str = "unknown") -> str:
    """Limit machine principals to deployment-configured classes before returning data."""
    if not isinstance(actor, Actor):
        raise DomainError("UNAUTHENTICATED", "Authentication is required", status_code=401)
    labels = classification_labels(record, default=default)
    if actor.kind != "user" and (
        labels & {"secret", "unknown"} or not labels <= actor.allowed_classifications
    ):
        raise DomainError(
            "CLASSIFICATION_FORBIDDEN",
            "The principal cannot read this data classification",
            status_code=403,
        )
    return max(labels, key=_CLASSIFICATION_RANK.__getitem__)


@dataclass(frozen=True, slots=True)
class Actor:
    """A principal created by trusted authentication, never by request-body parsing."""

    owner_id: str
    actor_id: str
    kind: str
    scopes: frozenset[str]
    allowed_classifications: frozenset[str] = frozenset({"public", "personal"})

    def __post_init__(self) -> None:
        if not self.owner_id or not self.actor_id or self.kind not in {"user", "agent", "service"}:
            raise ValueError("Invalid authenticated principal")
        object.__setattr__(self, "scopes", frozenset(self.scopes))
        allowed = frozenset(self.allowed_classifications)
        if not allowed <= CLASSIFICATIONS:
            raise ValueError("Unknown configured data classification")
        object.__setattr__(self, "allowed_classifications", allowed)

    def require(self, scope: str) -> None:
        """Enforce a capability, with wildcard reserved for server configuration."""
        if "*" not in self.scopes and scope not in self.scopes:
            raise DomainError("FORBIDDEN", "Required capability is missing", status_code=403)


def authenticated(actor: object, scope: str) -> None:
    """Reject caller-provided mappings and enforce the declared capability."""
    if not isinstance(actor, Actor):
        raise DomainError("UNAUTHENTICATED", "Authentication is required", status_code=401)
    actor.require(scope)


def request_actor(request: Any) -> Actor:
    """Read only an identity installed by verified authentication middleware."""
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, Actor):
        raise DomainError("UNAUTHENTICATED", "Authentication is required", status_code=401)
    return actor


class TokenAuthenticator:
    """Authenticate configured bearer tokens without keeping plaintext token copies.

    Configuration is supplied by the deployment's secret provider. This class does
    not mint credentials, fetch secrets, infer identities or offer a default token.
    """

    def __init__(self, tokens: Mapping[str, Actor]) -> None:
        if any(not token or len(token) < 24 for token in tokens):
            raise ValueError("Configured bearer credentials must be at least 24 characters")
        self._tokens = tuple(
            (hashlib.sha256(token.encode()).digest(), actor) for token, actor in tokens.items()
        )

    def authenticate(self, authorization: str | None) -> Actor:
        """Resolve an exact Bearer credential to its configured owner and scopes."""
        if not authorization or not authorization.startswith("Bearer "):
            raise DomainError("UNAUTHENTICATED", "A bearer credential is required", status_code=401)
        candidate = hashlib.sha256(authorization[7:].encode()).digest()
        matched = None
        for digest, actor in self._tokens:
            if hmac.compare_digest(digest, candidate):
                matched = actor
        if matched is None:
            raise DomainError("UNAUTHENTICATED", "Invalid bearer credential", status_code=401)
        return matched


class BrowserSessionAuthenticator(Protocol):
    """Trusted optional server boundary; request fields never create an Actor."""

    def authenticate(self, scope: Mapping[str, Any]) -> Actor: ...


class DomainAuthenticationMiddleware:
    """Minimal ASGI middleware; an absent/invalid bearer token never becomes owner."""

    def __init__(
        self,
        app: Any,
        authenticator: TokenAuthenticator,
        prefix: str = "/api/knowledge/v1",
        browser_authenticator: BrowserSessionAuthenticator | None = None,
    ) -> None:
        self.app = app
        self.authenticator = authenticator
        self.prefix = prefix
        self.browser_authenticator = browser_authenticator

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            if authorization or self.browser_authenticator is None:
                # An explicit invalid bearer never silently falls back to a cookie.
                actor = self.authenticator.authenticate(authorization)
            else:
                actor = cast("object", self.browser_authenticator.authenticate(scope))
            if not isinstance(actor, Actor):
                raise DomainError("UNAUTHENTICATED", "Authentication is required", status_code=401)
            scope.setdefault("state", {})["actor"] = actor
        except DomainError as exc:
            trace = scope.get("state", {}).get("trace_id")
            await JSONResponse(exc.as_dict(trace), status_code=exc.status_code)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)
