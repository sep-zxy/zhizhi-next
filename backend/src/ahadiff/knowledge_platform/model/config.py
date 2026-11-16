"""Load explicit administrator policy without reading, exporting or manufacturing credentials."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .codex_runner import CodexModelRunner, CodexRunnerConfig
from .fixture import DisabledModelRunner
from .pi_runner import PiModelRunner, PiRunnerConfig, SubscriptionRoute

if TYPE_CHECKING:
    from .contracts import Classification, ModelRunner


def build_runner_from_config(config: dict[str, Any] | None) -> ModelRunner:
    """Build from an explicitly loaded protected config; disabled is the safe default.

    Credential values are not accepted in JSON. Optional environment variable names are
    resolved only from the deployment environment for Pi's two official Coding Plan keys.
    The official Pi host owns OpenAI OAuth and all refresh behavior.
    """
    if not config or config.get("enabled") is not True:
        return DisabledModelRunner()
    adapter = config.get("adapter", "pi")
    if adapter not in {"pi", "codex_cli"}:
        raise ValueError("Unknown model adapter")
    permitted = {
        "adapter",
        "enabled",
        "routes",
        "agent_dir",
        "guard_extension",
        "executable",
        "timeout_seconds",
        "max_json_repairs",
        "max_input_bytes",
        "max_output_bytes",
        "quota_backoff_seconds",
        "provider_env_names",
    }
    if adapter == "codex_cli":
        permitted = {
            "enabled",
            "adapter",
            "routes",
            "executable",
            "timeout_seconds",
            "max_input_bytes",
            "max_output_bytes",
            "quota_backoff_seconds",
            "reuse_existing_login",
            "codex_home",
            "expected_version",
        }
    if set(config) - permitted:
        raise ValueError(
            "Unknown model config field, credentials and metered fallback are forbidden"
        )
    routes: list[SubscriptionRoute] = []
    for raw in config.get("routes", []):
        if not isinstance(raw, dict) or set(raw) - {
            "provider_id",
            "model_id",
            "account_qualified",
            "allowed_classifications",
            "china_terms_verified",
        }:
            raise ValueError("Invalid subscription route")
        classifications = raw.get("allowed_classifications", ["public", "personal"])
        if not isinstance(classifications, list) or not all(
            value in {"public", "personal", "third_party_private", "employer"}
            for value in classifications
        ):
            raise ValueError("Invalid route classification")
        routes.append(
            SubscriptionRoute(
                provider_id=raw["provider_id"],
                model_id=raw["model_id"],
                account_qualified=raw.get("account_qualified") is True,
                allowed_classifications=cast("tuple[Classification, ...]", tuple(classifications)),
                china_terms_verified=raw.get("china_terms_verified") is True,
            )
        )
    if adapter == "codex_cli":
        executable = config.get("executable", ["codex"])
        if not isinstance(executable, list) or not all(isinstance(arg, str) for arg in executable):
            raise ValueError("executable must be a command argv list, not a shell string")
        options = {
            key: config[key]
            for key in (
                "timeout_seconds",
                "max_input_bytes",
                "max_output_bytes",
                "quota_backoff_seconds",
                "expected_version",
            )
            if key in config
        }
        return CodexModelRunner(
            CodexRunnerConfig(
                enabled=True,
                reuse_existing_login=config.get("reuse_existing_login") is True,
                routes=tuple(routes),
                executable=tuple(executable),
                codex_home=Path(config["codex_home"]) if config.get("codex_home") else None,
                **options,
            )
        )
    env_names = config.get("provider_env_names", [])
    if not isinstance(env_names, list) or set(env_names) - {"ZAI_API_KEY", "ZAI_CODING_CN_API_KEY"}:
        raise ValueError("Only official Coding Plan environment references are allowed")
    executable = config.get("executable", ["pi"])
    if not isinstance(executable, list) or not all(isinstance(item, str) for item in executable):
        raise ValueError("executable must be a command argv list, not a shell string")
    return PiModelRunner(
        PiRunnerConfig(
            enabled=True,
            routes=tuple(routes),
            agent_dir=Path(config["agent_dir"]) if config.get("agent_dir") else None,
            guard_extension=Path(config["guard_extension"])
            if config.get("guard_extension")
            else None,
            executable=tuple(executable),
            timeout_seconds=float(config.get("timeout_seconds", 120)),
            max_json_repairs=int(config.get("max_json_repairs", 1)),
            max_input_bytes=int(config.get("max_input_bytes", 512_000)),
            max_output_bytes=int(config.get("max_output_bytes", 1_048_576)),
            quota_backoff_seconds=int(config.get("quota_backoff_seconds", 3600)),
            provider_environment={
                name: os.environ[name] for name in env_names if name in os.environ
            },
        )
    )
