"""Fixed S04 answer feedback for installed rehearsal, never live evidence."""

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
            answer = context["user_answer"]
            refs = context["allowed_source_refs"]
            if (request.get("stream") is not False or not isinstance(request.get("format"), dict)
                    or len(refs) != 1 or "access.ts" not in context["approved_code_context"]
                    or "Bob" not in answer or "canEdit" not in answer):
                raise ValueError("unexpected S04 answer or approved code context")
            feedback = {
                "summary": "回答正确区分了身份认证与文档所有者授权。",
                "user_claims": [answer],
                "code_facts": [
                    "canEdit 在写入前比较 user.id 与 doc.ownerId。",
                    "Bob 不是 Alice 文档的所有者，修改被拒绝并返回 FORBIDDEN。",
                ],
                "general_principles": [
                    "识别用户身份不等于授权访问任意资源。",
                    "资源操作仍需按当前主体和目标资源校验权限。",
                ],
                "inferences": ["此代码未展示 JWT 验签，也不能代表完整权限系统。"],
                "corrections": [],
                "source_refs": refs,
            }
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": json.dumps(feedback, ensure_ascii=False)},
                "done": True, "done_reason": "stop", "prompt_eval_count": 56,
                "eval_count": 39,
            }
            previous = (json.loads(self.log_path.read_text(encoding="utf-8"))
                        if self.log_path.exists() else [])
            previous.append({
                "mode": "replay_not_live", "model": request.get("model"),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "source_ref_ids": refs, "has_native_schema": True,
            })
            self.log_path.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", "s04-feedback-replay-1")
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
