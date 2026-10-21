"""Deterministic local Ollama stand-in for an isolated UI integration rehearsal.

This is never a live model or acceptance evidence. It verifies only the
installed client's HTTP request, structured reply, and cloud receipt path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    log_path: Path

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(length))
            prompt = request["messages"][0]["content"]
            if request.get("stream") is not False or not isinstance(prompt, str):
                raise ValueError("unexpected Ollama request")
            second_turn = "你能改什么" in prompt
            reply = {
                "reply": (
                    "认证说明请求属于谁，JWT 本身不代表所有接口权限。"
                    if not second_turn else
                    "认证回答你是谁；授权再判断你能否修改指定文档。"
                ),
                "suggestion": ({
                    "title": "Web 认证与授权",
                    "reason": "继续区分身份认证与操作权限。",
                } if second_turn else None),
            }
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 42,
                "eval_count": 27,
            }
            previous = (
                json.loads(self.log_path.read_text(encoding="utf-8"))
                if self.log_path.exists() else []
            )
            if not isinstance(previous, list):
                previous = [previous]
            previous.append({
                    "mode": "replay_not_live",
                    "model": request.get("model"),
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "contains_u1": "我最近总看到 JWT" in prompt,
                    "contains_u2": "你是谁" in prompt and "你能改什么" in prompt,
                    "has_native_schema": isinstance(request.get("format"), dict),
                    "stream": request.get("stream"),
            })
            self.log_path.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", "acceptance-replay-1")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_error(400)

    def log_message(self, _format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    Handler.log_path = args.log
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
