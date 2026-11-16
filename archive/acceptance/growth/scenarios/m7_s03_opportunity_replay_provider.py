"""Fixed TTL opportunity for S03 UI rehearsal; never live model evidence."""

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
            size = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(size))
            prompt = request["messages"][0]["content"]
            if request.get("stream") is not False or not isinstance(prompt, str):
                raise ValueError("unexpected Ollama request")
            context = json.loads(prompt.split("\n", 1)[1])
            sources = {item["relative_path"]: item for item in context["code_changes"]}
            required = {
                "src/demo/orders/PriceCache.java",
                "src/demo/orders/OrderService.java",
            }
            if set(sources) != required or not isinstance(request.get("format"), dict):
                raise ValueError("S03 requires approved cache and service sources")
            cache = sources["src/demo/orders/PriceCache.java"]
            service = sources["src/demo/orders/OrderService.java"]
            if "expiresAt" not in cache["patch"] or "new PriceCache" not in service["patch"]:
                raise ValueError("S03 TTL code facts missing")
            opportunity = {
                "title": "价格缓存的惰性过期",
                "reason": "PriceCache.get 在访问时比较时间，OrderService 初始化并使用缓存。",
                "learning_goal": (
                    "解释 PriceCache.get 如何在访问时检查 TTL，以及到期是否会自动刷新。"
                ),
                "source_refs": [cache["source_ref_id"], service["source_ref_id"]],
                "estimated_minutes": 4,
                "topic_suggestions": ["订单价格缓存与 TTL"],
                "uncertainties": ["未由本次代码变化证明 HashMap 在并发下安全"],
            }
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": json.dumps(
                    {"opportunities": [opportunity]}, ensure_ascii=False,
                )},
                "done": True, "done_reason": "stop",
                "prompt_eval_count": 72, "eval_count": 43,
            }
            self.log_path.write_text(json.dumps({
                "mode": "replay_not_live",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "source_refs": {name: item["source_ref_id"] for name, item in sources.items()},
                "has_native_schema": True, "stream": False,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", "s03-opportunity-replay-1")
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
