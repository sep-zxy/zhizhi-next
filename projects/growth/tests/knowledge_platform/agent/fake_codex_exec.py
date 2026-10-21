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
    print("codex-cli " + ("0.1.0" if scenario == "wrong_version" else "0.163.0-alpha.10.2"))
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
    emit({"type": "item.completed", "item": {"type": "error", "message": message}})
    # A later valid answer must not bypass a rejected configuration/reroute notification.
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
if scenario == "arithmetic":
    inner = '{"answer":4}'
if scenario == "invalid_inner":
    inner = '{"text":"a","text":"b"}'
if scenario == "schema_wrong":
    inner = '{"text":5}'
if scenario == "secret":
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
if scenario == "early_exit":
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
if scenario == "nonzero_exit":
    raise SystemExit(3)
if scenario == "after_completion":
    emit({"type": "turn.started"})
