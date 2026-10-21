"""Strict private JSONL transport. No HTTP listener, shell or arbitrary module selection."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import signal
import sys
import threading
from typing import Any, BinaryIO, TextIO

from .pi_bridge import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, PiBridge, _uuid


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate protocol field")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise ValueError("Non-finite protocol number")


async def serve(bridge: PiBridge, source: BinaryIO, destination: TextIO) -> None:
    """Drain stdin while one bounded model request runs, so cancel cannot deadlock.

    A daemon reader avoids an uncancellable executor thread holding Windows
    shutdown open. EOF and signals cancel the runner first; its owned Codex Job
    closes and terminates descendants. The parent remains responsible for its
    own final process-exit deadline, not a promise of an OS-wide sandbox.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
    pending: dict[str, asyncio.Task[dict[str, Any]]] = {}
    stopped = threading.Event()
    protocol_failed = False

    def deliver(raw: bytes | None) -> None:
        nonlocal protocol_failed
        if stopped.is_set():
            return
        try:
            queue.put_nowait(raw)
        except asyncio.QueueFull:
            protocol_failed = True
            stopped.set()
            for task in pending.values():
                task.cancel()

    def read() -> None:
        while not stopped.is_set():
            try:
                raw = source.readline(MAX_INPUT_BYTES + 2)
            except Exception:
                raw = b""
            try:
                loop.call_soon_threadsafe(deliver, raw or None)
            except RuntimeError:
                return
            if not raw or len(raw) > MAX_INPUT_BYTES:
                return

    def write(value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
            encoded = json.dumps(PiBridge.error(None, "BRIDGE_OUTPUT_TOO_LARGE"))
        destination.write(encoded + "\n")
        destination.flush()

    def finish(request_id: str, task: asyncio.Task[dict[str, Any]]) -> None:
        pending.pop(request_id, None)
        if task.cancelled():
            result = PiBridge.error(request_id, "BRIDGE_CANCELLED", status="cancelled")
        else:
            try:
                result = task.result()
            except Exception:
                result = PiBridge.error(request_id, "BRIDGE_MODEL_FAILED", status="failed")
        try:
            write(result)
        except (OSError, ValueError):
            stopped.set()

    reader = threading.Thread(target=read, name="pi-private-jsonl", daemon=True)
    reader.start()
    try:
        while not stopped.is_set():
            raw = await queue.get()
            if raw is None:
                break
            request_id: str | None = None
            try:
                if len(raw) > MAX_INPUT_BYTES or not raw.endswith(b"\n"):
                    write(PiBridge.error(None, "BRIDGE_INPUT_TOO_LARGE"))
                    break
                request = json.loads(
                    raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant
                )
                if not isinstance(request, dict):
                    raise ValueError
                request_id = _uuid(request.get("id"))
                if type(request.get("version")) is not int or request["version"] != 1:
                    raise ValueError
                kind = request.get("type")
                if kind == "hello" and set(request) == {"version", "id", "type"}:
                    write(bridge.hello(request_id))
                elif kind == "cancel" and set(request) == {"version", "id", "type", "request_id"}:
                    target = _uuid(request["request_id"])
                    task = pending.get(target)
                    if task is None:
                        write(PiBridge.error(request_id, "BRIDGE_REQUEST_NOT_RUNNING"))
                    else:
                        task.cancel()
                        write(
                            {
                                "version": 1,
                                "id": request_id,
                                "type": "response",
                                "status": "cancel_requested",
                            }
                        )
                elif kind == "turn":
                    if pending:
                        code = (
                            "BRIDGE_REQUEST_IN_PROGRESS" if request_id in pending else "BRIDGE_BUSY"
                        )
                        write(PiBridge.error(request_id, code))
                    else:
                        task = asyncio.create_task(bridge.turn(request))
                        pending[request_id] = task
                        task.add_done_callback(lambda done, key=request_id: finish(key, done))
                else:
                    raise ValueError
            except (ValueError, UnicodeError, TypeError, KeyError):
                write(PiBridge.error(request_id, "BRIDGE_INVALID_REQUEST"))
    finally:
        stopped.set()
        tasks = list(pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if protocol_failed:
            write(PiBridge.error(None, "BRIDGE_PROTOCOL_BUDGET_EXHAUSTED"))


async def _run(bridge: PiBridge) -> None:
    task = asyncio.create_task(serve(bridge, sys.stdin.buffer, sys.stdout))
    loop = asyncio.get_running_loop()
    previous: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, lambda _sig, _frame: loop.call_soon_threadsafe(task.cancel))
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main() -> int:
    """Only the separately approved fixed-root acceptance factory can activate CLI use."""
    code = "BRIDGE_LAUNCH_NOT_CONFIGURED"
    try:
        if sys.argv[1:] != ["--owner-acceptance"]:
            raise ValueError
        from ..owner_identity import _acceptance_root

        factory_module = importlib.import_module("ahadiff.knowledge_platform.owner_acceptance")
        factory = getattr(factory_module, "create_pi_bridge", None)
        if not callable(factory):
            raise ValueError
        bridge = factory(_acceptance_root())
        if not isinstance(bridge, PiBridge):
            raise ValueError
        asyncio.run(_run(bridge))
        return 0
    except Exception:
        # Factory failures may contain file names or identity details. Never serialize them.
        sys.stdout.write(json.dumps(PiBridge.error(None, code)) + "\n")
        sys.stdout.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
