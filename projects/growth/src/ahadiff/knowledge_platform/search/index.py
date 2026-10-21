"""SQLite FTS5 projection with explicit Chinese character n-grams."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from typing import Any


def _tokens(text: str) -> list[str]:
    result = re.findall(r"[a-z0-9_]+", text.casefold())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(run)
        result.extend(run[index : index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(result))


class SearchIndex:
    """Disposable index; it cannot change or roll back canonical entries."""

    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.execute(
            "CREATE TABLE documents (document_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
            "scope TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE VIRTUAL TABLE text_index USING fts5("
            "document_id UNINDEXED, terms, tokenize='unicode61')"
        )

    def rebuild(self, documents: list[dict[str, Any]]) -> None:
        """Atomically replace the projection from canonical, scoped documents."""
        with self._lock, self.connection:
            self.connection.execute("DELETE FROM text_index")
            self.connection.execute("DELETE FROM documents")
            for item in documents:
                if item.get("deleted"):
                    continue
                document = dict(item)
                identifier = document.get("entry_id") or document["document_id"]
                scope = document.get("scope", "wiki")
                key = f"{scope}:{identifier}"
                metadata = document.get("metadata") or {}
                text = " ".join(
                    str(value)
                    for value in (
                        identifier,
                        document.get("title", ""),
                        document.get("body", ""),
                        metadata.get("aliases", []),
                        metadata.get("tags", []),
                        metadata.get("concept_id", ""),
                    )
                )
                self.connection.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?)",
                    (
                        key,
                        document["owner_id"],
                        scope,
                        int(document.get("revision", 1)),
                        json.dumps(document, ensure_ascii=False, sort_keys=True),
                    ),
                )
                self.connection.execute(
                    "INSERT INTO text_index VALUES (?,?)", (key, " ".join(_tokens(text)))
                )

    def search(
        self, query: str, *, owner_id: str, scope: str = "wiki", limit: int = 10
    ) -> list[dict[str, Any]]:
        """Filter identity/scope in SQL, not after returning private snippets."""
        from ahadiff.knowledge_platform.auth import classification_labels, classify_record

        if scope not in {"wiki", "sources", "learning"}:
            raise ValueError("Unknown search scope")
        terms = _tokens(query[:500])
        if not terms:
            return []
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in terms)
        with self._lock:
            rows = self.connection.execute(
                "SELECT documents.data, bm25(text_index) AS score FROM text_index "
                "JOIN documents USING(document_id) "
                "WHERE text_index MATCH ? AND owner_id=? AND scope=? "
                "ORDER BY score, documents.document_id LIMIT ?",
                (expression, owner_id, scope, min(max(limit, 1), 100)),
            ).fetchall()
        result = []
        for row in rows:
            item = json.loads(row["data"])
            body = str(item.get("body", ""))
            position = body.casefold().find(query.casefold())
            snippet = (
                body[max(0, position - 80) : max(0, position - 80) + 320]
                if position >= 0
                else body[:320]
            )
            headings = re.findall(r"^#{1,6}\s+(.+)$", body[: max(position, 0)], flags=re.MULTILINE)
            result.append(
                {
                    "entry_id": item.get("entry_id") or item.get("document_id"),
                    "revision": item.get("revision", 1),
                    "title": item.get("title", ""),
                    "matched_section": headings[-1] if headings else None,
                    "snippet": snippet,
                    "evidence_refs": item.get("evidence_refs", []),
                    "content_kind": item.get(
                        "content_kind",
                        (item.get("metadata") or {}).get("content_kind", "knowledge_synthesis"),
                    ),
                    "visibility": item.get("visibility", "private"),
                    "classification": classify_record(item),
                    "classification_labels": sorted(classification_labels(item)),
                    "scope": scope,
                    "stale_index": False,
                }
            )
        return result

    def close(self) -> None:
        """Release a disposable index without touching the source ledger."""
        self.connection.close()


class SearchService:
    """Build a current projection from the domain's authenticated read API."""

    def __init__(self, domain: Any) -> None:
        self.domain = domain

    def entries(self, actor: Any) -> list[dict[str, Any]]:
        """Page the whole catalog so entries beyond the first hundred are found."""
        result: list[dict[str, Any]] = []
        while True:
            page = self.domain.list_entries(actor=actor, limit=100, offset=len(result))
            result.extend(page)
            if len(page) < 100:
                return result

    def search(
        self, query: str, *, actor: Any, scope: str = "wiki", limit: int = 10
    ) -> list[dict[str, Any]]:
        """Never search RAW or learning facts under Wiki-only authority."""
        from ahadiff.knowledge_platform.auth import (
            classification_labels,
            classify_record,
            enforce_read_classification,
        )
        from ahadiff.knowledge_platform.errors import DomainError

        if scope not in {"wiki", "sources", "learning"}:
            raise DomainError("INVALID_INPUT", "Unknown search scope", status_code=422)
        required = "knowledge:read" if scope == "wiki" else f"knowledge:{scope}:read"
        if required not in actor.scopes and "*" not in actor.scopes:
            raise DomainError("FORBIDDEN", "Search scope is not authorized", status_code=403)
        if scope == "wiki":
            documents = self.entries(actor)
        else:
            documents = self._private_documents(actor, scope)
        for document in documents:
            enforce_read_classification(actor, document)
        index = SearchIndex()
        try:
            index.rebuild(documents)
            results = index.search(query, owner_id=actor.owner_id, scope=scope, limit=limit)
            if scope == "wiki":
                # A commit can race with rebuilding; never silently combine revisions.
                visible = []
                for item in results:
                    try:
                        current = self.domain.get_entry(item["entry_id"], actor=actor)
                    except DomainError as error:
                        if error.code == "NOT_FOUND":
                            continue
                        raise
                    if current.get("deleted"):
                        continue
                    enforce_read_classification(actor, current)
                    item["stale_index"] = current["revision"] != item["revision"]
                    item["current_revision"] = current["revision"]
                    item["classification"] = classify_record({"indexed": item, "current": current})
                    item["classification_labels"] = sorted(
                        classification_labels({"indexed": item, "current": current})
                    )
                    visible.append(item)
                results = visible
            return results
        finally:
            index.close()

    def _private_documents(self, actor: Any, scope: str) -> list[dict[str, Any]]:
        from ahadiff.knowledge_platform.auth import (
            classification_labels,
            classify_record,
            enforce_read_classification,
        )

        documents: dict[str, dict[str, Any]] = {}
        after = 0
        while True:
            events = self.domain.list_events(actor=actor, after=after, limit=100)
            for event in events:
                after = max(after, event["sequence"])
                event_type = str(event["type"])
                if scope == "learning" and event_type == "learning.outcome.submitted.v1":
                    item = self.domain.get_learning_outcome(event["aggregate_id"], actor=actor)
                    enforce_read_classification(actor, item)
                    payload = item.get("payload", item)
                    documents[event["aggregate_id"]] = {
                        "document_id": event["aggregate_id"],
                        "owner_id": actor.owner_id,
                        "scope": scope,
                        "revision": payload.get("version", 1),
                        "title": payload.get("metadata", {}).get("title", "学习原话"),
                        "body": payload.get("user_text", ""),
                        "content_kind": "user_expression",
                        "classification": classify_record(item),
                        "classification_labels": sorted(classification_labels(item)),
                        "visibility": "private",
                        "evidence_refs": [{"learning_outcome_id": event["aggregate_id"]}],
                    }
                elif scope == "sources" and event_type == "source.retained.v1":
                    item = self.domain.get_source(event["aggregate_id"], actor=actor)
                    enforce_read_classification(actor, item)
                    evidence = self.domain.read_evidence(
                        {"source_id": item["source_id"]}, actor=actor
                    )
                    documents[item["source_id"]] = {
                        "document_id": item["source_id"],
                        "owner_id": actor.owner_id,
                        "scope": scope,
                        "title": item.get("descriptor", {}).get("title", ""),
                        "body": evidence.get("raw_text", evidence.get("text", "")),
                        "content_kind": "source",
                        "classification": classify_record(item),
                        "classification_labels": sorted(classification_labels(item)),
                        "visibility": "private",
                        "evidence_refs": [{"source_id": item["source_id"]}],
                    }
            if len(events) < 100:
                return list(documents.values())
