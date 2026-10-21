"""Fixed feedback replies for isolated installed UI rehearsal; never live evidence."""

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
            if request.get("stream") is not False or not isinstance(request.get("format"), dict):
                raise ValueError("expected non-streaming native JSON schema")
            context = json.loads(prompt.split("\n", 1)[1])
            answer = context["user_answer"]
            refs = context["allowed_source_refs"]
            if len(refs) != 1 or "RoutePlanner.java" not in context["approved_code_context"]:
                raise ValueError("unexpected approved code context")
            is_error = (
                "仍由 main 线程串行执行" in answer
                or "每调用一次 supplyAsync 就新建一个线程" in answer
            )
            is_correction = (
                "supplyAsync 的第二个参数" in answer
                or "线程是不是新建由执行器决定" in answer
            )
            if not (is_error or is_correction):
                raise ValueError("unexpected rehearsal answer")
            feedback = {
                "summary": "这次回答需要修正线程创建与 join 等待的判断。" if is_error else
                    "修正回答指出了执行器、线程创建和 join 等待的边界。",
                "user_claims": [answer],
                "code_facts": [
                    "两次 supplyAsync 均传入 executor。",
                    "thenCombine 组合两个结果，join 等待最终值。",
                ],
                "general_principles": [
                    "显式传入 Executor 会影响异步任务的调度位置。",
                    "任务提交次数不等于新建线程次数；join 等待组合结果并可能阻塞调用方。",
                ],
                "inferences": ["具体并发时序仍需结合执行器实现和运行记录验证。"],
                "corrections": (
                    ["代码把查询提交给传入的 executor；不能断言仍在 main 线程串行执行。"]
                    if "仍由 main 线程串行执行" in answer else
                    ["显式 executor 不保证每个任务新建线程；join 会等待组合结果。"]
                ) if is_error else [],
                "source_refs": refs,
            }
            index = 1 if is_error else 2
            response = {
                "model": "acceptance-replay-model",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(feedback, ensure_ascii=False),
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 56,
                "eval_count": 39,
            }
            previous = json.loads(self.log_path.read_text(encoding="utf-8")) if self.log_path.exists() else []
            previous.append({
                "mode": "replay_not_live",
                "request_no": index,
                "model": request.get("model"),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "source_ref_ids": refs,
                "has_native_schema": True,
                "stream": False,
            })
            self.log_path.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", f"feedback-replay-{index}")
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
    args = parser.parse_args()
    Handler.log_path = args.log
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
