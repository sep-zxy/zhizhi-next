"""Fixed Ollama response for an isolated installed UI rehearsal, never live evidence."""

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
            context = json.loads(prompt.split("\n", 1)[1])
            sources = context["code_changes"]
            if len(sources) != 1 or not isinstance(request.get("format"), dict):
                raise ValueError("expected one approved source and native schema")
            source = sources[0]
            if source["relative_path"] != "src/demo/route/RoutePlanner.java":
                raise ValueError("unexpected source")
            opportunity = {
                "title": "并发查询与执行器边界",
                "reason": "RoutePlanner 将查询交给显式执行器并组合结果。",
                "learning_goal": "解释任务由哪个执行器执行，join() 是否阻塞调用者。",
                "source_refs": [source["source_ref_id"]],
                "estimated_minutes": 4,
                "topic_suggestions": ["Java 并发任务与执行器"],
                "uncertainties": ["未从代码变化单独证明异常路径的运行表现"],
            }
            content = json.dumps({"opportunities": [opportunity]}, ensure_ascii=False)
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 42,
                "eval_count": 27,
            }
            entry = {
                "mode": "replay_not_live",
                "model": request.get("model"),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "source_ref_id": source["source_ref_id"],
                "has_native_schema": True,
                "stream": False,
            }
            previous = (
                json.loads(self.log_path.read_text(encoding="utf-8"))
                if self.log_path.exists() else []
            )
            previous.append(entry)
            self.log_path.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", "opportunity-replay-1")
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
