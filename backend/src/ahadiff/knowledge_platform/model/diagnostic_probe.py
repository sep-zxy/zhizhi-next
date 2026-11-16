"""Read-only Codex host diagnostics; a model request requires --model-smoke.

The report never contains arguments, paths, prompts, raw stderr/provider text,
account identifiers or credentials. The official CLI alone reads its auth home.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from ._codex_notice_projection import PROJECTION_CLI_VERSION
from .codex_runner import CodexModelRunner, CodexRunnerConfig
from .contracts import ModelTask
from .diagnostics import coded_failure_diagnostic, sanitized_failure, sanitized_warning_codes
from .pi_runner import RpcFailure, SubscriptionRoute
from .validation import sanitized_usage

SMOKE_MODEL = "gpt-6.1-sol"


def fixed_public_smoke_task() -> ModelTask:
    """Return the one approved public arithmetic case, never user-supplied input."""
    return ModelTask(
        task_id="codex-cli-public-arithmetic-smoke",
        purpose="answer",
        input_refs=("public:fixed-arithmetic-2-plus-2",),
        input_classification="public",
        allowed_provider_ids=("openai",),
        schema_name="codex-cli-arithmetic-smoke-v1",
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "integer", "enum": [4]}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        payload={"question": "What is 2 + 2?"},
        dedupe_key="codex-cli-public-arithmetic-smoke-v1",
        max_output_tokens=4096,
        instructions="Calculate 2 + 2. Return only the required JSON answer. Do not use tools.",
    )


async def probe_codex(
    config: CodexRunnerConfig, *, model_smoke: bool = False, notice_projection: bool = False
) -> dict[str, Any]:
    """Run bounded existing-login checks, optionally one explicit fixed model call.

    Host-only success is not model or platform E2E success. A fixture config is
    allowed only through this Python test interface and remains marked fixture.
    """
    started = time.monotonic()
    runner = CodexModelRunner(
        config, collect_process_diagnostics=True,
        collect_notification_diagnostics=model_smoke,
        collect_notice_projection=notice_projection,
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "fixed_public_model_smoke" if model_smoke else "host_only",
        "platform": "windows" if os.name == "nt" else "posix",
        "fixture": config.development_transport,
        "expected_cli_version": config.expected_version,
        "status": "not_started",
        "model_attempted": False,
        "model": None,
        "failure": None,
        "processes": runner.process_diagnostics,
        "cleanup": runner.cleanup_diagnostics,
        "platform_e2e": "not_run",
    }
    try:
        if notice_projection and (
            not model_smoke or config.expected_version != PROJECTION_CLI_VERSION
        ):
            report["status"] = "blocked"
            report["error_code"] = "CODEX_NOTICE_PROJECTION_SCOPE_INVALID"
            report["failure"] = sanitized_failure(coded_failure_diagnostic(
                "CODEX_NOTICE_PROJECTION_SCOPE_INVALID", stage="validation"
            ))
        elif not config.enabled or not config.reuse_existing_login:
            report["status"] = "blocked"
            report["failure"] = sanitized_failure(
                coded_failure_diagnostic(
                    "CODEX_EXISTING_LOGIN_NOT_APPROVED", stage="validation"
                )
            )
        elif model_smoke:
            # No alternate prompt/schema/provider/model is accepted by this probe.
            if len(config.routes) != 1 or config.routes[0].model_id != SMOKE_MODEL:
                raise ValueError("The diagnostic model case must use the fixed approved route")
            result = await runner.run(fixed_public_smoke_task())
            answer_valid = result.status == "succeeded" and result.output == {"answer": 4}
            report["status"] = (
                "fixture_model_smoke_passed" if answer_valid and result.fixture
                else "model_smoke_passed" if answer_valid
                else "model_smoke_not_passed"
            )
            report["failure"] = sanitized_failure(result.failure)
            report["model"] = {
                "status": result.status,
                "error_code": result.error_code,
                "answer_matches_fixed_schema": answer_valid,
                "requested_model_id": SMOKE_MODEL,
                "actual_model_id": None,
                "actual_model_evidence": "unknown_in_exec_jsonl",
                "fixture": result.fixture,
                "usage": sanitized_usage(result.usage),
                "warning_codes": sanitized_warning_codes(result.warning_codes),
            }
        else:
            report.update(await runner.inspect_existing_runtime())
    except RpcFailure as exc:
        report["status"] = exc.status
        report["error_code"] = exc.code
        report["failure"] = sanitized_failure(
            getattr(exc, "failure", None)
        ) or sanitized_failure(coded_failure_diagnostic(exc.code, stage="validation"))
    except TimeoutError:
        report["status"] = "cancelled"
        report["error_code"] = "MODEL_DEADLINE_EXCEEDED"
        report["failure"] = sanitized_failure(
            coded_failure_diagnostic("MODEL_DEADLINE_EXCEEDED", stage="validation")
        )
    except (OSError, UnicodeError, ValueError):
        report["status"] = "blocked"
        report["error_code"] = "CODEX_HOST_INVALID"
        report["failure"] = sanitized_failure(
            coded_failure_diagnostic("CODEX_HOST_INVALID", stage="validation")
        )
    report["preflight"] = dict(runner.preflight_states)
    report["model_attempted"] = any(
        process["phase"] == "execution" for process in runner.process_diagnostics
    )
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return report


def main() -> None:
    """Print only a sanitized JSON report; use the deployment's existing official CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-exe", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument(
        "--model-smoke", action="store_true",
        help="Explicitly run one fixed, public 2+2 request; omitted means no model request.",
    )
    parser.add_argument(
        "--notice-projection", action="store_true",
        help="Opt in to constant-only notification projection for the fixed --model-smoke case.",
    )
    args = parser.parse_args()
    try:
        if not args.codex_exe.is_absolute() or not args.codex_home.is_absolute():
            raise ValueError("Existing CLI and auth home must be absolute paths")
        config = CodexRunnerConfig(
            enabled=True,
            reuse_existing_login=True,
            executable=(str(args.codex_exe),),
            codex_home=args.codex_home,
            routes=(
                SubscriptionRoute(
                    provider_id="openai", model_id=SMOKE_MODEL, account_qualified=True,
                    allowed_classifications=("public",),
                ),
            ),
            timeout_seconds=120,
            max_input_bytes=16384,
        )
        report = asyncio.run(probe_codex(
            config, model_smoke=args.model_smoke, notice_projection=args.notice_projection
        ))
    except Exception:
        # Keep CLI setup failures redacted as well. Never print exception repr/tracebacks.
        report = {
            "schema_version": 1, "status": "blocked", "error_code": "PROBE_SETUP_FAILED",
            "model_attempted": False, "platform_e2e": "not_run",
        }
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    raise SystemExit(
        0 if report["status"] in {"host_preflight_passed", "model_smoke_passed"} else 2
    )


if __name__ == "__main__":
    main()
