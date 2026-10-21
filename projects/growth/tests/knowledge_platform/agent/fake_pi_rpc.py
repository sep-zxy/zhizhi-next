"""Explicit test-only protocol peer. Does not import Pi, authenticate or use a network."""

import json
import sys
import time
from pathlib import Path

scenario = sys.argv[1]
audit_file = Path(sys.argv[2])
args = sys.argv[3:]
with audit_file.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


if "--version" in args:
    print("0.9.0" if scenario == "wrong_version" else "1.3.0")
    sys.exit()
if args[:2] == ["auth", "check"]:
    provider = args[args.index("--provider") + 1]
    emit(
        {
            "status": "not_ready" if scenario == "auth_missing" else "ready",
            "provider": provider,
            "authType": "api_key" if scenario == "api_key" or provider != "openai" else "oauth",
        }
    )
    sys.exit(1 if scenario == "auth_missing" else 0)

prompt_count = 0
for raw in sys.stdin.buffer:
    command = json.loads(raw)
    kind = command["type"]
    with audit_file.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": kind}) + "\n")
    response = {"type": "response", "id": command.get("id"), "command": kind, "success": True}
    if kind == "get_state":
        provider = args[args.index("--provider") + 1]
        base = (
            "https://api.z.ai/api/coding/paas/v4"
            if provider == "zai"
            else "https://api.openai.com/v1"
        )
        if scenario == "paid_endpoint":
            base = "https://api.z.ai/api/paas/v4"
        response["data"] = {
            "model": {
                "provider": provider,
                "id": "wrong-model" if scenario == "wrong_model" else args[args.index("--model") + 1],
                "api": "openai-responses",
                "baseUrl": base,
            }
        }
    elif kind == "prompt":
        response["data"] = {"disposition": "handled" if scenario == "handled" else "started"}
    emit(response)
    if kind == "abort":
        sys.exit()
    if kind != "prompt" or scenario == "handled":
        continue
    prompt_count += 1
    if scenario == "timeout":
        time.sleep(10)
        continue
    if scenario == "early_exit":
        sys.exit()
    if scenario == "tool":
        emit(
            {
                "type": "tool_execution_start",
                "toolName": "bash",
                "args": {"command": "never executed"},
            }
        )
        continue
    if scenario in {"quota", "auth_error"}:
        emit(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "429 quota exceeded"
                    if scenario == "quota"
                    else "401 OAuth expired",
                },
            }
        )
    else:
        text = (
            "not JSON"
            if scenario == "invalid" or (scenario == "repair" and prompt_count == 1)
            else '{"text":"有效结果\\u2028仍为同一JSONL"}'
        )
        emit(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "stop",
                    "content": [{"type": "text", "text": text}],
                    "usage": {"input": 12, "output": 5},
                },
            }
        )
    emit({"type": "agent_end", "willRetry": False})
    if scenario == "end_is_not_settled":
        emit(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "429 late quota error after agent_end",
                },
            }
        )
    emit({"type": "agent_settled", "aborted": scenario == "aborted"})
