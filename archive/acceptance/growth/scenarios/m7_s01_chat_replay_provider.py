"""Fixed response for S01 memory chat rehearsal; never live model evidence."""

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
                question != "我对 CompletableFuture 修正过什么误解？"
                or memory["memory_status"] != "current"
                or not notes or original is None
                or not all(item.get("historical") is True for item in attempts)
                or "新建一个线程" not in original["answer_text"]
                or "不会等待" not in original["answer_text"]
                or "执行器决定" not in correction["answer_text"]
                or "join 会等待" not in correction["answer_text"]
                or "执行器安排" not in notes[0]["content_text"]
            ):
                raise ValueError("S01 memory context incomplete")
            reply = {
                "reply": (
                    "你过去认为每次 supplyAsync 都会新建线程，join 不会等待。"
                    "后来你修正为：任务交给传入的 executor，线程由执行器安排；"
                    "thenCombine 组合结果，join 可能等待并阻塞调用方。"
                    "这来自你的两次回答和后写的笔记。"
                ),
                "suggestion": None,
            }
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 63,
                "eval_count": 45,
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
                "reply": reply["reply"],
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", "s01-chat-replay-1")
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
