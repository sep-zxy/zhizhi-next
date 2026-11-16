"""Fixed S03 TTL feedback replies for installed UI rehearsal, never live evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

A1 = "TTL 到了以后会有后台线程自动删除缓存，然后自动去查询新的价格。"
A2 = (
    "这里没有看到定时任务。每次 get 会比较当前时间与 expiresAt，"
    "到期以后这次调用才执行 load 并覆盖缓存。如果没有下一次访问，就不会主动刷新。"
)


class Handler(BaseHTTPRequestHandler):
    log_path: Path
    expected_refs: set[str]

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(length))
            prompt = request["messages"][0]["content"]
            if request.get("stream") is not False or not isinstance(request.get("format"), dict):
                raise ValueError("expected non-streaming native JSON schema")
            context = json.loads(prompt.split("\n", 1)[1])
            answer = context["user_answer"]
            refs = context["allowed_source_refs"]
            code = context["approved_code_context"]
            if set(refs) != self.expected_refs or len(refs) != 2:
                raise ValueError("S03 source references differ from approved task")
            if "expiresAt" not in code or "new PriceCache" not in code:
                raise ValueError("S03 approved code lacks TTL facts")
            if answer not in {A1, A2}:
                raise ValueError("unexpected S03 rehearsal answer")
            is_error = answer == A1
            feedback = {
                "summary": (
                    "这段代码在访问缓存时判断是否过期，没有显示后台定时刷新。"
                    if is_error else
                    "修正回答准确描述了访问时检查 TTL 和下次访问才重新加载。"
                ),
                "user_claims": [answer],
                "code_facts": [
                    "PriceCache.get 比较当前时间与 expiresAt。",
                    "OrderService 创建 PriceCache 并在报价时访问缓存。",
                ],
                "general_principles": [
                    "惰性过期在访问时检查；时间到达本身不会自动触发加载。",
                ],
                "inferences": ["HashMap 在并发访问下的安全性需要另行验证。"],
                "corrections": (
                    ["不能从 expiresAt 推断有后台线程删除缓存或自动查询价格。"]
                    if is_error else []
                ),
                "source_refs": refs,
            }
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": json.dumps(
                    feedback, ensure_ascii=False,
                )},
                "done": True, "done_reason": "stop",
                "prompt_eval_count": 65, "eval_count": 42,
            }
            previous = (
                json.loads(self.log_path.read_text(encoding="utf-8"))
                if self.log_path.exists() else []
            )
            previous.append({
                "mode": "replay_not_live", "request_no": 1 if is_error else 2,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                "source_ref_ids": refs, "has_native_schema": True, "stream": False,
            })
            self.log_path.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", f"s03-feedback-replay-{1 if is_error else 2}")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_error(400)

    def log_message(self, _format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--preparation", type=Path, required=True)
    args = parser.parse_args()
    prepared = json.loads(args.preparation.read_text(encoding="utf-8"))
    Handler.log_path = args.log
    Handler.expected_refs = set(prepared["source_ref_ids"])
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
