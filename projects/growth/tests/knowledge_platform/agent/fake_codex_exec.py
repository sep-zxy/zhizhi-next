"""Process protocol fixture only. It never calls Codex, the network or credentials."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

scenario, calls_path, *args = sys.argv[1:]
with Path(calls_path).open("a", encoding="utf-8") as file:
    file.write(
        json.dumps(
            {
                "args": args,
                "env_keys": sorted(os.environ),
                "codex_home": os.environ.get("CODEX_HOME"),
            }
        )
        + "\n"
    )
if args == ["--version"]:
    versions = {"wrong_version": "0.1.0", "reviewed_previous_version": "0.163.0-alpha.10.2",
                "unreviewed_next_version": "0.163.0-alpha.12"}
    print("codex-cli " + versions.get(scenario, "0.163.0-alpha.11"))
    raise SystemExit(0)
if args == ["login", "status"]:
    if scenario == "auth_missing":
        print("Not logged in", file=sys.stderr)
        raise SystemExit(1)
    print(
        "Logged in using " + ("API key: never-a-real-key" if scenario == "api_key" else "ChatGPT"),
        file=sys.stderr,
    )
    raise SystemExit(0)

payload = json.load(sys.stdin)
assert payload["untrusted_payload"] is not None
assert json.loads(Path(args[args.index("--output-schema") + 1]).read_text())["required"] == [
    "result_json"
]
if scenario == "timeout":
    Path(calls_path + ".pid").write_text(str(os.getpid()))
    time.sleep(30)
if scenario == "orphan_child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path(calls_path + ".child-pid").write_text(str(child.pid))
    raise SystemExit(0)
if scenario == "stderr_flood":
    sys.stderr.write("x" * 1500000)
    raise SystemExit(1)
if scenario == "stdout_flood":
    sys.stdout.write("x" * 1500000)
    raise SystemExit(1)
if scenario == "bad_jsonl":
    print("not json")
    raise SystemExit(0)


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


emit({"type": "thread.started", "thread_id": "fixture-thread"})
emit({"type": "turn.started"})
if scenario.startswith("notification_"):
    message = (
        "model rerouted: requested -> fallback-model (fixture); never-log-me"
        if scenario == "notification_reroute"
        else "runtime warning; private detail=never-log-me"
    )
    sandbox_notice = (
        "Credential masking is inactive because sandboxing is disabled. Commands "
        "will receive unmasked credentials. Select a sandboxed permission profile "
        "to use credential masking."
    )
    message = {
        "notification_boundary": sandbox_notice,
        "notification_stream_loss": "in-process app-server event stream lagged; dropped 1 events",
        "notification_ignored_configuration": (
            "Codex is ignoring 1 unrecognized configuration setting. "
            "Check for typos or deprecated settings.\n"
            "  system (C:\\never-log-me\\config.toml): `never-log-me` is ignored."
        ),
        "notification_legacy_windows": (
            "Ignoring deprecated managed config file at C:\\never-log-me\\managed_config.toml; "
            "CODEX_HOME/managed_config.toml is no longer supported on Windows. "
            "Use %ProgramData%\\OpenAI\\Codex\\requirements.toml for enforced settings "
            "or config.toml for defaults."
        ),
        "notification_transport_notice": (
            "Falling back from WebSockets to HTTPS transport. never-log-me"
        ),
        "notification_informational": (
            "`[features].transcript_v2` is deprecated and ignored. (Use "
            "`[tui].fullscreen_transcript` in config.toml instead.)"
        ),
    }.get("notification_informational" if scenario.startswith("notification_informational")
          else "notification_ignored_configuration" if scenario.endswith("configuration_boundary")
          else scenario, message)
    emit({"type": "item.completed", "item": {"type": "error", "message": message}})
    if scenario == "notification_ignored_configuration_boundary":
        emit({"type": "item.completed", "item": {"type": "error", "message": sandbox_notice}})
    if scenario == "notification_multiple":
        emit({"type": "item.completed", "item": {"type": "error",
              "message": "model rerouted: requested -> private-never-log-me"}})
    # A later valid answer must not bypass a rejected configuration/reroute notification.
    if scenario == "notification_timeout":
        time.sleep(30)
    if scenario == "notification_tool":
        emit({"type": "item.started", "item": {"type": "command_execution"}})
        time.sleep(30)
    if scenario in {"notification_failed", "notification_informational_failed"}:
        emit({"type": "turn.failed", "error": {"message": "synthetic failure"}})
        Path(calls_path + ".finished").write_text("natural fixture termination")
        raise SystemExit(1)
if scenario.startswith("stream_error_"):
    emit({"type": "error", "message": "401 unknown synthetic error; never-log-me"})
    if scenario == "stream_error_failed":
        emit({"type": "turn.failed", "error": {"message": "synthetic failure"}})
        raise SystemExit(1)
    if scenario == "stream_error_tool":
        emit({"type": "item.started", "item": {"type": "command_execution"}})
        time.sleep(30)
if scenario.startswith("provider_failure_"):
    messages = {
        "transport": "stream disconnected before completion; password=never-log-me",
        "schema": "Invalid schema for response_format; prompt=never-log-me",
        "model": "The requested model is not supported; token=never-log-me",
        "sandbox": "Sandbox initialization failed; path=never-log-me",
        "configuration": "invalid config value; private=never-log-me",
        "unknown": "Unrecognized upstream failure; content=never-log-me",
        "item": "stream disconnected before completion; password=never-log-me",
        "error": "stream disconnected before completion; password=never-log-me",
    }
    kind = scenario.removeprefix("provider_failure_")
    message = messages[kind]
    if kind == "item":
        emit({"type": "item.completed", "item": {"type": "error", "message": message}})
    elif kind == "error":
        emit({"type": "error", "message": message})
    else:
        emit({"type": "turn.failed", "error": {"message": message}})
    raise SystemExit(1)
if scenario in {"quota", "auth_error"}:
    emit(
        {
            "type": "turn.failed",
            "error": {
                "message": (
                    "429 usage limit; password=never-log-me"
                    if scenario == "quota"
                    else "401 unauthorized"
                )
            },
        }
    )
    raise SystemExit(1)
if scenario.startswith("tool_"):
    emit(
        {
            "type": "item.started",
            "item": {"type": scenario.removeprefix("tool_"), "command": "NEVER_EXECUTED_fixture"},
        }
    )
    time.sleep(30)
    raise SystemExit(0)
inner = '{"text":"有效结果\\u2028仍为同一JSONL"}'
if scenario == "arithmetic" or payload["output_schema"].get("required") == ["answer"]:
    inner = '{"answer":4}'
if scenario == "invalid_inner":
    inner = '{"text":"a","text":"b"}'
if scenario in {"schema_wrong", "notification_schema_wrong", "stream_error_schema_wrong"}:
    inner = '{"text":5}'
if scenario in {"secret", "notification_secret", "stream_error_secret"}:
    inner = json.dumps({"text": '"password": "fixture_secret_value"'})
outer = json.dumps({"result_json": inner})
if scenario == "deep_output":
    inner = '{"text":' + "[" * 2000 + "0" + "]" * 2000 + "}"
    outer = json.dumps({"result_json": inner})
if scenario == "invalid_envelope":
    outer = '{"text":"missing envelope"}'
if scenario == "claimed_model":
    # The model's text must never become trusted runtime metadata.
    inner = '{"text":"actual model is secret-fallback-provider"}'
    outer = json.dumps({"result_json": inner})
emit({"type": "item.completed", "item": {"type": "agent_message", "text": outer}})
if scenario == "duplicate_message":
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": outer}})
if scenario in {"early_exit", "notification_incomplete", "stream_error_incomplete"}:
    raise SystemExit(0)
emit(
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 50,
            "cached_input_tokens": 10,
            "output_tokens": 10000 if scenario == "too_many_tokens" else 15,
            "debug": "never-log-me",
        },
    }
)
if scenario.startswith("notification_") or scenario == "stream_error_recovered":
    Path(calls_path + ".finished").write_text("natural fixture termination")
if scenario in {"nonzero_exit", "notification_nonzero", "notification_informational_nonzero",
                "stream_error_nonzero"}:
    raise SystemExit(3)
if scenario in {"after_completion", "stream_error_extra_terminal"}:
    emit({"type": "turn.started"})
