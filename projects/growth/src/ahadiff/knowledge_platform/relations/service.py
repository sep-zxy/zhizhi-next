"""Read-only relation projections; a path is never an inferred knowledge fact."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any

from markdown_it import MarkdownIt

from ..auth import Actor, authenticated, classification_labels, classify_record
from ..contracts import fingerprint
from ..errors import DomainError, invalid

KINDS = frozenset({"navigation", "confirmed", "candidate"})
PUBLIC_RIGHTS = frozenset({"public", "public_domain", "licensed", "summary_allowed"})


def _reference_id(ref: dict[str, Any]) -> tuple[str, str] | None:
    values = [(key, ref[key]) for key in ("source_id", "learning_outcome_id") if ref.get(key)]
    if len(values) != 1 or not isinstance(values[0][1], str):
        return None
    return values[0]


def _navigation(body: str) -> list[str]:
    """Only real prose wikilinks count; code, images and URLs are not relations."""

    def wikilink(state: Any, silent: bool) -> bool:
        start = state.pos
        if not state.src.startswith("[[", start) or (start and state.src[start - 1] == "!"):
            return False
        end = state.src.find("]]", start + 2)
        if end < 0 or "\n" in state.src[start:end] or "[" in state.src[start + 2 : end]:
            return False
        if not silent:
            token = state.push("knowledge_wikilink", "", 0)
            token.content = state.src[start + 2 : end]
        state.pos = end + 2
        return True

    parser = MarkdownIt()
    parser.inline.ruler.before("link", "knowledge_wikilink", wikilink)
    result = []
    for block in parser.parse(body):
        if block.type != "inline":
            continue
        for token in block.children or []:
            if token.type != "knowledge_wikilink":
                continue
            target = token.content.split("|", 1)[0].split("#", 1)[0].strip()
            if target and "://" not in target and not target.startswith(("/", ".")):
                result.append(target)
    return result


class RelationService:
    """Rebuild an owner-scoped in-memory view, with no independently editable index.

    Structured declarations live in versioned formal Markdown metadata.relations.
    Confirmed edges additionally need durable review provenance and exact evidence.
    Every read rebuilds, so clearing or losing this object loses no knowledge.
    """

    def __init__(self, domain: Any) -> None:
        self.domain = domain

    @staticmethod
    def _options(visibility: str, kinds: list[str] | None = None) -> frozenset[str]:
        if not isinstance(visibility, str) or visibility not in {"all", "public"}:
            raise invalid("visibility must be all or public")
        if kinds is not None and (
            not isinstance(kinds, list)
            or any(not isinstance(kind, str) or kind not in KINDS for kind in kinds)
        ):
            raise invalid("Unknown relation kind")
        return KINDS if kinds is None else frozenset(kinds)

    @staticmethod
    def _public(entry: dict[str, Any]) -> bool:
        return (
            entry.get("visibility") == "public"
            and entry.get("publication_eligible") is True
            and classification_labels(entry) == frozenset({"public"})
        )

    @staticmethod
    def _node(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "entry_id": entry["entry_id"],
            "revision": entry["revision"],
            "title": entry["title"],
            "visibility": entry["visibility"],
            "data_classification": classify_record(entry),
            "classification_labels": sorted(classification_labels(entry)),
        }

    def _entries(self, actor: Actor, visibility: str) -> dict[str, dict[str, Any]]:
        entries = {}
        offset = 0
        while True:
            page = self.domain.list_entries(actor=actor, limit=100, offset=offset)
            for entry in page:
                if not entry.get("deleted") and (visibility == "all" or self._public(entry)):
                    entries[entry["entry_id"]] = entry
            if len(page) < 100:
                return entries
            offset += len(page)

    def _events(self, actor: Actor, aggregate_id: str, event_type: str) -> list[dict[str, Any]]:
        result = []
        after = 0
        while True:
            page = self.domain.list_events(
                actor=actor, aggregate_id=aggregate_id, types=[event_type], after=after, limit=100
            )
            result.extend(page)
            if len(page) < 100:
                return result
            after = page[-1]["sequence"]

    def _review_provenance(self, entry: dict[str, Any], actor: Actor) -> dict[str, Any] | None:
        """Never trust a metadata assertion that a model or user approved an edge."""
        for event in self._events(actor, entry["entry_id"], "knowledge.committed.v1"):
            payload = event["payload"]
            if payload.get("revision") != entry["revision"]:
                continue
            change = self.domain.get_change(payload["change_id"], actor=actor)
            if (
                change.get("state") != "committed"
                or change.get("committed_revision") != entry["revision"]
                or change.get("entry_id") != entry["entry_id"]
                or change.get("metadata") != entry.get("metadata")
            ):
                continue
            approvals = self._events(actor, change["change_id"], "change.reviewed.v1")
            for approval in reversed(approvals):
                covered = approval["payload"]
                if (
                    covered.get("state") == "approved"
                    and covered.get("patch_hash") == change["patch_hash"]
                    and "metadata" in covered.get("reviewed_fields", [])
                    and covered.get("reviewed_relations_hash")
                    == fingerprint(entry.get("metadata", {}).get("relations"))
                ):
                    return {
                        "change_id": change["change_id"],
                        "review_id": approval["payload"]["review_id"],
                        "patch_hash": change["patch_hash"],
                        "evidence_hash": change["evidence_hash"],
                        "reviewed_relations_hash": covered["reviewed_relations_hash"],
                    }
        return None

    def _evidence(
        self, declaration: dict[str, Any], entry: dict[str, Any], actor: Actor, visibility: str
    ) -> tuple[list[dict[str, Any]], bool, set[str]] | None:
        refs = declaration.get("evidence_refs", [])
        if not isinstance(refs, list) or len(refs) > 50:
            return None
        bound = {_reference_id(ref) for ref in entry.get("evidence_refs", [])}
        verified = bool(refs)
        projected = []
        labels: set[str] = set()
        for ref in refs:
            if not isinstance(ref, dict) or _reference_id(ref) is None:
                return None
            identity = _reference_id(ref)
            if identity not in bound:
                return None
            try:
                evidence = self.domain.read_evidence(ref, actor=actor)
            except DomainError as exc:
                if exc.code in {"NOT_FOUND", "CLASSIFICATION_FORBIDDEN", "BASE_STALE"}:
                    return None
                raise
            evidence_labels = classification_labels(evidence)
            labels.update(evidence_labels)
            if visibility == "public" and (
                identity[0] != "source_id"
                or evidence_labels != frozenset({"public"})
                or evidence.get("rights") not in PUBLIC_RIGHTS
            ):
                return None
            locator = ref.get("locator")
            quote = locator.get("quote") if isinstance(locator, dict) else None
            if (
                not isinstance(quote, str)
                or not quote.strip()
                or len(quote) > 4000
                or quote not in (evidence.get("text") or "")
                or ref.get("content_hash") != evidence.get("content_hash")
            ):
                verified = False
            safe = {identity[0]: identity[1], "content_hash": evidence["content_hash"]}
            if visibility != "public" and isinstance(quote, str) and len(quote) <= 4000:
                safe["locator"] = {"quote": quote}
            projected.append(safe)
        return projected, verified, labels

    @staticmethod
    def _edge(
        source: dict[str, Any], target: dict[str, Any], kind: str, **details: Any
    ) -> dict[str, Any]:
        labels = classification_labels(source) | classification_labels(target)
        labels |= frozenset(details.pop("evidence_labels", []))
        edge = {
            "source_entry_id": source["entry_id"],
            "target_entry_id": target["entry_id"],
            "source_revision": source["revision"],
            "target_revision": target["revision"],
            "kind": kind,
            "is_fact": kind == "confirmed",
            "classification_labels": sorted(labels),
            **details,
        }
        edge["data_classification"] = classify_record(edge)
        edge["edge_id"] = "rel_" + fingerprint(edge)
        return edge

    def rebuild(self, *, actor: Actor, visibility: str = "all") -> dict[str, Any]:
        """Return a deterministic disposable snapshot, or fail on concurrent revisions."""
        authenticated(actor, "knowledge:read")
        self._options(visibility)
        head = self.domain.writer.head()
        entries = self._entries(actor, visibility)
        names: dict[str, set[str]] = defaultdict(set)
        for entry_id, entry in entries.items():
            for name in [
                entry_id,
                entry["title"],
                *(entry.get("metadata", {}).get("aliases") or []),
            ]:
                if isinstance(name, str):
                    names[name].add(entry_id)
        edges = {}
        ignored = 0
        for entry in entries.values():
            for name in _navigation(entry["body"]):
                targets = names.get(name, set())
                if len(targets) != 1:
                    ignored += 1
                    continue
                target = entries[next(iter(targets))]
                edge = self._edge(entry, target, "navigation", relation_type="wikilink")
                edges[edge["edge_id"]] = edge
            declarations = entry.get("metadata", {}).get("relations", [])
            if not isinstance(declarations, list) or len(declarations) > 200:
                ignored += 1
                continue
            provenance = None
            checked_provenance = False
            for declaration in declarations:
                if not isinstance(declaration, dict):
                    ignored += 1
                    continue
                kind = declaration.get("kind")
                target_id = declaration.get("target_entry_id")
                target = entries.get(target_id) if isinstance(target_id, str) else None
                relation_type = declaration.get("relation_type")
                statement = declaration.get("statement")
                if (
                    not isinstance(kind, str)
                    or kind not in {"confirmed", "candidate"}
                    or target is None
                    or not isinstance(relation_type, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", relation_type)
                    or not isinstance(statement, str)
                    or not statement.strip()
                    or len(statement) > 2000
                ):
                    ignored += 1
                    continue
                evidence = self._evidence(declaration, entry, actor, visibility)
                if evidence is None:
                    ignored += 1
                    continue
                refs, verified, labels = evidence
                if kind == "confirmed" and not checked_provenance:
                    provenance = self._review_provenance(entry, actor)
                    checked_provenance = True
                declared_revision = declaration.get("target_revision")
                target_matches = (
                    isinstance(declared_revision, int)
                    and not isinstance(declared_revision, bool)
                    and declared_revision == target["revision"]
                )
                confirmed = (
                    kind == "confirmed" and verified and provenance is not None and target_matches
                )
                edge = self._edge(
                    entry,
                    target,
                    "confirmed" if confirmed else "candidate",
                    relation_type=relation_type,
                    statement=statement,
                    declared_target_revision=declared_revision if target_matches else None,
                    evidence_refs=refs,
                    evidence_labels=labels,
                    verification="reviewed_evidence" if confirmed else "pending_verification",
                    review_provenance=provenance if confirmed else None,
                )
                edges[edge["edge_id"]] = edge
        if self.domain.writer.head() != head:
            raise DomainError(
                "PROJECTION_STALE",
                "Formal revisions changed during rebuild",
                status_code=409,
                retryable=True,
            )
        result = {
            "nodes": [self._node(entries[key]) for key in sorted(entries)],
            "edges": [edges[key] for key in sorted(edges)],
            "diagnostics": {"ignored_declarations": ignored},
            "visibility": visibility,
            "inference": "none",
        }
        result["projection_revision"] = fingerprint(result)
        result["classification_labels"] = sorted(classification_labels(result, default="public"))
        result["data_classification"] = classify_record(result, default="public")
        return result

    @staticmethod
    def _incident(edge: dict[str, Any], entry_id: str, direction: str) -> str | None:
        if direction in {"out", "both"} and edge["source_entry_id"] == entry_id:
            return edge["target_entry_id"]
        if direction in {"in", "both"} and edge["target_entry_id"] == entry_id:
            return edge["source_entry_id"]
        return None

    @staticmethod
    def _view(
        snapshot: dict[str, Any], ids: set[str], edges: list[dict[str, Any]], **extra: Any
    ) -> dict[str, Any]:
        result = {
            "nodes": [node for node in snapshot["nodes"] if node["entry_id"] in ids],
            "edges": edges,
            "projection_revision": snapshot["projection_revision"],
            "visibility": snapshot["visibility"],
            "inference": "none",
            **extra,
        }
        result["classification_labels"] = sorted(classification_labels(result, default="public"))
        result["data_classification"] = classify_record(result, default="public")
        return result

    def neighbors(
        self,
        entry_id: str,
        *,
        actor: Actor,
        kinds: list[str] | None = None,
        direction: str = "both",
        visibility: str = "all",
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return only direct edges; hidden endpoints never appear as dangling IDs."""
        kinds = self._options(visibility, kinds)
        self.domain._validate_page(limit, 0)
        if direction not in {"in", "out", "both"}:
            raise invalid("Unknown relation direction")
        snapshot = self.rebuild(actor=actor, visibility=visibility)
        if entry_id not in {node["entry_id"] for node in snapshot["nodes"]}:
            raise DomainError("NOT_FOUND", "Entry is not visible", status_code=404)
        eligible = [
            edge
            for edge in snapshot["edges"]
            if edge["kind"] in kinds and self._incident(edge, entry_id, direction) is not None
        ]
        edges = eligible[:limit]
        ids = {entry_id} | {
            edge[key] for edge in edges for key in ("source_entry_id", "target_entry_id")
        }
        return self._view(
            snapshot, ids, edges, root_entry_id=entry_id, truncated=len(eligible) > limit
        )

    def traverse(
        self,
        entry_id: str,
        *,
        actor: Actor,
        max_depth: int = 2,
        max_nodes: int = 100,
        kinds: list[str] | None = None,
        direction: str = "both",
        visibility: str = "all",
    ) -> dict[str, Any]:
        """Bounded BFS returns reachability, never transitive confirmation or mastery."""
        kinds = self._options(visibility, kinds)
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 1 <= max_depth <= 3:
            raise invalid("max_depth must be between 1 and 3")
        if (
            isinstance(max_nodes, bool)
            or not isinstance(max_nodes, int)
            or not 1 <= max_nodes <= 200
        ):
            raise invalid("max_nodes must be between 1 and 200")
        if direction not in {"in", "out", "both"}:
            raise invalid("Unknown relation direction")
        snapshot = self.rebuild(actor=actor, visibility=visibility)
        if entry_id not in {node["entry_id"] for node in snapshot["nodes"]}:
            raise DomainError("NOT_FOUND", "Entry is not visible", status_code=404)
        visited = {entry_id: 0}
        queue = deque([entry_id])
        edges = {}
        truncated = False
        while queue:
            current = queue.popleft()
            if visited[current] >= max_depth:
                continue
            for edge in snapshot["edges"]:
                other = self._incident(edge, current, direction)
                if edge["kind"] not in kinds or other is None:
                    continue
                if other not in visited:
                    if len(visited) >= max_nodes:
                        truncated = True
                        continue
                    visited[other] = visited[current] + 1
                    queue.append(other)
                if len(edges) >= 1000 and edge["edge_id"] not in edges:
                    truncated = True
                    continue
                edges[edge["edge_id"]] = edge
        return self._view(
            snapshot,
            set(visited),
            [edges[key] for key in sorted(edges)],
            root_entry_id=entry_id,
            distances=visited,
            max_depth=max_depth,
            truncated=truncated,
        )
