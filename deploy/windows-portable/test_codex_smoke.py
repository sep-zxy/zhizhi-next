"""Offline unit/fixture validation of the separate run-only acceptance entry."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "codex_smoke", Path(__file__).parent / "codex-smoke/smoke.py"
)
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


def test_fixed_request_has_no_external_data_or_fallback():
    config = SMOKE.fixed_config(Path("/existing/codex.exe"), Path("/existing/home"))
    assert config["routes"] == [{
        "provider_id": "openai", "model_id": "gpt-6.1-sol", "account_qualified": True,
        "allowed_classifications": ["public"],
    }]
    assert config["reuse_existing_login"] is True and config["timeout_seconds"] == 120
    request = SMOKE.fixed_task_fields()
    assert request["payload"] == {"question": "What is 2 + 2?"}
    assert request["input_classification"] == "public"
    assert request["output_schema"]["properties"]["answer"]["enum"] == [4]


def result(**overrides):
    return SimpleNamespace(**{
        **dict(
            status="succeeded", output={"answer": 4}, fixture=False, provider_id="openai",
            requested_model_id=SMOKE.MODEL, model_id=None,
            runtime_version="codex-cli " + SMOKE.CLI_VERSION,
            error_code=None, usage={"input": 8, "output": 2, "private": "not retained"},
        ),
        **overrides,
    })


def test_report_is_bounded_and_does_not_attest_actual_model():
    report = SMOKE.summarize(result())
    assert report["status"] == "adapter_smoke_passed"
    assert report["actual_model_id"] is None
    assert report["usage"] == {"input": 8, "output": 2}
    assert "output" not in report and "private" not in json.dumps(report)
    assert report["platform_e2e"] == "not_run"


@pytest.mark.parametrize("overrides", [
    {"fixture": True}, {"status": "waiting_auth"}, {"status": "waiting_quota"},
    {"output": {"answer": 5}}, {"model_id": "model-self-report"},
    {"requested_model_id": "other"}, {"provider_id": "other"},
])
def test_waiting_fixture_wrong_route_or_output_cannot_pass(overrides):
    assert SMOKE.summarize(result(**overrides))["status"] == "adapter_smoke_not_passed"


def test_real_config_factory_and_task_boundary_are_used(monkeypatch):
    import ahadiff.knowledge_platform.model.config as config_module

    seen = {}

    class Runner:
        async def run(self, task):
            seen["task"] = task
            return result(fixture=True)

    def factory(config):
        seen["config"] = config
        return Runner()

    monkeypatch.setattr(config_module, "build_runner_from_config", factory)
    report = SMOKE.execute_adapter(Path("/existing/codex.exe"), Path("/existing/home"))
    assert seen["config"]["adapter"] == "codex_cli"
    assert seen["task"].schema_name == "codex-cli-arithmetic-smoke-v1"
    assert report["status"] == "adapter_smoke_not_passed"


def test_cloud_entry_blocks_before_any_auth_or_files(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "smoke.py", "--codex-exe", "/existing/codex.exe",
        "--codex-home", "/existing/home", "--approve-existing-chatgpt-login",
    ])
    monkeypatch.setattr(sys, "platform", "linux")
    assert SMOKE.main() == 2


def test_report_scope_rejects_escape_and_links(tmp_path):
    with pytest.raises(SMOKE.SmokeBlocked):
        SMOKE.inside(tmp_path, "../outside")
    with pytest.raises(SMOKE.SmokeBlocked):
        SMOKE.inside(tmp_path, "C:/outside")
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SMOKE.SmokeBlocked):
        SMOKE.inside(tmp_path, "link/report.json")


def test_integrity_preflight_detects_tampering_and_extra_code(tmp_path):
    files = {}
    for prefix in ("app/backend", "app/pi", "app/wiki"):
        base = tmp_path / prefix
        base.mkdir(parents=True)
        (base / "code").write_text("fixed")
        component = {"files": {"code": SMOKE.sha(base / "code")}}
        (base / "component.json").write_text(json.dumps(component))
        for name in ("code", "component.json"):
            files[prefix + "/" + name] = SMOKE.sha(base / name)
    (tmp_path / "runtime").mkdir()
    (tmp_path / "provenance").mkdir()
    manifest = tmp_path / "provenance/distribution.json"
    manifest.write_text(json.dumps({"files": files}))
    digest = SMOKE.sha(manifest)
    assert SMOKE.verify_package(tmp_path, digest) == 6
    (tmp_path / "runtime/unlisted.py").write_text("bad")
    with pytest.raises(SMOKE.SmokeBlocked, match="UNLISTED_IMMUTABLE"):
        SMOKE.verify_package(tmp_path, digest)
    (tmp_path / "runtime/unlisted.py").unlink()
    (tmp_path / "app/backend/code").write_text("changed")
    with pytest.raises(SMOKE.SmokeBlocked, match="IMMUTABLE_FILE_MISMATCH"):
        SMOKE.verify_package(tmp_path, digest)
