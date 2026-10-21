"""Explicit, fixed-release acceptance activation; never initialize an identity.

This module's flags are safeguards, not user authorization. Real activation is
performed only after a separate approval for the exact Windows release/scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .acceptance_model import AcceptanceScopeError, ScopeReceipts, material_manifest
from .model.contracts import canonical_hash
from .owner_identity import _acceptance_root, _safe, load_owner_identity


def _json(path: Path, maximum: int = 8_000_000) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
        value = json.loads(raw)
        if len(raw) > maximum or not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError):
        raise AcceptanceScopeError("ACCEPTANCE_PROVENANCE_INVALID") from None


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_binding(root: Path, source: str, release: str) -> dict[str, Any]:
    """Require the exact reviewed GitHub-built version; no old-root/cloud fallback."""
    if (
        not re.fullmatch(r"[0-9a-f]{40}", source)
        or not re.fullmatch(re.escape(source[:12]) + r"-[0-9]+-[0-9]+", release)
        or root.name != release
    ):
        raise AcceptanceScopeError("ACCEPTANCE_RELEASE_MISMATCH")
    manifest = _json(_safe(root, root / "release.json"))
    build = _json(_safe(root, root / "provenance/github-build.json"))
    distribution_path = _safe(root, root / "provenance/distribution.json")
    distribution = _json(distribution_path)
    if (
        manifest.get("release_id") != release
        or manifest.get("source_commit") != source
        or manifest.get("final_delivery") is not False
        or build.get("source_commit") != source
        or build.get("repository") != "sep-zxy/zhizhi-platform"
        or build.get("runner") != "github-hosted"
        or build.get("event") != "workflow_dispatch"
        or build.get("status") != "ci_candidate_built"
        or build.get("schema_version") != 1
        or build.get("windows_e2e") != "not_run"
        or build.get("final_delivery") is not False
        or manifest.get("github_run_id") != build.get("run_id")
        or manifest.get("github_run_attempt") != build.get("run_attempt")
        or release != f"{source[:12]}-{build.get('run_id')}-{build.get('run_attempt')}"
        or manifest.get("distribution_sha256") != _digest(distribution_path)
        or distribution.get("target") != "windows-x64"
    ):
        raise AcceptanceScopeError("ACCEPTANCE_PROVENANCE_MISMATCH")
    files = distribution.get("files")
    if not isinstance(files, dict) or not files:
        raise AcceptanceScopeError("ACCEPTANCE_DISTRIBUTION_INVALID")
    phases = build.get("phases")
    required_phases = {
        "backend-isolated-fixtures",
        "viewer-learning-browser-fixtures",
        "private-wiki-browser-fixtures",
        "assemble-windows-product",
    }
    if (
        not isinstance(manifest.get("files"), dict)
        or set(manifest["files"])
        != set(files) | {"config/launch.json", "provenance/distribution.json"}
        or any(manifest["files"][name] != digest for name, digest in files.items())
        or not isinstance(phases, list)
        or any(not isinstance(phase, dict) for phase in phases)
        or any(phase.get("status") != "passed" for phase in phases)
        or not required_phases <= {phase.get("phase") for phase in phases}
        or len(phases) != len({phase.get("phase") for phase in phases})
        or manifest["files"].get("provenance/distribution.json") != _digest(distribution_path)
    ):
        raise AcceptanceScopeError("ACCEPTANCE_PROVENANCE_MISMATCH")
    for name, digest in manifest["files"].items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or _digest(_safe(root, root / name)) != digest
        ):
            raise AcceptanceScopeError("ACCEPTANCE_DISTRIBUTION_CHANGED")
    if files.get("provenance/github-build.json") != _digest(root / "provenance/github-build.json"):
        raise AcceptanceScopeError("ACCEPTANCE_BUILD_RECEIPT_CHANGED")
    for prefix in ("app", "runtime"):
        for path in _safe(root, root / prefix).rglob("*"):
            _safe(root, path)
            if path.is_file() and path.relative_to(root).as_posix() not in files:
                raise AcceptanceScopeError("ACCEPTANCE_UNLISTED_CODE")
    return {
        "schema_version": 1,
        "package_root": os.path.normcase(str(root)),
        "source_commit": source,
        "release_id": release,
        "distribution_sha256": manifest["distribution_sha256"],
        "materials_sha256": canonical_hash(material_manifest()),
        "policy": material_manifest(),
    }


def _existing_cli_path(value: Path, *, directory: bool) -> str:
    if not value.is_absolute() or str(value).startswith(("\\\\", "//")):
        raise AcceptanceScopeError("ACCEPTANCE_CLI_PATH_INVALID")
    # Inspect metadata only. Never enumerate/read the existing authentication home.
    for path in (value, *value.parents):
        info = path.lstat()
        if (
            path.is_symlink()
            or getattr(path, "is_junction", lambda: False)()
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise AcceptanceScopeError("ACCEPTANCE_CLI_PATH_REDIRECTED")
    if not (value.is_dir() if directory else value.is_file()):
        raise AcceptanceScopeError("ACCEPTANCE_CLI_PATH_UNAVAILABLE")
    return str(value)


def activate(
    *,
    approved: bool,
    expected_source: str,
    expected_release: str,
    codex_exe: Path,
    codex_home: Path,
) -> str:
    if approved is not True:
        raise AcceptanceScopeError("ACCEPTANCE_EXPLICIT_APPROVAL_REQUIRED")
    root = _acceptance_root()
    binding = verified_binding(root, expected_source, expected_release)
    directory = _safe(root, root / "data/owner-acceptance")
    identity = load_owner_identity(directory / "identity.json")
    desktop = _json(_safe(root, directory / "desktop.json"), 32768)
    if desktop.get("model") != {"enabled": False} or desktop.get("providers") != []:
        raise AcceptanceScopeError("ACCEPTANCE_BASE_MODEL_MUST_BE_DISABLED")
    configuration = {
        **binding,
        "codex_exe": _existing_cli_path(codex_exe, directory=False),
        "codex_home": _existing_cli_path(codex_home, directory=True),
        "owner_port": identity.port,
    }
    receipts = ScopeReceipts(root, directory / "acceptance-receipts", binding, identity.token)
    existing = receipts.read("activation")
    if existing is not None:
        if existing != configuration:
            raise AcceptanceScopeError("ACCEPTANCE_ACTIVATION_CONFLICT")
        return "already_activated"
    # Initial activation is deliberately before any service start. No adoption
    # of an existing database/vault, even apparently empty, or migration/deletion.
    if any(_safe(root, directory / "platform").iterdir()) or any(
        _safe(root, directory / "desktop").iterdir()
    ):
        raise AcceptanceScopeError("ACCEPTANCE_NEW_EMPTY_STATE_REQUIRED")
    receipts.put("activation", configuration)
    return "activated_not_started"


def create_activated_application(
    *,
    expected_source: str,
    expected_release: str,
    learning_viewer_url: str | None = None,
    pi_scope: bool = False,
) -> Any:
    """Load an existing exact activation; never create identity or infer consent."""
    from .acceptance_model import MODEL, VERSION, WORKER, AcceptanceDomain, AcceptanceModelRunner
    from .app import create_application
    from .auth import TokenAuthenticator
    from .learning.wiki_learning import verify_saved_learning_outcome
    from .model.codex_runner import CodexModelRunner, CodexRunnerConfig
    from .model.pi_runner import SubscriptionRoute
    from .owner_browser import OwnerBrowserSessions
    from .owner_identity import ACTOR, OWNER
    from .workflow import WorkflowSettings

    root = _acceptance_root()
    binding = verified_binding(root, expected_source, expected_release)
    directory = _safe(root, root / "data/owner-acceptance")
    identity = load_owner_identity(directory / "identity.json")
    receipts = ScopeReceipts(root, directory / "acceptance-receipts", binding, identity.token)
    activation = receipts.read("activation")
    if activation is None or any(activation.get(key) != value for key, value in binding.items()):
        raise AcceptanceScopeError("ACCEPTANCE_ACTIVATION_REQUIRED")
    if activation.get("owner_port") != identity.port:
        raise AcceptanceScopeError("ACCEPTANCE_IDENTITY_BINDING_CHANGED")
    desktop = _json(_safe(root, directory / "desktop.json"), 32768)
    if desktop.get("model") != {"enabled": False} or desktop.get("providers") != []:
        raise AcceptanceScopeError("ACCEPTANCE_BASE_MODEL_MUST_BE_DISABLED")
    executable = _existing_cli_path(Path(activation["codex_exe"]), directory=False)
    home = _existing_cli_path(Path(activation["codex_home"]), directory=True)
    # The launcher passes desktop as --repo-root; the real viewer stores its
    # learning ledger in that workspace's .ahadiff state, not beside it.
    context_path = _safe(root, directory / "desktop/.ahadiff/growth.sqlite")
    domain = AcceptanceDomain(
        _safe(root, directory / "platform"),
        receipts,
        lambda facts: verify_saved_learning_outcome(
            context_path,
            facts,
            backend_url=identity.origin,
            device_id=identity.device_id,
            owner_id=OWNER,
        ),
    )
    try:
        runner = AcceptanceModelRunner(
            domain,
            CodexModelRunner(
                CodexRunnerConfig(
                    enabled=True,
                    reuse_existing_login=True,
                    executable=(executable,),
                    codex_home=Path(home),
                    expected_version=VERSION,
                    routes=(
                        SubscriptionRoute(
                            provider_id="openai",
                            model_id=MODEL,
                            account_qualified=True,
                            allowed_classifications=("personal",),
                        ),
                    ),
                    timeout_seconds=120,
                )
            ),
        )
        sessions = OwnerBrowserSessions(
            identity.origin,
            TokenAuthenticator({identity.token: identity.actor()}),
            owner_id=OWNER,
            actor_id=ACTOR,
        )
        app = create_application(
            directory / "platform",
            tokens={identity.token: identity.actor()},
            domain=domain,
            model_runner=runner,
            start_worker=True,
            workflow_settings=WorkflowSettings(
                owner_id=OWNER,
                worker_id=WORKER,
                allowed_provider_ids=("openai",),
                allowed_classifications=frozenset({"personal"}),
                auto_commit_enabled=True,
            ),
            owner_browser_sessions=sessions,
            private_web_root=_safe(root, root / "app/wiki-private"),
            learning_viewer_url=learning_viewer_url,
        )
        app.state.acceptance_port = identity.port
        if pi_scope:
            from .pi_acceptance import install_boundary

            install_boundary(app, root)
        return app
    except BaseException:
        domain.close()
        raise


def create_pi_bridge(root: Path) -> Any:
    from .pi_acceptance import create_bridge

    return create_bridge(root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-owner-acceptance-model", action="store_true")
    parser.add_argument("--serve-activated-scope", action="store_true")
    parser.add_argument("--serve-activated-pi-scope", action="store_true")
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--codex-exe", type=Path)
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--learning-viewer-url")
    args = parser.parse_args()
    try:
        if args.serve_activated_scope or args.serve_activated_pi_scope:
            if args.serve_activated_scope and args.serve_activated_pi_scope:
                raise AcceptanceScopeError("ACCEPTANCE_SCOPE_AMBIGUOUS")
            if args.approve_owner_acceptance_model or args.codex_exe or args.codex_home:
                raise AcceptanceScopeError("ACCEPTANCE_SERVE_CANNOT_ACTIVATE")
            import uvicorn

            app = create_activated_application(
                expected_source=args.expected_source,
                expected_release=args.expected_release,
                learning_viewer_url=args.learning_viewer_url,
                pi_scope=args.serve_activated_pi_scope,
            )
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=app.state.acceptance_port,
                access_log=False,
                log_level="warning",
            )
            return
        if (
            args.codex_exe is None
            or args.codex_home is None
            or args.learning_viewer_url is not None
        ):
            raise AcceptanceScopeError("ACCEPTANCE_ACTIVATION_INPUT_INVALID")
        status = activate(
            approved=args.approve_owner_acceptance_model,
            expected_source=args.expected_source,
            expected_release=args.expected_release,
            codex_exe=args.codex_exe,
            codex_home=args.codex_home,
        )
        print(json.dumps({"status": status, "model_called": False, "live_e2e": "not_run"}))
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_code": "ACCEPTANCE_OPERATION_FAILED",
                    "live_e2e": "not_run",
                }
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
