"""One explicitly activated Pi search capability, not a general bearer gateway.

The factory supplies the existing source proof and durable attempt/receipt hooks.
This boundary never creates an identity, accepts caller identity fields, or lets
its agent token fall through to unrelated domain routes.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import re
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from ..auth import Actor, classification_labels, classify_record, enforce_read_classification
from ..search.index import SearchService
from .validation import contains_obvious_secret

if TYPE_CHECKING:
    from collections.abc import Callable

QUERY = "正式正文与原始来源证据"
AGENT = "portable-pi-acceptance-agent"
_PATH = "/api/knowledge/v1/search"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HASH = re.compile(r"[0-9a-f]{64}")


class _ScopeError(ValueError):
    pass


class _SingleSource:
    """SearchService sees only the independently authorized exact source snapshot."""

    def __init__(self, actor: Actor, read: Callable[[], dict[str, Any]]):
        self.actor, self.read = actor, read
        self.snapshot: dict[str, Any] | None = None

    def current(self) -> dict[str, Any]:
        item = copy.deepcopy(self.read())
        if (
            not isinstance(item, dict)
            or item.get("owner_id") != self.actor.owner_id
            or not isinstance(item.get("entry_id"), str)
            or not _ID.fullmatch(item["entry_id"])
            or type(item.get("revision")) is not int
            or item["revision"] < 1
            or not isinstance(item.get("content_hash"), str)
            or not _HASH.fullmatch(item["content_hash"])
            or not isinstance(item.get("body"), str)
            or hashlib.sha256(item["body"].encode("utf-8")).hexdigest() != item["content_hash"]
            or not isinstance(item.get("title"), str)
            or not item["title"]
            or item.get("deleted")
            or item.get("visibility") != "private"
            or contains_obvious_secret(item)
        ):
            raise _ScopeError
        enforce_read_classification(self.actor, item)
        # Do not send owner paths, arbitrary metadata, RAW or unsupported evidence
        # fields into search/model context. This does not replace factory proof.
        source = {
            key: item[key]
            for key in ("owner_id", "entry_id", "revision", "content_hash", "title", "body")
        }
        refs = item.get("evidence_refs", [])
        if not isinstance(refs, list) or len(refs) > 40:
            raise _ScopeError
        source["evidence_refs"] = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise _ScopeError
            safe: dict[str, Any] = {}
            for key in ("source_id", "learning_outcome_id", "external_document_id"):
                if key in ref:
                    if not isinstance(ref[key], str) or not _ID.fullmatch(ref[key]):
                        raise _ScopeError
                    safe[key] = ref[key]
            for key in ("revision", "source_revision"):
                if key in ref:
                    if type(ref[key]) is not int or ref[key] < 1:
                        raise _ScopeError
                    safe[key] = ref[key]
            if "content_hash" in ref:
                if not isinstance(ref["content_hash"], str) or not _HASH.fullmatch(
                    ref["content_hash"]
                ):
                    raise _ScopeError
                safe["content_hash"] = ref["content_hash"]
            if not any(
                key in safe for key in ("source_id", "learning_outcome_id", "external_document_id")
            ):
                raise _ScopeError
            source["evidence_refs"].append(safe)
        source.update(
            visibility="private",
            classification=classify_record(item),
            classification_labels=sorted(classification_labels(item)),
        )
        return source

    def list_entries(self, *, actor: Actor, limit: int, offset: int) -> list[dict[str, Any]]:
        if actor != self.actor or limit != 100 or offset != 0:
            raise _ScopeError
        self.snapshot = self.current()
        return [self.snapshot]

    def get_entry(self, entry_id: str, *, actor: Actor) -> dict[str, Any]:
        current = self.current()
        if actor != self.actor or current != self.snapshot or entry_id != current["entry_id"]:
            raise _ScopeError
        return current


class PiSearchBoundary:
    """Fixed loopback search only; all other users retain the original app policy."""

    def __init__(
        self,
        app: Any,
        *,
        agent_token: str,
        origin: str,
        actor: Actor,
        read_source: Callable[[], dict[str, Any]],
        reserve_search: Callable[[str], bool],
        record_result: Callable[[dict[str, Any]], None],
    ) -> None:
        url = urlsplit(origin)
        try:
            valid_origin = (
                url.scheme == "http"
                and url.hostname == "127.0.0.1"
                and url.port is not None
                and 1024 <= url.port <= 65535
                and origin == f"http://127.0.0.1:{url.port}"
            )
        except ValueError:
            valid_origin = False
        if (
            not valid_origin
            or not isinstance(agent_token, str)
            or len(agent_token) < 24
            or not agent_token.isascii()
            or any(ord(char) < 33 or ord(char) > 126 for char in agent_token)
            or not isinstance(actor, Actor)
            or actor.kind != "agent"
            or actor.actor_id != AGENT
            or actor.scopes != frozenset({"knowledge:read"})
            or not actor.allowed_classifications
            or not actor.allowed_classifications <= {"public", "personal"}
            or not all(
                callable(callback) for callback in (read_source, reserve_search, record_result)
            )
        ):
            raise ValueError("Invalid fixed Pi search capability")
        self.app, self.actor = app, actor
        self._token_digest = hashlib.sha256(("Bearer " + agent_token).encode("ascii")).digest()
        self.host = url.netloc.encode("ascii")
        self.read_source, self.reserve_search, self.record_result = (
            read_source,
            reserve_search,
            record_result,
        )

    def _matches(self, value: bytes) -> bool:
        return hmac.compare_digest(hashlib.sha256(value).digest(), self._token_digest)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        headers = scope.get("headers", [])
        authorization = [value for key, value in headers if key.lower() == b"authorization"]
        if not any(self._matches(value) for value in authorization):
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        trace = str(uuid.uuid4())
        try:
            names = [key.lower() for key, _ in headers]
            values = {key.lower(): value for key, value in headers}
            if (
                len(authorization) != 1
                or len(names) != len(set(names))
                or values.get(b"host") != self.host
                or b"origin" in values
                or b"cookie" in values
                or b"transfer-encoding" in values
                or values.get(b"content-length", b"0") != b"0"
                or scope.get("method") != "GET"
                or scope.get("path") != _PATH
                or scope.get("raw_path", _PATH.encode()) != _PATH.encode()
                or scope.get("scheme") != "http"
            ):
                raise _ScopeError
            raw_query = scope.get("query_string", b"")
            if not isinstance(raw_query, bytes) or len(raw_query) > 512:
                raise _ScopeError
            pairs = parse_qsl(
                raw_query.decode("ascii"), keep_blank_values=True, strict_parsing=True
            )
            if len(pairs) != 2 or dict(pairs) != {"q": QUERY, "limit": "5"}:
                raise _ScopeError
            supplied_trace = values.get(b"x-trace-id")
            if supplied_trace is not None:
                proposed_trace = supplied_trace.decode("ascii")
                if str(uuid.UUID(proposed_trace)) != proposed_trace:
                    raise _ScopeError
                trace = proposed_trace
            # ASGI transports may emit an empty nonterminal chunk even for an
            # empty GET. Drain a bounded number, never accept actual body bytes.
            async with asyncio.timeout(2):
                for _ in range(4):
                    event = await receive()
                    if event.get("type") != "http.request" or event.get("body"):
                        raise _ScopeError
                    if not event.get("more_body"):
                        break
                else:
                    raise _ScopeError
            if await run_in_threadpool(self.reserve_search, trace) is not True:
                raise _ScopeError
            envelope = await run_in_threadpool(self._search, trace)
            if await run_in_threadpool(self.record_result, copy.deepcopy(envelope)) is not None:
                raise _ScopeError
            response = JSONResponse(envelope, headers={"Cache-Control": "no-store"})
        except Exception:
            response = JSONResponse(
                {
                    "error": {
                        "code": "PI_SEARCH_SCOPE_BLOCKED",
                        "retryable": False,
                        "detail": "Fixed Pi search is unavailable",
                    },
                    "trace_id": trace,
                },
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        await response(scope, receive, send)

    def _search(self, trace: str) -> dict[str, Any]:
        source = _SingleSource(self.actor, self.read_source)
        results = SearchService(source).search(QUERY, actor=self.actor, scope="wiki", limit=5)
        if source.snapshot is None or source.current() != source.snapshot:
            raise _ScopeError
        for result in results:
            if (
                result["entry_id"] != source.snapshot["entry_id"]
                or result["revision"] != source.snapshot["revision"]
                or result["stale_index"]
            ):
                raise _ScopeError
            result["content_hash"] = source.snapshot["content_hash"]
        data = {"results": results, **({"data_classification": "public"} if not results else {})}
        enforce_read_classification(self.actor, data)
        return {
            "data": data,
            "task_id": None,
            "trace_id": trace,
            "data_classification": classify_record(data),
            "classification_labels": sorted(classification_labels(data)),
        }
