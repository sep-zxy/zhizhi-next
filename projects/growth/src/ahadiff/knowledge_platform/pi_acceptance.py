"""Separate, explicit Pi read/search acceptance slot; never extend the v1 grant."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

from .acceptance_model import (
    MODEL,
    VERSION,
    AcceptanceDomain,
    AcceptanceScopeError,
    ScopeReceipts,
    _entry_digest,
    canonical_bytes,
)
from .auth import Actor, classification_labels, enforce_read_classification
from .errors import DomainError
from .model.codex_runner import CodexModelRunner, CodexRunnerConfig
from .model.contracts import ModelResult, ModelTask, canonical_hash
from .model.pi_bridge import BridgeLimits, PiBridge, PiBridgeConfig
from .model.pi_runner import SubscriptionRoute
from .model.validation import contains_obvious_secret
from .owner_acceptance import _existing_cli_path, _json, verified_binding
from .owner_identity import (
    OWNER,
    ProtectedOwnerIdentity,
    _acceptance_root,
    _safe,
    load_owner_identity,
)

PI_PROMPT = (
    "合成 Pi 验收 v1：请检索“正式正文与原始来源证据”，只查询当前授权知识目录，"
    "报告实际匹配条目；不要收藏、批准、修改或发布。"
)
QUERY = "正式正文与原始来源证据"
ARGUMENTS = {"query": QUERY, "limit": 5}
AGENT = "portable-pi-acceptance-agent"


def pi_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "purpose": "pi_read_search_acceptance_v1",
        "synthetic": True,
        "prompt": PI_PROMPT,
        "prompt_sha256": hashlib.sha256(PI_PROMPT.encode()).hexdigest(),
        "provider": "openai",
        "adapter": "codex_cli",
        "model": MODEL,
        "version": VERSION,
        "tools": {"zhizhi_search": ARGUMENTS},
        "model_attempts": 2,
        "tool_attempts": 1,
        "automatic_retry": False,
        "user_commands": False,
        "public_publish": False,
    }


@dataclass
class _Context:
    root: Path
    identity: ProtectedOwnerIdentity
    base_binding: dict[str, Any]
    base_receipts: ScopeReceipts
    base_activation: dict[str, Any]


def _load_context(root: Path) -> _Context:
    if root != _acceptance_root():
        raise AcceptanceScopeError("PI_ROOT_MISMATCH")
    release = _json(_safe(root, root / "release.json"))
    binding = verified_binding(root, release["source_commit"], release["release_id"])
    directory = _safe(root, root / "data/owner-acceptance")
    identity = load_owner_identity(directory / "identity.json")
    receipts = ScopeReceipts(root, directory / "acceptance-receipts", binding, identity.token)
    activation = receipts.read("activation")
    if (
        activation is None
        or any(activation.get(k) != v for k, v in binding.items())
        or activation.get("owner_port") != identity.port
    ):
        raise AcceptanceScopeError("PI_BASE_ACTIVATION_REQUIRED")
    desktop = _json(_safe(root, directory / "desktop.json"))
    if desktop.get("model") != {"enabled": False} or desktop.get("providers") != []:
        raise AcceptanceScopeError("PI_BASE_CONFIGURATION_CHANGED")
    return _Context(root, identity, binding, receipts, activation)


def _source(context: _Context) -> dict[str, Any]:
    state = _safe(context.root, context.root / "data/owner-acceptance/platform")
    if not (state / "platform.sqlite3").is_file() or not (state / "vault/HEAD").is_file():
        raise AcceptanceScopeError("PI_REAL_WRITER_SOURCE_REQUIRED")
    for name in ("platform.sqlite3", "platform.sqlite3-wal", "platform.sqlite3-shm"):
        _safe(context.root, state / name)
    for path in (state / "vault").rglob("*"):
        _safe(context.root, path)
    domain = cast(
        "AcceptanceDomain",
        AcceptanceDomain.open_readonly(
            state / "platform.sqlite3", state / "vault", object_path=state / "objects"
        ),
    )
    domain.scope_receipts = context.base_receipts
    domain.outcome_verifier = lambda _: False
    try:
        matches = []
        for entry in domain.list_entries(actor=context.identity.actor()):
            proof = context.base_receipts.read(f"entry:{entry['entry_id']}:{entry['revision']}")
            change = context.base_receipts.read("change:" + proof["change_id"]) if proof else None
            if change and change["stage"] == "generate-source":
                matches.append(entry)
        if len(matches) != 1 or not matches[0].get("commit_sha"):
            raise AcceptanceScopeError("PI_REAL_WRITER_SOURCE_REQUIRED")
        return matches[0]
    finally:
        domain.close()


def _source_binding(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": entry["entry_id"],
        "revision": entry["revision"],
        "content_hash": entry["content_hash"],
        "title": entry["title"],
        "semantic_digest": _entry_digest(entry),
        "commit_sha": entry["commit_sha"],
        "classification_labels": sorted(classification_labels(entry)),
    }


class PiScope:
    """Persistent two-model/one-tool allowance with an independent key purpose."""

    def __init__(self, context: _Context):
        self.context = context
        entry = _source(context)
        enforce_read_classification(self.agent(), entry)
        self.binding = {
            "base_binding_hash": canonical_hash(context.base_binding),
            "owner_id": OWNER,
            "device_id": context.identity.device_id,
            "origin": context.identity.origin,
            "source": _source_binding(entry),
            "manifest": pi_manifest(),
        }
        # Distinct domains for ledger authentication and the minimum agent bearer.
        ledger_secret = hmac.digest(
            context.identity.token.encode(), b"zhizhi/pi-acceptance-ledger/v1", "sha256"
        ).hex()
        self.receipts = ScopeReceipts(
            context.root,
            context.root / "data/owner-acceptance/pi-receipts",
            self.binding,
            ledger_secret,
        )
        self.pending: dict[str, Any] | None = None

    def require_active(self) -> None:
        if self.receipts.read("activation") != self.binding:
            raise AcceptanceScopeError("PI_EXPLICIT_ACTIVATION_REQUIRED")

    def read_source(self) -> dict[str, Any]:
        self.require_active()
        source = _source(self.context)
        enforce_read_classification(self.agent(), source)
        if _source_binding(source) != self.binding["source"]:
            raise AcceptanceScopeError("PI_SOURCE_CHANGED")
        return source

    def agent_token(self) -> str:
        self.require_active()
        return (
            "zhizhi-pi-v1."
            + hmac.digest(
                self.context.identity.token.encode(),
                b"zhizhi/pi-acceptance-agent/v1\0" + canonical_bytes(self.binding),
                "sha256",
            ).hex()
        )

    def agent(self) -> Actor:
        return Actor(
            OWNER,
            AGENT,
            "agent",
            frozenset({"knowledge:read"}),
            allowed_classifications=frozenset({"public", "personal"}),
        )

    def authorize(self, request: dict[str, Any]) -> bool:
        self.read_source()
        messages = request.get("messages")
        first = {"role": "user", "text": PI_PROMPT}
        turn = request.get("turn")
        run = self.receipts.read("run")
        if turn == 1:
            allowed = messages == [first] and (run is None or run["id"] == request["run_id"])
        elif turn == 2 and run and run["id"] == request["run_id"]:
            result = self.receipts.read("search-result")
            allowed = bool(
                result
                and isinstance(messages, list)
                and len(messages) == 3
                and messages[0] == first
                and messages[1].get("role") == "assistant"
                and messages[1].get("tool_call", {}).get("name") == "zhizhi_search"
                and messages[1].get("tool_call", {}).get("arguments") == ARGUMENTS
                and messages[2].get("role") == "tool"
                and messages[2].get("name") == "zhizhi_search"
                and messages[2].get("result") == result["envelope"]
            )
        else:
            allowed = False
        self.pending = json.loads(json.dumps(request)) if allowed else None
        return allowed

    def reserve_model(self, task: ModelTask) -> bool:
        self.read_source()
        request = self.pending
        self.pending = None
        if (
            request is None
            or task.purpose != "answer"
            or task.schema_name != "pi_domain_turn_v1"
            or task.allowed_provider_ids != ("openai",)
            or task.input_classification != "personal"
            or task.payload.get("transcript") != request["messages"]
        ):
            return False
        self.receipts.put("run", {"id": request["run_id"]})
        turn = request["turn"]
        return self.receipts.put(
            f"model-attempt:{turn}",
            {
                "fingerprint": task.fingerprint,
                "task_id": task.task_id,
                "request_hash": canonical_hash(request),
                "turn": turn,
            },
        )

    def reserve_search(self, trace_id: str) -> bool:
        self.read_source()
        result = self.receipts.read("model-result:1")
        expected = {"type": "tool_call", "name": "zhizhi_search", "arguments": ARGUMENTS}
        if result is None or result["result"]["output"] != expected:
            return False
        return self.receipts.put(
            "search-attempt",
            {"trace_id": trace_id, "source": self.binding["source"], "arguments": ARGUMENTS},
        )

    def record_result(self, envelope: dict[str, Any]) -> None:
        self.read_source()
        attempt = self.receipts.read("search-attempt")
        rows = envelope.get("data", {}).get("results")
        source = self.binding["source"]
        if (
            attempt is None
            or attempt["trace_id"] != envelope.get("trace_id")
            or not isinstance(rows, list)
            or len(rows) > 1
            or any(
                not isinstance(row, dict)
                or any(row.get(k) != source[k] for k in ("entry_id", "revision", "content_hash"))
                for row in rows
            )
        ):
            raise AcceptanceScopeError("PI_SEARCH_RECEIPT_INVALID")
        self.receipts.put("search-result", {"envelope": envelope, "matched": bool(rows)})


class _PiTransport:
    def __init__(self, scope: PiScope, transport: CodexModelRunner):
        self.scope, self.transport = scope, transport

    async def run(self, task: ModelTask) -> ModelResult:
        def blocked() -> ModelResult:
            return ModelResult(
                task_id=task.task_id,
                status="blocked",
                error_code="PI_ACCEPTANCE_SCOPE_BLOCKED",
                input_fingerprint=task.fingerprint,
            )

        stage = 1 if len(task.payload.get("transcript", [])) == 1 else 2
        attempt = self.scope.receipts.read(f"model-attempt:{stage}")
        if not attempt or attempt["fingerprint"] != task.fingerprint:
            return blocked()
        try:
            self.scope.read_source()
        except (AcceptanceScopeError, DomainError):
            return blocked()
        result = await self.transport.run(task)
        if result.status != "succeeded":
            return result
        try:
            self.scope.read_source()
        except (AcceptanceScopeError, DomainError):
            return blocked()
        if (
            result.fixture
            or result.runtime_version != "codex-cli " + VERSION
            or result.provider_id != "openai"
            or result.requested_model_id != MODEL
            or result.model_id not in {None, MODEL}
            or result.input_fingerprint != task.fingerprint
            or result.task_id != task.task_id
            or result.result_hash != canonical_hash(result.output)
            or not Draft202012Validator(task.output_schema).is_valid(result.output)
            or contains_obvious_secret(result.output)
        ):
            return blocked()
        if stage == 1:
            if result.output != {
                "type": "tool_call",
                "name": "zhizhi_search",
                "arguments": ARGUMENTS,
            }:
                return blocked()  # Never synthesize a missing tool call.
        elif (
            not isinstance(result.output, dict)
            or set(result.output) != {"type", "text"}
            or result.output["type"] != "text"
        ):
            return blocked()
        self.scope.receipts.put(f"model-result:{stage}", {"result": asdict(result)})
        return result


def activate(*, approved: bool, expected_source: str, expected_release: str) -> str:
    if approved is not True:
        raise AcceptanceScopeError("PI_EXPLICIT_APPROVAL_REQUIRED")
    context = _load_context(_acceptance_root())
    if (context.base_binding["source_commit"], context.base_binding["release_id"]) != (
        expected_source,
        expected_release,
    ):
        raise AcceptanceScopeError("PI_RELEASE_MISMATCH")
    scope = PiScope(context)
    created = scope.receipts.put("activation", scope.binding)
    return "pi_activated_not_started" if created else "pi_already_activated"


def active_scope(root: Path) -> PiScope:
    scope = PiScope(_load_context(root))
    scope.require_active()
    return scope


def create_bridge(root: Path) -> PiBridge:
    scope = active_scope(root)
    config = scope.context.base_activation
    runner = CodexModelRunner(
        CodexRunnerConfig(
            enabled=True,
            reuse_existing_login=True,
            executable=(_existing_cli_path(Path(config["codex_exe"]), directory=False),),
            codex_home=Path(_existing_cli_path(Path(config["codex_home"]), directory=True)),
            expected_version=VERSION,
            timeout_seconds=120,
            routes=(
                SubscriptionRoute(
                    provider_id="openai",
                    model_id=MODEL,
                    account_qualified=True,
                    allowed_classifications=("personal",),
                ),
            ),
        )
    )
    return PiBridge(
        _PiTransport(scope, runner),
        PiBridgeConfig(
            owner_id=OWNER,
            device_id=scope.context.identity.device_id,
            deployment_id="pi-" + canonical_hash(scope.binding),
            allowed_tools=("zhizhi_search",),
            enabled=True,
            allow_fixture=False,
            limits=BridgeLimits(
                model_calls_per_run=2,
                tool_calls_per_run=1,
                model_calls_per_process=2,
                timeout_seconds=120,
            ),
        ),
        authorize=scope.authorize,
        reserve_attempt=scope.reserve_model,
    )


def install_boundary(app: Any, root: Path) -> None:
    from .model.pi_search_boundary import PiSearchBoundary

    scope = active_scope(root)
    app.add_middleware(
        PiSearchBoundary,
        agent_token=scope.agent_token(),
        origin=scope.context.identity.origin,
        actor=scope.agent(),
        read_source=scope.read_source,
        reserve_search=scope.reserve_search,
        record_result=scope.record_result,
    )


def child_environment(root: Path) -> dict[str, str]:
    scope = active_scope(root)
    directory = _safe(root, root / "data/owner-acceptance/pi")
    sessions = _safe(root, directory / "sessions")
    sessions.mkdir(parents=True, exist_ok=True)
    return {
        "ZHIZHI_MODEL_PROVIDER": "zhizhi-codex-cli",
        "ZHIZHI_MODEL_ID": MODEL,
        "ZHIZHI_AGENT_TOKEN": scope.agent_token(),
        "ZHIZHI_API_URL": scope.context.identity.origin,
        "ZHIZHI_ALLOWED_CLASSIFICATIONS": "public,personal",
        "ZHIZHI_PI_PYTHON": str(_safe(root, root / "runtime/python/python.exe")),
        "PI_CODING_AGENT_DIR": str(directory),
        "PI_CODING_AGENT_SESSION_DIR": str(sessions),
    }


def completion_summary(
    root: Path, *, answer_sha256: str | None = None, search_result_hash: str | None = None
) -> dict[str, Any]:
    """Read signed evidence only; never infer success from process exit or print raw output."""
    scope = active_scope(root)
    source = scope.read_source()
    if not scope.receipts.read("run"):
        raise AcceptanceScopeError("PI_COMPLETION_INCOMPLETE")
    for stage in (1, 2):
        attempt = scope.receipts.read(f"model-attempt:{stage}")
        saved = scope.receipts.read(f"model-result:{stage}")
        result = saved.get("result", {}) if saved else {}
        if (
            not attempt
            or result.get("status") != "succeeded"
            or result.get("fixture") is not False
            or result.get("provider_id") != "openai"
            or result.get("requested_model_id") != MODEL
            or result.get("model_id") not in {None, MODEL}
            or result.get("runtime_version") != "codex-cli " + VERSION
            or result.get("input_fingerprint") != attempt["fingerprint"]
            or result.get("task_id") != attempt["task_id"]
            or result.get("result_hash") != canonical_hash(result.get("output"))
        ):
            raise AcceptanceScopeError("PI_COMPLETION_INCOMPLETE")
        output = result.get("output")
        if stage == 1 and output != {
            "type": "tool_call",
            "name": "zhizhi_search",
            "arguments": ARGUMENTS,
        }:
            raise AcceptanceScopeError("PI_COMPLETION_INCOMPLETE")
        if stage == 2 and (
            not isinstance(output, dict)
            or set(output) != {"type", "text"}
            or output["type"] != "text"
            or not isinstance(output["text"], str)
            or not output["text"].strip()
        ):
            raise AcceptanceScopeError("PI_COMPLETION_INCOMPLETE")
        if (
            stage == 2
            and answer_sha256 is not None
            and (hashlib.sha256(output["text"].encode("utf-8")).hexdigest() != answer_sha256)
        ):
            raise AcceptanceScopeError("PI_COMPLETION_ANSWER_MISMATCH")
    search = scope.receipts.read("search-result")
    attempt = scope.receipts.read("search-attempt")
    if (
        not search
        or search.get("matched") is not True
        or not attempt
        or attempt.get("source") != scope.binding["source"]
        or attempt.get("arguments") != ARGUMENTS
        or search.get("envelope", {}).get("trace_id") != attempt.get("trace_id")
    ):
        raise AcceptanceScopeError("PI_COMPLETION_NO_MATCH")
    if search_result_hash is not None and canonical_hash(search["envelope"]) != search_result_hash:
        raise AcceptanceScopeError("PI_COMPLETION_SEARCH_MISMATCH")
    return {
        "status": "pi_source_search_verified",
        "live_e2e": "pi_source_search_only",
        "source_entry_id": source["entry_id"],
        "source_revision": source["revision"],
        "source_content_hash": source["content_hash"],
        "model_calls": 2,
        "search_calls": 1,
        "final_delivery": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-owner-pi-search", action="store_true")
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-release", required=True)
    args = parser.parse_args()
    try:
        status = activate(
            approved=args.approve_owner_pi_search,
            expected_source=args.expected_source,
            expected_release=args.expected_release,
        )
        print(json.dumps({"status": status, "model_called": False, "live_e2e": "not_run"}))
    except Exception:
        print(json.dumps({"status": "blocked", "error_code": "PI_ACTIVATION_FAILED"}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
