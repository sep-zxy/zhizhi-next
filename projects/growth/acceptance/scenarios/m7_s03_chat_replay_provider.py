"""Fixed S03 memory answer for installed rehearsal, never live model evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


QUESTION = "我对订单缓存到期有什么误解？现在怎么理解？"
REPLY = (
    "你过去以为 TTL 到点会有后台线程自动删除缓存并重新查询价格。"
    "后来你修正为：PriceCache.get 在访问时比较当前时间和 expiresAt；"
    "只有下一次 get 发现过期才执行 load，未访问就不会自动刷新。"
    "你的笔记还提醒 HashMap 并发安全性需单独检查。"
)


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
            payload = json.loads(prompt[prompt.index("{"):])
            memory = payload["memory"]
            notes = memory["notes"]
            attempts = memory["attempts"]
            question = payload["messages"][-1]["content"]
            by_id = {item["attempt_id"]: item for item in attempts}
            correction = next(
                (item for item in attempts if item.get("parent_attempt_id") in by_id), None,
            )
            original = by_id[correction["parent_attempt_id"]] if correction else None
            if (
                question != QUESTION or memory["memory_status"] != "current"
                or not notes or original is None
                or not all(item.get("historical") is True for item in attempts)
                or "后台线程自动删除缓存" not in original["answer_text"]
                or "没有下一次访问" not in correction["answer_text"]
                or "HashMap" not in notes[0]["content_text"]
                or "到期不会自动刷新" not in notes[0]["content_text"]
            ):
                raise ValueError("S03 memory context incomplete")
            reply = {"reply": REPLY, "suggestion": None}
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)},
                "done": True, "done_reason": "stop",
                "prompt_eval_count": 67, "eval_count": 48,
            }
            self.log_path.write_text(json.dumps({
                "mode": "replay_not_live",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "memory_status": memory["memory_status"],
                "note_ids": [item["note_id"] for item in notes],
                "attempt_ids": [item["attempt_id"] for item in attempts],
                "original_attempt_id": original["attempt_id"],
                "correction_attempt_id": correction["attempt_id"],
                "all_attempts_historical": True,
                "reply": REPLY,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", "s03-chat-replay-1")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, StopIteration):
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
