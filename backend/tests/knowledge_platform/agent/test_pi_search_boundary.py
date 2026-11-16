"""Synthetic identities, actual ASGI and SearchService; no model or live server."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import replace
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.model.pi_search_boundary import AGENT, QUERY, PiSearchBoundary

TOKEN = "INERT-PI-SEARCH-TEST-IDENTITY-ONLY"
ORIGIN = "http://127.0.0.1:9876"
ACTOR = Actor("synthetic-owner", AGENT, "agent", frozenset({"knowledge:read"}))
PATH = "/api/knowledge/v1/search?" + urlencode({"q": QUERY, "limit": "5"})


@pytest.fixture
def harness():
    text = "合成材料：正式正文与原始来源证据分别保留。候选需要独立复核。"
    source = {
        "owner_id": ACTOR.owner_id,
        "entry_id": str(uuid4()),
        "revision": 2,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "title": "合成来源",
        "body": text,
        "visibility": "private",
        "data_classification": "personal",
        "evidence_refs": [
            {
                "source_id": str(uuid4()),
                "source_revision": 1,
                "object_path": "INTERNAL-LOCATION-MUST-NOT-LEAK",
            }
        ],
        "metadata": {"internal_only": "INTERNAL-METADATA-MUST-NOT-LEAK"},
    }
    calls = {"fallback": [], "reserved": [], "recorded": [], "reads": 0}

    async def app(scope, receive, send):
        calls["fallback"].append(scope)
        await send({"type": "http.response.start", "status": 418, "headers": []})
        await send({"type": "http.response.body", "body": b"original-app"})

    def read():
        calls["reads"] += 1
        return copy.deepcopy(source)

    def reserve(trace):
        if calls["reserved"]:
            return False
        calls["reserved"].append(trace)
        return True

    def record(value):
        calls["recorded"].append(value)

    options = {
        "agent_token": TOKEN,
        "origin": ORIGIN,
        "actor": ACTOR,
        "read_source": read,
        "reserve_search": reserve,
        "record_result": record,
    }
    return app, options, source, calls


def request(boundary, path=PATH, *, headers=None, method="GET", content=None):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=boundary), base_url=ORIGIN
        ) as client:
            return await client.request(
                method,
                path,
                headers=headers or {"Authorization": "Bearer " + TOKEN},
                content=content,
            )

    return asyncio.run(run())


def test_real_search_safe_projection_and_receipt_before_success(harness):
    app, options, source, calls = harness
    boundary = PiSearchBoundary(app, **options)
    trace = str(uuid4())
    response = request(boundary, headers={"Authorization": "Bearer " + TOKEN, "X-Trace-ID": trace})
    assert response.status_code == 200
    value = response.json()
    assert value == calls["recorded"][0]
    assert value["trace_id"] == trace and value["task_id"] is None
    assert value["data_classification"] == "personal"
    assert value["classification_labels"] == ["personal"]
    item = value["data"]["results"][0]
    assert (item["entry_id"], item["revision"], item["content_hash"]) == (
        source["entry_id"],
        2,
        source["content_hash"],
    )
    assert item["snippet"] == source["body"]
    assert item["evidence_refs"] == [
        {"source_id": source["evidence_refs"][0]["source_id"], "source_revision": 1}
    ]
    assert item["stale_index"] is False and item["current_revision"] == 2
    assert "owner_id" not in response.text and "INTERNAL-" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert calls["reserved"] == [trace] and not calls["fallback"]
    assert request(boundary).status_code == 403
    assert len(calls["recorded"]) == 1


@pytest.mark.parametrize(
    "path,method",
    [
        ("/health", "GET"),
        ("/api/knowledge/v1/entries", "GET"),
        ("/api/knowledge/v1/tasks/test", "GET"),
        ("/api/knowledge/v1/changes", "POST"),
        ("/api/knowledge/v1/auth/owner/approve", "POST"),
        (PATH, "POST"),
        ("/api/knowledge/v1/search", "GET"),
        (PATH + "&scope=wiki", "GET"),
        (PATH + "&q=extra", "GET"),
        (PATH + "&limit=5", "GET"),
        (PATH.replace("limit=5", "limit=05"), "GET"),
        ("/api/knowledge/v1/search?" + urlencode({"q": QUERY + "\n", "limit": "5"}), "GET"),
    ],
)
def test_agent_has_no_other_route_or_query_capability(harness, path, method):
    app, options, _, calls = harness
    response = request(PiSearchBoundary(app, **options), path, method=method)
    assert response.status_code == 403
    assert not calls["fallback"] and not calls["reserved"] and calls["reads"] == 0


@pytest.mark.parametrize(
    "extra",
    [
        [("Host", "127.0.0.1:9877")],
        [("Origin", ORIGIN)],
        [("Cookie", "unused=1")],
        [("Authorization", "Bearer unrelated")],
        [("Host", "127.0.0.1:9876"), ("Host", "127.0.0.1:9876")],
        [("X-Trace-ID", "PRIVATE-ERROR-SENTINEL")],
        [("Transfer-Encoding", "chunked")],
    ],
)
def test_headers_fail_before_reserving_and_never_echo_raw_values(harness, extra):
    app, options, _, calls = harness
    response = request(
        PiSearchBoundary(app, **options), headers=[("Authorization", "Bearer " + TOKEN), *extra]
    )
    assert response.status_code == 403
    assert not calls["reserved"] and not calls["recorded"] and not calls["fallback"]
    assert "PRIVATE-ERROR-SENTINEL" not in response.text and TOKEN not in response.text


def test_nonempty_get_body_has_no_side_effect(harness):
    app, options, _, calls = harness
    assert request(PiSearchBoundary(app, **options), content=b"not-allowed").status_code == 403
    assert not calls["reserved"] and calls["reads"] == 0


@pytest.mark.parametrize("authorization", ["", "Bearer someone-else", "bearer " + TOKEN])
def test_other_authentication_is_unchanged_not_adopted(harness, authorization):
    app, options, _, calls = harness
    response = request(PiSearchBoundary(app, **options), headers={"Authorization": authorization})
    assert response.status_code == 418 and response.text == "original-app"
    assert len(calls["fallback"]) == 1 and not calls["reserved"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", "another-owner"),
        ("revision", True),
        ("content_hash", "0" * 64),
        ("visibility", "public"),
        ("deleted", True),
        ("data_classification", "employer"),
        ("evidence_refs", [{"source_id": "source\n"}]),
    ],
)
def test_source_must_be_exact_private_authorized_version(harness, field, value):
    app, options, source, calls = harness
    source[field] = value
    response = request(PiSearchBoundary(app, **options))
    assert response.status_code == 403 and not calls["recorded"]
    assert len(calls["reserved"]) == 1 and not calls["fallback"]


def test_stale_source_does_not_mix_revisions_or_record_success(harness):
    app, options, source, calls = harness

    def changed():
        calls["reads"] += 1
        return {**source, "revision": 2 if calls["reads"] == 1 else 3}

    options["read_source"] = changed
    assert request(PiSearchBoundary(app, **options)).status_code == 403
    assert len(calls["reserved"]) == 1 and not calls["recorded"]


def test_empty_actual_search_is_truthful_not_fabricated(harness):
    app, options, source, calls = harness
    source["title"], source["body"] = "XYZ", "xyz abc"
    source["content_hash"] = hashlib.sha256(source["body"].encode()).hexdigest()
    response = request(PiSearchBoundary(app, **options))
    assert response.status_code == 200
    assert response.json()["data"]["results"] == []
    assert response.json()["data_classification"] == "public"
    assert len(calls["recorded"]) == 1


def test_receipt_callback_cannot_silently_return_a_failure(harness):
    app, options, _, calls = harness
    options["record_result"] = lambda value: False
    response = request(PiSearchBoundary(app, **options))
    assert response.status_code == 403 and len(calls["reserved"]) == 1
    assert not calls["recorded"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("scheme", "https"),
        ("raw_path", b"/api/knowledge/v1/%73earch"),
        ("query_string", b"x" * 513),
    ],
)
def test_noncanonical_asgi_request_is_blocked(harness, field, value):
    app, options, _, calls = harness
    events = []
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/api/knowledge/v1/search",
        "raw_path": b"/api/knowledge/v1/search",
        "query_string": PATH.split("?", 1)[1].encode(),
        "headers": [(b"host", b"127.0.0.1:9876"), (b"authorization", ("Bearer " + TOKEN).encode())],
        field: value,
    }

    async def send(event):
        events.append(event)

    async def receive():
        raise AssertionError("Invalid header/path must not read body")

    asyncio.run(PiSearchBoundary(app, **options)(scope, receive, send))
    assert events[0]["status"] == 403 and not calls["reserved"]


def test_empty_chunk_stream_is_bounded(harness):
    app, options, _, calls = harness
    events, reads = [], []
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/api/knowledge/v1/search",
        "query_string": PATH.split("?", 1)[1].encode(),
        "headers": [(b"host", b"127.0.0.1:9876"), (b"authorization", ("Bearer " + TOKEN).encode())],
    }

    async def send(event):
        events.append(event)

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"", "more_body": True}

    asyncio.run(PiSearchBoundary(app, **options)(scope, receive, send))
    assert events[0]["status"] == 403 and len(reads) == 4
    assert not calls["reserved"]


@pytest.mark.parametrize("callback", ["read_source", "reserve_search", "record_result"])
def test_callback_exception_is_fixed_failure_without_raw_secrets(harness, callback):
    app, options, _, calls = harness

    def fail(*args):
        raise RuntimeError("PRIVATE-ERROR-SENTINEL " + TOKEN)

    options[callback] = fail
    response = request(PiSearchBoundary(app, **options))
    assert response.status_code == 403
    assert "PRIVATE-ERROR-SENTINEL" not in response.text and TOKEN not in response.text
    assert not calls["recorded"] and not calls["fallback"]


@pytest.mark.parametrize(
    "actor",
    [
        replace(ACTOR, kind="user"),
        replace(ACTOR, actor_id="other-agent"),
        replace(ACTOR, scopes=frozenset({"knowledge:read", "knowledge:write"})),
        replace(ACTOR, allowed_classifications=frozenset({"employer"})),
    ],
)
def test_constructor_rejects_broader_identity(harness, actor):
    app, options, _, _ = harness
    options["actor"] = actor
    with pytest.raises(ValueError):
        PiSearchBoundary(app, **options)


def test_agent_websocket_never_reaches_application(harness):
    app, options, _, calls = harness
    events = []

    async def send(value):
        events.append(value)

    async def receive():
        raise AssertionError("Must not receive websocket content")

    asyncio.run(
        PiSearchBoundary(app, **options)(
            {"type": "websocket", "headers": [(b"authorization", ("Bearer " + TOKEN).encode())]},
            receive,
            send,
        )
    )
    assert events == [{"type": "websocket.close", "code": 1008}]
    assert not calls["fallback"]


def test_absent_authentication_is_original_policy(harness):
    app, options, _, calls = harness
    events = []

    async def send(value):
        events.append(value)

    async def receive():
        return {"type": "http.request", "body": b""}

    asyncio.run(PiSearchBoundary(app, **options)({"type": "http", "headers": []}, receive, send))
    assert events[0]["status"] == 418 and len(calls["fallback"]) == 1
    assert TOKEN not in json.dumps(events, default=str)
