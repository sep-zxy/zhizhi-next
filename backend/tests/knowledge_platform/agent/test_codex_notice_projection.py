"""Synthetic notices/fixture processes only; zero official CLI or model calls."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ahadiff.knowledge_platform.model import (
    CodexRunnerConfig,
    SubscriptionRoute,
    codex_runner,
    diagnostic_probe,
)
from ahadiff.knowledge_platform.model._codex_notice_projection import (
    EVIDENCE_IDS,
    FAMILIES,
    PROJECTION_TASK_FINGERPRINT,
    REASON_IDS,
    TEMPLATE_IDS,
    project_notice,
    projection_result,
)
from ahadiff.knowledge_platform.model._codex_notice_templates import EXACT_NOTIFICATION_TEMPLATES
from ahadiff.knowledge_platform.model.codex_runner import CodexModelRunner, _ExecEvents
from ahadiff.knowledge_platform.model.diagnostic_probe import fixed_public_smoke_task, probe_codex


def settings(tmp_path, scenario="notification_ignored_configuration"):
    return CodexRunnerConfig(
        enabled=True,
        reuse_existing_login=True,
        development_transport=True,
        executable=(
            sys.executable,
            str(Path(__file__).with_name("fake_codex_exec.py")),
            scenario,
            str(tmp_path / "calls.jsonl"),
        ),
        routes=(
            SubscriptionRoute(
                provider_id="openai",
                model_id="gpt-6.1-sol",
                account_qualified=True,
                allowed_classifications=("public",),
            ),
        ),
    )


def ignored(source="session-flags", key="synthetic-private-marker", count=1):
    label = "setting" if count == 1 else "settings"
    rows = [
        f"  {source}: `{key}{i if count > 1 else ''}` is ignored." for i in range(min(count, 3))
    ]
    if count > 3:
        rows.append(f"  ... and {count - 3} more ignored settings.")
    return (
        f"Codex is ignoring {count} unrecognized configuration {label}. "
        "Check for typos or deprecated settings.\n" + "\n".join(rows)
    )


@pytest.mark.parametrize("count", [1, 2, 3, 4, 9999])
@pytest.mark.parametrize(
    "source",
    [
        "session-flags",
        "system (C:\\synthetic-private-marker)",
        "enterprise-managed (synthetic-private-marker, synthetic-id)",
        "requirements (synthetic-private-marker)",
    ],
)
def test_full_ignored_config_grammar_drops_all_dynamic_slots(count, source):
    result = project_notice(ignored(source=source, count=count))
    assert result["template_id"] == "ignored_configuration_fields"
    assert result["reason_id"] == "unrecognized_configuration"
    assert result["evidence"] == "official_template_shape_match"
    assert "synthetic-private-marker" not in json.dumps(result)
    assert result["upstream_kind"] == "not_preserved_by_exec_jsonl"


@pytest.mark.parametrize(
    ("key", "hint"),
    [
        (
            "windows.sandbox_private_desktop",
            " Remove windows.sandbox_private_desktop; legacy Windows sandboxes "
            "always use a private desktop.",
        ),
        (
            "network_proxy",
            " Use [permissions.<name>.network] for network settings, or "
            "[experimental_network] in requirements.toml for enforced network policy.",
        ),
        (
            "allowed_permissions",
            " Use [allowed_permission_profiles] and default_permissions; the old "
            "allowlist is ignored even when both forms are present.",
        ),
        (
            "features.include_view_image_tool",
            " Use [features].view_image to configure the image tool.",
        ),
        (
            "profiles.synthetic-private-marker.features.include_view_image_tool",
            " Use [features].view_image to configure the image tool.",
        ),
    ],
)
def test_official_fixed_hints_must_correspond_to_key(key, hint):
    message = ignored(key=key) + hint
    assert project_notice(message)["template_id"] == "ignored_configuration_fields"
    assert project_notice(ignored() + hint)["template_id"] is None
    assert "synthetic-private-marker" not in json.dumps(project_notice(message))


@pytest.mark.parametrize("message", list(EXACT_NOTIFICATION_TEMPLATES))
def test_projection_does_not_control_nonfatal_protocol_acceptance(message):
    result = project_notice(message)
    assert result["template_id"] == EXACT_NOTIFICATION_TEMPLATES[message]
    assert result["evidence"] == "exact_public_template_text"
    events = _ExecEvents(20, notification_details=True, notice_projection=True)
    events.accept(json.dumps({
        "type": "item.completed", "item": {"type": "error", "message": message},
    }).encode())
    assert events.text is None and not events.completed
    assert events.notification["projection"] == result
    for item in [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "isolated"}},
        {"type": "turn.completed", "usage": {"output_tokens": 1}},
    ]:
        events.accept(json.dumps(item).encode())
    events.finish(0)
    assert events.provider_turn_state == "completed"


@pytest.mark.parametrize(
    "change",
    [
        lambda m: "prefix " + m,
        lambda m: m + " suffix",
        lambda m: m.upper(),
        lambda m: m.replace("\n", "\r\n"),
        lambda m: m + "\x1b[31m",
        lambda m: m + "\0",
        lambda m: m + "\n" + m,
        lambda m: m + "x" * 8192,
        lambda m: m.replace("Codex", "Cоdex"),
        lambda m: m.replace(
            "synthetic-private-marker",
            "Falling back from WebSockets to HTTPS transport. synthetic-private-marker",
        ),
        lambda m: m.replace("1 unrecognized", "2 unrecognized"),
    ],
)
def test_extra_text_injected_templates_controls_and_inconsistent_counts_not_unique(change):
    projection = project_notice(change(ignored()))
    assert projection["template_id"] is None
    assert projection["evidence"] not in {
        "official_template_shape_match",
        "exact_public_template_text",
    }
    assert "synthetic-private-marker" not in json.dumps(projection)


@pytest.mark.parametrize(
    "secret",
    [
        "C:\\private-synthetic-account\\private.txt",
        "https://private.example.invalid/token-value",
        "synthetic-user@example.invalid",
        "token=synthetic-secret-marker",
        "Q" * 200,
        "private-账户-合成",
        "password=synthetic-secret-marker",
    ],
)
def test_no_dynamic_slot_text_hash_or_shape_leaves_projection(secret, capsys, caplog):
    result = project_notice(ignored(source=f"system ({secret})", key=secret))
    assert result["template_id"] == "ignored_configuration_fields"
    serialized = json.dumps(result)
    assert secret not in serialized
    assert set(result) == {
        "projection_version",
        "source_commit",
        "template_id",
        "reason_id",
        "family",
        "evidence",
        "upstream_kind",
    }
    assert capsys.readouterr() == ("", "") and not caplog.records


@pytest.mark.parametrize(
    ("source", "matches"),
    [
        ("MDM (synthetic-domain:synthetic-key)", True),
        ("enterprise-managed (synthetic-name, synthetic-id)", True),
        ("project (/synthetic-project/.codex/config.toml)", True),
        ("legacy managed_config.toml (MDM)", True),
        ("packaged defaults (/synthetic/config.toml)", True),
        ("user (/synthetic/config.toml)", True),
        ("MDM (synthetic-no-colon)", False),
        ("enterprise-managed (synthetic-no-separator)", False),
        ("project (/synthetic-project/wrong-file)", False),
        ("unknown-source (synthetic)", False),
    ],
)
def test_config_source_formatter_fixed_separators_are_preserved(source, matches):
    assert (project_notice(ignored(source=source))["template_id"] is not None) is matches


def legacy(path="C:\\synthetic-private-marker\\managed_config.toml"):
    return (
        "Ignoring deprecated managed config file at "
        + path
        + "; CODEX_HOME/managed_config.toml is no longer supported on Windows. "
        "Use %ProgramData%\\OpenAI\\Codex\\requirements.toml for enforced settings "
        "or config.toml for defaults."
    )


@pytest.mark.parametrize(
    "message",
    [
        legacy(),
        "in-process app-server event stream lagged; dropped 17 events",
    ],
)
def test_other_full_grammars_match_only_the_whole_original_message(message):
    result = project_notice(message)
    assert result["evidence"] == "official_template_shape_match"
    for mutation in (
        "prefix " + message,
        message + " suffix",
        message + "\n",
        message.upper(),
        message + "\r",
        message + "\x1b",
        message + "\0",
        message + message,
        message[:-1],
        message + "X" * 8192,
    ):
        assert project_notice(mutation)["template_id"] is None
    assert "synthetic-private-marker" not in json.dumps(result)


@pytest.mark.parametrize(
    "path",
    [
        "C:\\private?marker\\managed_config.toml",
        "relative\\managed_config.toml",
        "\\\\synthetic-server\\synthetic-share\\managed_config.toml",
        "C:\\synthetic\\wrong.toml",
        "C:\\Falling back from WebSockets to HTTPS transport. nested\\managed_config.toml",
    ],
)
def test_legacy_invalid_or_nested_path_never_full_matches(path):
    assert project_notice(legacy(path))["template_id"] is None


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "1000000000", "secret-marker", "01"])
def test_stream_count_outside_conservative_grammar_cannot_claim_full_match(value):
    message = f"in-process app-server event stream lagged; dropped {value} events"
    assert project_notice(message)["template_id"] is None


def test_closed_output_vocabulary_and_reviewed_source_manifest():
    record = json.loads(
        (
            Path(__file__).resolve().parents[4]
            / "deploy/windows-portable/codex-notice-projection-review.json"
        ).read_text(encoding="utf-8")
    )
    assert record["commit"] == "5425d3e625daa58eea3ad1de27c087eb3b2ffbd3"
    assert record["scope"]["task_fingerprint"] == fixed_public_smoke_task().fingerprint
    assert record["cli_version"] == "0.163.0-alpha.11.2"
    assert set(record["output"]["families"]) == FAMILIES
    assert set(record["output"]["evidence"]) == EVIDENCE_IDS
    assert {c["id"] for c in record["full_grammar_candidates"]} == (
        TEMPLATE_IDS - set(EXACT_NOTIFICATION_TEMPLATES.values())
    )
    assert {c["reason_id"] for c in record["full_grammar_candidates"]} == REASON_IDS
    sources = [record["mapping"]]
    for candidate in record["full_grammar_candidates"] + record["family_only_candidates"]:
        sources.append(candidate["source"])
        if "formatter" in candidate:
            sources.append(candidate["formatter"])
    for source in sources:
        assert len(source["sha"]) == 40
        assert source["url"] == (
            "https://github.com/openai/codex/blob/"
            + record["commit"]
            + "/"
            + source["path"]
            + "#L"
            + source["lines"].split("-")[0]
        )
    # Hard-code the output domain independently of any runtime input or source slots.
    values = {
        "projection_version": {1},
        "source_commit": {"5425d3e625daa58eea3ad1de27c087eb3b2ffbd3"},
        "template_id": {None, *TEMPLATE_IDS},
        "reason_id": {None, *REASON_IDS},
        "family": {
            "unknown",
            "ambiguous",
            "model_metadata",
            "configuration",
            "code_mode",
            "hooks",
            "context",
            "feature_deprecation",
            "transport",
            "persistence",
            "event_stream",
        },
        "evidence": {
            "none",
            "exact_public_template_text",
            "official_template_shape_match",
            "family_hint",
            "projection_failed",
        },
        "upstream_kind": {"not_preserved_by_exec_jsonl"},
    }
    messages = [
        *EXACT_NOTIFICATION_TEMPLATES,
        ignored(),
        ignored() * 2,
        legacy(),
        "in-process app-server event stream lagged; dropped 17 events",
        *(
            candidate["prefix"] + "synthetic-secret"
            for candidate in record["family_only_candidates"]
        ),
        "x" * 144,
        "",
        "private",
        "\ud800",
        b"private",
        None,
        "X" * 8193,
    ]
    outputs = [project_notice(message) for message in messages]
    outputs.append(
        projection_result(
            family="synthetic-secret",
            template_id="synthetic-secret",
            reason_id="synthetic-secret",
            evidence="synthetic-secret",
        )
    )
    for output in outputs:
        assert set(output) == set(values)
        assert all(value in values[key] for key, value in output.items())


def test_wrong_route_is_blocked_by_runner_before_any_process(tmp_path):
    config = settings(tmp_path)
    config = replace(config, routes=(replace(config.routes[0], model_id="other-model"),))
    runner = CodexModelRunner(
        config,
        collect_process_diagnostics=True,
        collect_notification_diagnostics=True,
        collect_notice_projection=True,
    )
    result = asyncio.run(runner.run(fixed_public_smoke_task()))
    assert result.error_code == "CODEX_NOTICE_PROJECTION_SCOPE_INVALID" and result.output is None
    assert runner.process_diagnostics == [] and not (tmp_path / "calls.jsonl").exists()


def test_actual_wrong_cli_version_still_stops_before_auth_or_model(tmp_path):
    report = asyncio.run(
        probe_codex(
            settings(tmp_path, "wrong_version"),
            model_smoke=True,
            notice_projection=True,
        )
    )
    assert report["model"]["error_code"] == "CODEX_VERSION_MISMATCH"
    assert report["model_attempted"] is False
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call["args"] for call in calls] == [["--version"]]


@pytest.mark.parametrize("join_cancelled", [False, True])
def test_projection_notification_primary_survives_cleanup_failure(
    tmp_path,
    monkeypatch,
    capsys,
    caplog,
    join_cancelled,
):
    import tempfile

    from ahadiff.knowledge_platform.model._codex_cleanup import cleanup_failure

    original_temp = tempfile.mkdtemp
    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda *, prefix, dir=None: original_temp(prefix=prefix, dir=tmp_path)
    )
    original_gather = asyncio.gather
    original_stop = codex_runner._stop
    stops = []

    async def failed_join():
        raise asyncio.CancelledError()

    def gather(*args, **kwargs):
        # Inject after both preflight processes stopped, so the primary
        # notification exists before the execution reader-join cancellation.
        if join_cancelled and len(stops) == 2 and kwargs.get("return_exceptions"):
            return failed_join()
        return original_gather(*args, **kwargs)

    async def stop(process, job=None):
        await original_stop(process, job)
        stops.append(process.pid)

    async def retained_workspace(_):
        return {
            "resource": "temporary_directory",
            "status": "failed",
            "attempts": 1,
            "errors": [cleanup_failure(PermissionError(13, "synthetic-secret"))],
            "recovery": "owned_workspace_retained_for_review",
        }

    monkeypatch.setattr(asyncio, "gather", gather)
    monkeypatch.setattr(codex_runner, "_stop", stop)
    monkeypatch.setattr(codex_runner, "cleanup_owned_workspace", retained_workspace)
    runner = CodexModelRunner(
        settings(tmp_path, "notification_ignored_configuration_boundary"),
        collect_process_diagnostics=True,
        collect_notification_diagnostics=True,
        collect_notice_projection=True,
    )

    async def run():
        first = await runner.run(fixed_public_smoke_task())
        before = (tmp_path / "calls.jsonl").read_bytes()
        replay = await runner.run(fixed_public_smoke_task())
        assert before == (tmp_path / "calls.jsonl").read_bytes()
        return first, replay

    first, replay = asyncio.run(run())
    assert first.error_code == "CODEX_RUNTIME_BOUNDARY_VIOLATION" and first.output is None
    assert replay.error_code == "CODEX_CLEANUP_PENDING" and replay.output is None
    assert len(stops) == 3 and not runner._cache
    assert runner.process_diagnostics[-1]["notification"]["projection"]["template_id"] == (
        "ignored_configuration_fields"
    )
    assert runner.cleanup_diagnostics[-1]["status"] == "failed"
    serialized = json.dumps([runner.process_diagnostics, runner.cleanup_diagnostics])
    assert "synthetic-secret" not in serialized and "never-log-me" not in serialized
    assert capsys.readouterr() == ("", "") and not caplog.records


def test_projection_cli_flag_is_explicit_and_no_model_without_smoke(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnostic_probe",
            "--codex-exe",
            str(tmp_path / "official.exe"),
            "--codex-home",
            str(tmp_path / "private-home"),
            "--notice-projection",
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        diagnostic_probe.main()
    report = json.loads(capsys.readouterr().out)
    assert stopped.value.code == 2
    assert report["error_code"] == "CODEX_NOTICE_PROJECTION_SCOPE_INVALID"
    assert report["processes"] == [] and report["model_attempted"] is False
    assert str(tmp_path) not in json.dumps(report)


def test_projection_cannot_replay_old_success_after_cleanup_failure(tmp_path, monkeypatch):
    import tempfile

    original_temp = tempfile.mkdtemp
    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda *, prefix, dir=None: original_temp(prefix=prefix, dir=tmp_path)
    )
    runner = CodexModelRunner(
        settings(tmp_path, "arithmetic"), collect_process_diagnostics=True,
        collect_notification_diagnostics=True, collect_notice_projection=True,
    )

    async def failed_cleanup(_):
        return {"resource": "temporary_directory", "status": "failed", "attempts": 1,
                "errors": [], "recovery": "owned_workspace_retained_for_review"}

    async def run():
        task = fixed_public_smoke_task()
        first = await runner.run(task)
        assert first.status == "succeeded" and task.dedupe_key in runner._cache
        monkeypatch.setattr(codex_runner, "cleanup_owned_workspace", failed_cleanup)
        # An independently owned host-only workspace fails cleanup after the
        # cached fixture answer. There is no second model request.
        host = await runner.inspect_existing_runtime()
        assert host["status"] == "blocked" and host["error_code"] == "CODEX_CLEANUP_FAILED"
        before = (tmp_path / "calls.jsonl").read_bytes()
        replay = await runner.run(task)
        assert replay.error_code == "CODEX_CLEANUP_PENDING" and replay.output is None
        assert before == (tmp_path / "calls.jsonl").read_bytes()
        assert task.dedupe_key in runner._cache

    asyncio.run(run())


@pytest.mark.parametrize(
    ("message", "family"),
    [
        ("Falling back from WebSockets to HTTPS transport. synthetic-secret-marker", "transport"),
        (
            "Failed to save the conversation transcript; Codex will continue retrying. "
            "Error: synthetic-secret-marker",
            "persistence",
        ),
        ("Code Mode is unavailable because synthetic-secret-marker", "code_mode"),
        (
            "prefix Falling back from WebSockets to HTTPS transport. synthetic-secret-marker",
            "ambiguous",
        ),
        ("synthetic-secret-marker", "unknown"),
        ("x" * 144, "unknown"),
        ("\ud800", "unknown"),
        ("x" * 8193, "unknown"),
    ],
)
def test_family_hint_never_claims_origin_or_reason(message, family):
    result = project_notice(message)
    assert result["family"] == family
    assert result["template_id"] is None and result["reason_id"] is None
    assert result["evidence"] in {"family_hint", "none"}
    assert "synthetic-secret-marker" not in json.dumps(result)


@pytest.mark.parametrize("message", [None, {}, 123, b"private"])
def test_non_text_stays_unknown(message):
    assert project_notice(message)["family"] == "unknown"


@pytest.mark.parametrize(
    ("scenario", "template", "family"),
    [
        ("notification_ignored_configuration", "ignored_configuration_fields", "configuration"),
        ("notification_legacy_windows", "windows_legacy_managed_config", "configuration"),
        ("notification_transport_notice", None, "transport"),
        ("notification_multiple", None, "unknown"),
    ],
)
def test_opt_in_fixed_probe_accepts_nonfatal_notice_once_cleaned_and_redacted(
    tmp_path, scenario, template, family, capsys, caplog
):
    report = asyncio.run(
        probe_codex(settings(tmp_path, scenario), model_smoke=True, notice_projection=True)
    )
    assert report["model"]["error_code"] is None
    assert report["model"]["status"] == "succeeded"
    assert report["model"]["answer_matches_fixed_schema"] is True
    assert report["model"]["actual_model_id"] is None
    process = report["processes"][-1]
    projection = process["notification"]["projection"]
    assert projection["template_id"] == template and projection["family"] == family
    expected_count = 2 if scenario == "notification_multiple" else 1
    assert process["event_counts"]["item.error"] == expected_count
    assert process["provider_turn_state"] == "completed" and process["exit_code"] == 0
    assert process["terminal_event"] == "turn.completed"
    assert process["notification_policy"] == "nonfatal"
    assert process["cleanup"]["status"] == "completed"
    assert all(item["status"] == "completed" for item in report["cleanup"])
    assert "never-log-me" not in json.dumps(report)
    assert capsys.readouterr() == ("", "") and not caplog.records
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert sum("exec" in call["args"] for call in calls) == 1
    assert "never-log-me" not in (tmp_path / "calls.jsonl").read_text()


def test_default_and_old_versions_never_enable_new_projection(tmp_path):
    assert fixed_public_smoke_task().fingerprint == PROJECTION_TASK_FINGERPRINT
    report = asyncio.run(probe_codex(settings(tmp_path), model_smoke=True))
    assert "projection" not in report["processes"][-1]["notification"]
    (tmp_path / "calls.jsonl").unlink()
    for model_smoke, cfg in [
        (False, settings(tmp_path)),
        (True, replace(settings(tmp_path), expected_version="0.163.0-alpha.10.2")),
    ]:
        report = asyncio.run(probe_codex(cfg, model_smoke=model_smoke, notice_projection=True))
        assert report["error_code"] == "CODEX_NOTICE_PROJECTION_SCOPE_INVALID"
        assert report["model_attempted"] is False and report["processes"] == []
        assert not (tmp_path / "calls.jsonl").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"payload": {"question": "different"}},
        {"task_id": "other"},
        {"trace_id": "private"},
        {"purpose": "generate"},
        {"dedupe_key": "other"},
        {"max_output_tokens": 20},
        {"input_classification": "personal"},
        {"instructions": "different"},
        {"output_schema": {"type": "string"}},
        {"schema_name": "other"},
        {"input_refs": ("public:other",)},
        {"allowed_provider_ids": ("other",)},
        {"deadline": datetime(2030, 1, 1, tzinfo=UTC)},
    ],
)
def test_arbitrary_task_or_mutated_probe_rejected_before_any_process(tmp_path, change, monkeypatch):
    changed = replace(fixed_public_smoke_task(), **change)
    monkeypatch.setattr(diagnostic_probe, "fixed_public_smoke_task", lambda: changed)
    report = asyncio.run(probe_codex(settings(tmp_path), model_smoke=True, notice_projection=True))
    assert report["model"]["error_code"] == "CODEX_NOTICE_PROJECTION_SCOPE_INVALID"
    assert report["processes"] == [] and not report["model_attempted"]
    assert not (tmp_path / "calls.jsonl").exists()


def test_projection_without_diagnostic_collectors_cannot_run(tmp_path):
    runner = CodexModelRunner(settings(tmp_path), collect_notice_projection=True)
    result = asyncio.run(runner.run(fixed_public_smoke_task()))
    assert result.error_code == "CODEX_NOTICE_PROJECTION_SCOPE_INVALID" and result.output is None
    assert not (tmp_path / "calls.jsonl").exists()


def test_projection_exception_does_not_change_acceptance_or_leak_exception_text(
    tmp_path, monkeypatch, caplog, capsys
):
    def broken(_message):
        raise OSError(5, "synthetic-secret-marker", "private-path-marker")

    monkeypatch.setattr(codex_runner, "project_notice", broken)
    report = asyncio.run(probe_codex(settings(tmp_path), model_smoke=True, notice_projection=True))
    assert report["model"]["error_code"] is None
    assert report["processes"][-1]["notification"]["projection"]["evidence"] == "projection_failed"
    assert all(item["status"] == "completed" for item in report["cleanup"])
    for secret in ("synthetic-secret-marker", "private-path-marker", "never-log-me"):
        assert secret not in json.dumps(report)
    assert capsys.readouterr() == ("", "") and not caplog.records
