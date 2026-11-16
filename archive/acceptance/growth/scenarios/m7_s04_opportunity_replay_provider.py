"""Fixed S04 code opportunity response for installed UI rehearsal only."""

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
            request = json.loads(self.rfile.read(int(self.headers["content-length"])))
            prompt = request["messages"][0]["content"]
            context = json.loads(prompt.split("\n", 1)[1])
            sources = context["code_changes"]
            if (request.get("stream") is not False or not isinstance(request.get("format"), dict)
                    or len(sources) != 1 or sources[0]["relative_path"] != "src/access.ts"):
                raise ValueError("unexpected S04 source or model format")
            source = sources[0]
            opportunity = {
                "title": "文档所有者校验与资源授权",
                "reason": "access.ts 的修改在写入前检查当前用户是否为文档所有者。",
                "learning_goal": "区分 JWT 身份认证与文档所有者授权，并解释 Bob 为何被拒绝。",
                "source_refs": [source["source_ref_id"]],
                "estimated_minutes": 4,
                "topic_suggestions": ["Web 认证与授权"],
                "uncertainties": ["仅凭这段代码不能证明所有 API 都执行同样的授权检查"],
            }
            content = json.dumps({"opportunities": [opportunity]}, ensure_ascii=False)
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": content},
                "done": True, "done_reason": "stop", "prompt_eval_count": 42,
                "eval_count": 27,
            }
            previous = (json.loads(self.log_path.read_text(encoding="utf-8"))
                        if self.log_path.exists() else [])
            previous.append({
                "mode": "replay_not_live", "model": request.get("model"),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "source_ref_id": source["source_ref_id"],
                "has_native_schema": True, "stream": False,
            })
            self.log_path.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", "s04-opportunity-replay-1")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
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
