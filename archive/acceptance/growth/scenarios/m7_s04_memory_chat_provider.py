"""Fixed S04 memory answer for local rehearsal; it is not live model evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


QUESTION = "我现在怎样区分登录和权限？"
REPLY = (
    "你现在会先问登录是否识别了操作者，再按目标资源检查他是否有权操作。"
    "在文档案例里，Bob 即使已登录，仍不是 Alice 文档的所有者，"
    "canEdit 因此拒绝修改并返回 FORBIDDEN。你的笔记也注明："
    "这段代码只展示所有者检查，没有证明 JWT 验签或完整权限系统。"
)


class Handler(BaseHTTPRequestHandler):
    log_path: Path
    note_id: str
    attempt_id: str
    temp_note_id: str
    note_text: str

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
            note_ids = [item["note_id"] for item in notes]
            attempt_ids = [item["attempt_id"] for item in attempts]
            if (
                payload["messages"][-1]["content"] != QUESTION
                or memory["memory_status"] != "current"
                or self.note_id not in note_ids
                or self.attempt_id not in attempt_ids
                or self.temp_note_id in note_ids
                or not any(item["note_id"] == self.note_id
                           and item["content_text"] == self.note_text for item in notes)
                or not all(item.get("historical") is True for item in attempts)
            ):
                raise ValueError("S04 memory context incomplete or contains deleted note")
            response = {
                "model": "acceptance-replay-model",
                "message": {"role": "assistant", "content": json.dumps(
                    {"reply": REPLY, "suggestion": None}, ensure_ascii=False,
                )},
                "done": True, "done_reason": "stop",
                "prompt_eval_count": 75, "eval_count": 51,
            }
            self.log_path.write_text(json.dumps({
                "mode": "replay_not_live",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "memory_status": memory["memory_status"],
                "note_ids": note_ids, "attempt_ids": attempt_ids,
                "historical_attempts": all(item.get("historical") is True
                                           for item in attempts),
                "reply": REPLY,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("x-request-id", "s04-memory-chat-replay-1")
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
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    artifacts = args.run / "artifacts"
    answer = json.loads((artifacts / "answer-note-ui.json").read_text(encoding="utf-8"))
    created = json.loads((artifacts / "temp-create.json").read_text(encoding="utf-8"))
    Handler.note_id = answer["note_id"]
    Handler.attempt_id = answer["attempt_id"]
    Handler.temp_note_id = created["temp_note_id"]
    Handler.note_text = answer["note_text"]
    Handler.log_path = artifacts / "memory-chat-provider-request.json"
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
