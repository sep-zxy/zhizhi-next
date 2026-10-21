"""Narrow original-summary eligibility, separate from RAW copying rights.

These are engineering policy checks, not a legal safe-harbor determination.
Only trusted acquisition/generation/review code may supply the keyword proofs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from .auth import Actor, authenticated, classification_labels
from .contracts import MAJOR_RISKS, fingerprint, new_id, now, text_hash
from .errors import DomainError, invalid

POLICY_ID = "original-factual-synthesis-v1"
PUBLICATION_CHECKS = frozenset(
    {
        "facts_supported",
        "expression_independent",
        "privacy_clear",
        "provenance_complete",
        "assets_clear",
        "attribution_complete",
        "restrictions_clear",
    }
)
PROOF_FIELDS = frozenset(
    {
        "external_document_manifest",
        "derivative_input_manifest",
        "publication_review",
        "publication_certificate",
        "publication_certificate_id",
        "publication_attestation",
        "public_artifact",
    }
)


def _explicit_private(value: dict[str, Any]) -> bool:
    """Private transport intent cannot be erased by a separated public-document hint."""
    return (
        value.get("publication_intent") == "private"
        or value.get("metadata", {}).get("publication_intent") == "private"
        or value.get("payload", {}).get("publication_intent") == "private"
        or value.get("payload", {}).get("metadata", {}).get("publication_intent") == "private"
    )


def reject_publication_proofs(value: Any) -> None:
    """Request or model metadata cannot mint acquisition or publication authority."""
    if isinstance(value, dict):
        if PROOF_FIELDS.intersection(value):
            raise DomainError(
                "FORBIDDEN", "Publication proof is not a payload capability", status_code=403
            )
        for item in value.values():
            reject_publication_proofs(item)
    elif isinstance(value, list):
        for item in value:
            reject_publication_proofs(item)


def _safe_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise invalid("External document URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in {None, 443}
        or parsed.hostname in {"localhost", "localhost.localdomain"}
        or parsed.hostname.endswith((".local", ".internal"))
        or re.search(
            r"(?:login|signin|auth|private|password|token|secret)", unquote(parsed.path), re.I
        )
    ):
        raise invalid("External document URL is not eligible for clean input")
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global:
            raise invalid("External document address must be public")
    except ValueError:
        pass
    return value


def _artifact(change: dict[str, Any], documents: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = {}
    for key in ("tags", "aliases", "categories"):
        value = change.get("metadata", {}).get(key)
        if value is not None:
            if (
                not isinstance(value, list)
                or len(value) > 100
                or any(not isinstance(item, str) or len(item) > 200 for item in value)
            ):
                raise invalid("Public metadata must contain bounded string arrays")
            metadata[key] = value
    urls = sorted({item["canonical_url"] for item in documents})
    return {
        "title": change["title"],
        "body": change["body"],
        "entry_type": change["entry_type"],
        "metadata": metadata,
        "citations": [{"url": url, "title": urlsplit(url).hostname} for url in urls],
    }


def _scan_artifact(artifact: dict[str, Any], documents: list[dict[str, Any]]) -> None:
    """Conservative alarms, never a claim that short overlap makes reuse lawful."""
    body = artifact["body"]
    if len(body) > 20_000:
        raise DomainError("PUBLICATION_BLOCKED", "Original summary is too long", status_code=422)
    content = (
        artifact["title"]
        + "\n"
        + body
        + "\n"
        + json.dumps(artifact["metadata"], ensure_ascii=False)
    )
    if re.search(
        r"我的理解|我的观点|我认为|my understanding|I believe|Bearer\s|PRIVATE KEY|sk-[A-Za-z0-9]|"
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        content,
        re.I,
    ):
        raise DomainError(
            "PUBLICATION_BLOCKED",
            "Personal or sensitive content needs separate review",
            status_code=422,
        )
    if re.search(r"^\s*\|?.+\|.+\n\s*\|?\s*:?-{3,}", body, re.M):
        raise DomainError(
            "PUBLICATION_BLOCKED", "Reproduced tables are outside this policy", status_code=422
        )
    citation_urls = {item["url"] for item in artifact["citations"]}
    for token in MarkdownIt().parse(body):
        if token.type in {"fence", "code_block", "html_block", "blockquote_open"}:
            raise DomainError(
                "PUBLICATION_BLOCKED", "Code, HTML and quotations are excluded", status_code=422
            )
        for child in token.children or []:
            if child.type in {"image", "code_inline", "html_inline"}:
                raise DomainError(
                    "PUBLICATION_BLOCKED", "Assets and code are excluded", status_code=422
                )
            if child.type == "link_open" and child.attrGet("href") not in citation_urls:
                raise DomainError(
                    "PUBLICATION_BLOCKED", "Public links must match safe citations", status_code=422
                )
    tokens = re.findall(r"[\u3400-\u9fff]|[a-z0-9]+", content.casefold())
    width = 24 if any(re.search(r"[\u3400-\u9fff]", token) for token in tokens) else 12
    spans = {tuple(tokens[i : i + width]) for i in range(max(0, len(tokens) - width + 1))}
    for document in documents:
        source_tokens = re.findall(r"[\u3400-\u9fff]|[a-z0-9]+", document["text"].casefold())
        if spans and any(
            tuple(source_tokens[i : i + width]) in spans
            for i in range(max(0, len(source_tokens) - width + 1))
        ):
            raise DomainError(
                "PUBLICATION_BLOCKED",
                "Substantial verbatim overlap requires separate rights",
                status_code=422,
            )


class PublicationMethods:
    """Domain-owned immutable acquisition facts and bounded review certificates."""

    def _register_external_document(
        self,
        connection: Any,
        source: dict[str, Any],
        intake: dict[str, Any],
        manifest: dict[str, Any],
        data: bytes,
    ) -> str | None:
        labels = classification_labels(intake) | classification_labels(source, default="public")
        if (
            labels - {"public", "personal"}
            or _explicit_private(intake)
            or _explicit_private(source.get("descriptor", {}))
        ):
            return None
        if (
            manifest.get("authenticated") is not False
            or manifest.get("publicly_accessible") is not True
            or manifest.get("context_separated") is not True
            or manifest.get("content_kind") not in {"external_article", "external_post"}
            or manifest.get("privacy_assessment") != "not_assessed"
            or manifest.get("observed_restrictions") != []
            or manifest.get("fetcher_version") != "anonymous-safe-fetch-v1"
        ):
            return None
        text = manifest.get("body_text")
        if not isinstance(text, str) or not text.strip() or len(text) > 1_000_000:
            raise invalid("External document requires separated bounded body text")
        try:
            actual = json.loads(data.decode("utf-8"))["document"]["body_text"]
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise invalid(
                "External manifest must bind the actual retained document bundle"
            ) from exc
        if actual != text or text_hash(text) != manifest.get("content_hash"):
            raise DomainError(
                "EVIDENCE_REQUIRED", "External document body hash mismatch", status_code=422
            )
        canonical_url = _safe_url(manifest.get("canonical_url"))
        _safe_url(manifest.get("final_url"))
        if not isinstance(manifest.get("captured_at"), str) or not manifest["captured_at"]:
            raise invalid("External document acquisition time is required")
        acquisition = {key: value for key, value in manifest.items() if key != "body_text"}
        document = {
            "document_id": new_id("doc"),
            "owner_id": source["owner_id"],
            "revision": 1,
            "origin_source_id": source["source_id"],
            "origin_source_hash": source["content_hash"],
            "text": text,
            "content_hash": text_hash(text),
            "canonical_url": canonical_url,
            "acquisition": acquisition,
            "acquisition_hash": fingerprint(acquisition),
            "data_classification": "personal",
            "classification_labels": ["personal"],
            "privacy_assessment": "not_assessed",
            "created_at": now(),
        }
        self.store.put(connection, "external_document", document["document_id"], document)
        return document["document_id"]

    def _external_document(self, connection: Any, document_id: str, actor: Actor) -> dict[str, Any]:
        document = self.store.get(connection, "external_document", document_id, actor.owner_id)
        self._protect_read(connection, document, actor)
        source = self.store.get(connection, "source", document["origin_source_id"], actor.owner_id)
        source = self._protect_read(connection, source, actor)
        descriptor = source.get("descriptor", {})
        intake = self.store.get(connection, "intake", source["intake_id"], actor.owner_id)
        if _explicit_private(descriptor) or _explicit_private(intake):
            raise DomainError(
                "PUBLICATION_BLOCKED", "Source has an explicit private intent", status_code=422
            )
        if classification_labels(source) - {"public", "personal"}:
            raise DomainError(
                "PUBLICATION_BLOCKED", "Source context is restricted", status_code=422
            )
        data = (self.object_path / source["object_path"]).read_bytes()
        if (
            hashlib.sha256(data).hexdigest() != document["origin_source_hash"]
            or source["content_hash"] != document["origin_source_hash"]
            or text_hash(document["text"]) != document["content_hash"]
            or fingerprint(document["acquisition"]) != document["acquisition_hash"]
        ):
            raise DomainError(
                "OBJECT_CORRUPT", "External document provenance changed", status_code=409
            )
        return {
            key: document[key]
            for key in (
                "document_id",
                "text",
                "content_hash",
                "acquisition_hash",
                "canonical_url",
                "data_classification",
                "classification_labels",
                "privacy_assessment",
            )
        } | {
            "evidence_ref": {
                "external_document_id": document_id,
                "content_hash": document["content_hash"],
            }
        }

    def read_external_document(self, source_id: str, *, actor: Actor) -> dict[str, Any] | None:
        """Return independently acquired text, never the user's transport envelope."""
        authenticated(actor, "knowledge:read")
        with self.store.reading() as connection:
            source = self.store.get(connection, "source", source_id, actor.owner_id)
            self._protect_read(connection, source, actor)
            document_id = source.get("external_document_id")
            return self._external_document(connection, document_id, actor) if document_id else None

    def _derivative_documents(
        self, connection: Any, change: dict[str, Any], actor: Actor
    ) -> list[dict[str, Any]]:
        manifest = change.get("derivative_input_manifest")
        if (
            not isinstance(manifest, dict)
            or manifest.get("artifact_kind") != "original_factual_synthesis"
        ):
            raise DomainError(
                "PUBLICATION_BLOCKED", "A trusted clean input manifest is required", status_code=422
            )
        if set(manifest) != {"artifact_kind", "documents", "public_catalogue", "model_input_hash"}:
            raise invalid("Clean input manifest has unsupported fields")
        if (
            change["visibility"] != "public"
            or change["publication_intent"] != "public"
            or set(change.get("risk_flags", [])) & MAJOR_RISKS
            or change["operation"] not in {"create", "update"}
        ):
            raise DomainError(
                "PUBLICATION_BLOCKED",
                "This candidate is outside automatic summary policy",
                status_code=422,
            )
        if change["base_revision"]:
            previous = self._entry(
                connection, change["entry_id"], actor.owner_id, change["base_revision"]
            )
            if (
                previous.get("visibility") != "public"
                or previous.get("publication_eligible") is not True
                or previous.get("deleted")
                or previous.get("publication_intent") != "public"
            ):
                raise DomainError(
                    "PUBLICATION_BLOCKED",
                    "Existing private entries cannot be auto-upgraded",
                    status_code=422,
                )
            self.verify_publication_entry(previous, actor=actor)
        if not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("model_input_hash", ""))):
            raise invalid("A fingerprint of the actual clean model input is required")
        documents = []
        for ref in change["evidence_refs"]:
            if set(ref) - {
                "external_document_id",
                "content_hash",
                "locator",
                "claim_ids",
            } or not ref.get("external_document_id"):
                raise DomainError(
                    "PUBLICATION_BLOCKED",
                    "Clean summaries may not consume RAW or learning evidence",
                    status_code=422,
                )
            document = self._external_document(connection, ref["external_document_id"], actor)
            if ref.get("content_hash") != document["content_hash"]:
                raise DomainError("BASE_STALE", "Clean evidence hash changed", status_code=409)
            documents.append(document)
        expected = [
            {key: doc[key] for key in ("document_id", "content_hash", "acquisition_hash")}
            for doc in documents
        ]
        if not documents or manifest.get("documents") != expected:
            raise DomainError(
                "PUBLICATION_BLOCKED",
                "Clean input document manifest does not match",
                status_code=422,
            )
        catalogue = manifest.get("public_catalogue")
        if not isinstance(catalogue, list) or len(catalogue) > 1000:
            raise invalid("Public input catalogue must be a bounded list")
        for ref in catalogue:
            if not isinstance(ref, dict) or set(ref) != {"entry_id", "revision", "content_hash"}:
                raise invalid("Public input catalogue needs exact entry revision/hash")
            if ref["entry_id"] == change["entry_id"]:
                raise invalid(
                    "The base entry is bound separately, not through the public catalogue"
                )
            entry = self._entry(connection, ref["entry_id"], actor.owner_id)
            if (
                entry.get("visibility") != "public"
                or entry.get("publication_eligible") is not True
                or entry.get("deleted")
                or entry["revision"] != ref["revision"]
                or entry["content_hash"] != ref["content_hash"]
            ):
                raise DomainError(
                    "BASE_STALE", "Public input catalogue is stale or ineligible", status_code=409
                )
            if entry.get("publication_certificate"):
                self.verify_publication_entry(entry, actor=actor)
            elif classification_labels(entry) - {"public"}:
                raise DomainError(
                    "PUBLICATION_BLOCKED",
                    "Public input catalogue includes private context",
                    status_code=422,
                )
            else:
                self._verify_rights_artifact(connection, entry, actor)
        return documents

    def publication_review_context(self, change_id: str, *, actor: Actor) -> dict[str, Any] | None:
        """Recompute complete final-artifact bindings from trusted source records."""
        authenticated(actor, "knowledge:read")
        with self.store.reading() as connection:
            change = self.store.get(connection, "change", change_id, actor.owner_id)
            self._protect_read(connection, change, actor)
            if not change.get("derivative_input_manifest"):
                return None
            documents = self._derivative_documents(connection, change, actor)
            artifact = _artifact(change, documents)
            _scan_artifact(artifact, documents)
            source_manifest = change["derivative_input_manifest"]["documents"]
            return {
                "policy_id": POLICY_ID,
                "policy_version": self.policy_version,
                "candidate_hash": fingerprint(artifact),
                "source_manifest_hash": fingerprint(source_manifest),
                "input_manifest_hash": fingerprint(change["derivative_input_manifest"]),
                "artifact": artifact,
                "source_manifest": source_manifest,
            }

    def _issue_publication_certificate(
        self,
        connection: Any,
        change: dict[str, Any],
        review: dict[str, Any],
        proof: dict[str, Any],
        actor: Actor,
    ) -> dict[str, Any]:
        if (
            actor.kind != "agent"
            or review["decision"] != "approve"
            or actor.actor_id == change["proposer_id"]
        ):
            raise DomainError(
                "PUBLICATION_BLOCKED",
                "Independent specialist approval is required",
                status_code=422,
            )
        context = self.publication_review_context(change["change_id"], actor=actor)
        if context is None or set(change.get("risk_flags", [])) & MAJOR_RISKS:
            raise DomainError(
                "PUBLICATION_BLOCKED",
                "Candidate does not permit summary certification",
                status_code=422,
            )
        for key in (
            "policy_id",
            "policy_version",
            "candidate_hash",
            "source_manifest_hash",
            "input_manifest_hash",
        ):
            if proof.get(key) != context[key]:
                raise DomainError(
                    "REVIEW_REQUIRED", "Publication proof binding is stale", status_code=409
                )
        checks = proof.get("checks")
        if (
            not isinstance(checks, dict)
            or set(checks) != PUBLICATION_CHECKS
            or any(
                not isinstance(item, dict)
                or item.get("passed") is not True
                or not isinstance(item.get("reason"), str)
                or not item["reason"].strip()
                for item in checks.values()
            )
        ):
            raise DomainError(
                "PUBLICATION_BLOCKED",
                "Every independent publication check must pass",
                status_code=422,
            )
        task_id = proof.get("review_task_id")
        task = self.store.get(connection, "task", task_id, actor.owner_id)
        if (
            task["kind"] not in {"knowledge.generate", "knowledge.review"}
            or task["state"] != "running"
            or change.get("task_id") != task_id
            or not task.get("lease_until")
            or task["lease_until"] <= now()
        ):
            raise DomainError(
                "PUBLICATION_BLOCKED",
                "Publication review needs the actual leased workflow task",
                status_code=422,
            )
        certificate = {
            **context,
            "certificate_id": new_id("pubcert"),
            "owner_id": actor.owner_id,
            "revision": 1,
            "change_id": change["change_id"],
            "patch_hash": change["patch_hash"],
            "review_id": review["review_id"],
            "reviewer_actor_id": actor.actor_id,
            "review_task_id": task_id,
            "checks": checks,
            "created_at": now(),
        }
        self.store.put(
            connection, "publication_certificate", certificate["certificate_id"], certificate
        )
        return certificate

    def _publication_for_change(
        self, connection: Any, change: dict[str, Any], actor: Actor
    ) -> dict[str, Any] | None:
        certificate_id = change.get("publication_certificate_id")
        if not certificate_id:
            return None
        certificate = self.store.get(
            connection, "publication_certificate", certificate_id, actor.owner_id
        )
        patch_hash = fingerprint(
            {
                key: change[key]
                for key in (
                    "entry_id",
                    "base_revision",
                    "operation",
                    "title",
                    "body",
                    "entry_type",
                    "visibility",
                    "evidence_refs",
                    "metadata",
                    "publication_intent",
                    "publication_eligible",
                )
            }
        )
        context = self.publication_review_context(change["change_id"], actor=actor)
        review = self.store.get(connection, "review", certificate["review_id"], actor.owner_id)
        if (
            context is None
            or patch_hash != change["patch_hash"]
            or change["policy_version"] != self.policy_version
            or certificate["patch_hash"] != change["patch_hash"]
            or certificate["change_id"] != change["change_id"]
            or review.get("publication_certificate_id") != certificate_id
            or review["decision"] != "approve"
            or review["actor_kind"] != "agent"
            or review["actor_id"] != certificate["reviewer_actor_id"]
            or review["actor_id"] == change["proposer_id"]
            or review["change_id"] != change["change_id"]
            or any(
                review[key] != change[key]
                for key in ("patch_hash", "base_revision", "evidence_hash", "policy_version")
            )
            or any(certificate.get(key) != value for key, value in context.items())
        ):
            raise DomainError(
                "REVIEW_REQUIRED", "Publication certificate no longer matches", status_code=409
            )
        return certificate

    def verify_publication_entry(self, entry: dict[str, Any], *, actor: Actor) -> dict[str, Any]:
        """Trusted resolver for a specified Git snapshot; returns only its public artifact."""
        authenticated(actor, "knowledge:read")
        summary = entry.get("publication_certificate")
        if (
            entry.get("owner_id", actor.owner_id) != actor.owner_id
            or entry.get("visibility") != "public"
            or entry.get("publication_intent") != "public"
            or entry.get("publication_eligible") is not True
            or entry.get("deleted")
        ):
            raise DomainError(
                "PUBLICATION_CERTIFICATE_INVALID",
                "Entry is not a certified public artifact",
                status_code=409,
            )
        with self.store.reading() as connection:
            current = self.store.get(connection, "entry", entry.get("entry_id"), actor.owner_id)
            if (
                current.get("visibility") != "public"
                or current.get("publication_intent") != "public"
                or current.get("publication_eligible") is not True
                or current.get("deleted")
            ):
                raise DomainError(
                    "PUBLICATION_CERTIFICATE_INVALID",
                    "Current publication decision revoked this entry",
                    status_code=409,
                )
            trusted = self._entry(
                connection, entry["entry_id"], actor.owner_id, entry.get("revision")
            )
            comparable = {
                key: value
                for key, value in trusted.items()
                if key not in {"commit_sha", "path", "owner_id", "schema_version"}
            }
            if any(entry.get(key) != value for key, value in comparable.items()):
                raise DomainError(
                    "PUBLICATION_CERTIFICATE_INVALID",
                    "Git artifact differs from the authoritative revision",
                    status_code=409,
                )
            if summary is None:
                return self._verify_rights_artifact(connection, trusted, actor)
            if not isinstance(summary, dict):
                raise DomainError(
                    "PUBLICATION_CERTIFICATE_INVALID",
                    "Certificate summary is invalid",
                    status_code=409,
                )
            certificate = self.store.get(
                connection, "publication_certificate", summary.get("certificate_id"), actor.owner_id
            )
            change = self.store.get(connection, "change", certificate["change_id"], actor.owner_id)
            checked = self._publication_for_change(connection, change, actor)
            expected = {
                key: certificate[key]
                for key in (
                    "certificate_id",
                    "policy_id",
                    "policy_version",
                    "candidate_hash",
                    "source_manifest_hash",
                    "input_manifest_hash",
                    "patch_hash",
                    "review_id",
                )
            }
            if (
                checked is None
                or summary != expected
                or change["state"] != "committed"
                or change["entry_id"] != entry.get("entry_id")
                or change["committed_revision"] != entry.get("revision")
                or entry.get("public_artifact") != certificate["artifact"]
                or _artifact(entry, self._derivative_documents(connection, change, actor))
                != certificate["artifact"]
            ):
                raise DomainError(
                    "PUBLICATION_CERTIFICATE_INVALID",
                    "Public artifact or revision was changed",
                    status_code=409,
                )
            return json.loads(json.dumps(certificate["artifact"]))

    def _verify_rights_artifact(
        self, connection: Any, entry: dict[str, Any], actor: Actor
    ) -> dict[str, Any]:
        """Preserve the separate, pre-existing explicitly licensed source path."""
        documents = []
        if classification_labels(entry) - {"public"} or not entry.get("evidence_refs"):
            raise DomainError(
                "PUBLICATION_CERTIFICATE_INVALID",
                "Legacy public evidence is not eligible",
                status_code=409,
            )
        for ref in entry["evidence_refs"]:
            if (
                not ref.get("source_id")
                or ref.get("learning_outcome_id")
                or ref.get("external_document_id")
            ):
                raise DomainError(
                    "PUBLICATION_CERTIFICATE_INVALID",
                    "Public source authorization is required",
                    status_code=409,
                )
            source = self._evidence(connection, ref, actor)
            descriptor = source.get("descriptor", {})
            if (
                classification_labels(source) - {"public"}
                or source.get("rights")
                not in {"public", "public_domain", "licensed", "summary_allowed"}
                or descriptor.get("publication_intent") == "private"
                or descriptor.get("metadata", {}).get("publication_intent") == "private"
            ):
                raise DomainError(
                    "PUBLICATION_CERTIFICATE_INVALID",
                    "Source rights or privacy no longer permit this artifact",
                    status_code=409,
                )
            if source.get("canonical_url"):
                documents.append({"canonical_url": _safe_url(source["canonical_url"])})
        return _artifact(entry, documents)
