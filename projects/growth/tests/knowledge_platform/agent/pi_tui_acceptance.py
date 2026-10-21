"""Opt-in real Pi TUI + real local HTTP/SQLite checks, never a live model or user approval.

This drives synthetic keyboard input against a temporary fixture owner. It is not
proof of a real human sign-off or real subscription/model/channel acceptance.
Run explicitly with --pi-cli and --extension after installing pinned dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import contextlib
import fcntl
import json
import os
import pty
import re
import secrets
import select
import socket
import struct
import subprocess
import tempfile
import termios
import threading
import time
from pathlib import Path

import uvicorn

from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
TEXT = "Fixture architecture: asynchronous boundaries isolate failures."


class RealTerminal:
    def __init__(self, command: list[str], env: dict[str, str], cwd: Path):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 70, 180, 0, 0))
        self.process = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
        os.close(slave)
        self.output = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read(self, duration: float = 0.1) -> str:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                try:
                    raw = os.read(self.master, 65536)
                except OSError:
                    break
                if not raw:
                    break
                if b"\x1b[6n" in raw:
                    os.write(self.master, b"\x1b[1;1R")
                self.output += self.decoder.decode(raw)
        return ANSI.sub("", self.output)

    def command(self, value: str) -> None:
        self.read(0.2)
        self.output = ""
        os.write(self.master, value.encode("utf-8"))
        self.read(0.15)
        os.write(self.master, b"\r")

    def expect(self, value: str, timeout: float = 12) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = self.read()
            if value in output or re.sub(r"\s+", "", value) in re.sub(r"\s+", "", output):
                return output
            if self.process.poll() is not None:
                break
        raise AssertionError(f"Expected {value!r}; terminal tail:\n{self.read()[-6000:]}")

    def key(self, value: bytes) -> None:
        os.write(self.master, value)
        self.read(0.4)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=4)
        os.close(self.master)


def run(pi_cli: Path, extension: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="zhizhi-real-tui-fixture-") as temporary:
        root = Path(temporary)
        owner = Actor("tui-fixture-owner", "tui-fixture-user", "user", frozenset({"*"}))
        generator = Actor("tui-fixture-owner", "tui-fixture-agent", "agent", frozenset({"*"}))
        token = secrets.token_urlsafe(32)  # Ephemeral local fixture, never a production account.
        app = create_application(root / "state", tokens={token: owner})
        domain, intake = app.state.domain, app.state.intake_service
        first = intake.receive({"text": TEXT}, actor=owner, idempotency_key="keep-fixture")
        second = intake.receive(
            {"text": TEXT + " Discard fixture."}, actor=owner, idempotency_key="discard-fixture"
        )
        asyncio.run(intake.process_next(actor=owner))
        asyncio.run(intake.process_next(actor=owner))
        server_socket = socket.socket()
        server_socket.bind(("127.0.0.1", 0))
        port = server_socket.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [server_socket]}, daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "Fixture domain HTTP service did not start"
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
        env.update(
            HOME=str(root),
            TERM="xterm-256color",
            PI_CODING_AGENT_DIR=str(root / "agent"),
            PI_OFFLINE="1",
            PI_SKIP_VERSION_CHECK="1",
            ZHIZHI_ALLOWED_PROVIDERS="",
            ZHIZHI_API_URL=f"http://127.0.0.1:{port}",
            ZHIZHI_USER_TOKEN=token,
        )
        version = subprocess.run(
            ["node", str(pi_cli), "--version"],
            env=env,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=True,
        )
        assert version.stdout.strip() == "1.3.0", "Real TUI acceptance requires pinned Pi 1.3.0"
        command = [
            "node",
            str(pi_cli),
            "--no-session",
            "--offline",
            "--no-approve",
            "--no-tools",
            "--no-mcp",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-themes",
            "--extension",
            str(extension),
        ]
        terminal = RealTerminal(command, env, root)
        try:
            terminal.read(2)
            terminal.command(f"/zhizhi-keep {first['intake_id']}")
            terminal.expect("收藏这份资料？")
            terminal.key(b"\x1b")
            assert not domain.read_intake(first["intake_id"], actor=owner).get("source_id")
            terminal.command(f"/zhizhi-keep {first['intake_id']}")
            terminal.expect("收藏这份资料？")
            terminal.key(b"\r")
            terminal.expect('"state":"retained"')
            source_id = domain.read_intake(first["intake_id"], actor=owner)["source_id"]
            terminal.command(f"/zhizhi-discard {second['intake_id']}")
            terminal.expect("丢弃这份预览？")
            terminal.key(b"\r")
            terminal.expect('"state":"discarded"')
            assert domain.read_intake(second["intake_id"], actor=owner)["state"] == "discarded"
            change = domain.propose_change(
                {
                    "title": "TUI fixture architecture",
                    "body": TEXT,
                    "visibility": "private",
                    "evidence_refs": [{"source_id": source_id}],
                    "risk_flags": ["core_conclusion"],
                    "metadata": {"audit_marker": "fixture-semantic-metadata"},
                },
                actor=generator,
                idempotency_key="major-fixture",
            )
            terminal.command(f"/zhizhi-review {change['change_id']}")
            dialog = terminal.expect("确认以下具体知识变更？")
            assert change["patch_hash"] in dialog and TEXT in dialog
            assert "fixture-semantic-metadata" in dialog
            assert "publication_intent" in dialog and "visibility" in dialog
            terminal.key(b"\x1b")
            assert domain.get_change(change["change_id"], actor=owner)["state"] == "needs_user"
            terminal.command(f"/zhizhi-review {change['change_id']}")
            terminal.expect("确认以下具体知识变更？")
            old_policy = domain.policy_version
            domain.policy_version = "fixture-policy-changed-after-dialog"
            terminal.key(b"\r")
            terminal.expect("REVIEW_REQUIRED")
            assert domain.get_change(change["change_id"], actor=owner)["state"] == "needs_user"
            domain.policy_version = old_policy
            terminal.command(f"/zhizhi-review {change['change_id']}")
            terminal.expect("确认以下具体知识变更？")
            terminal.key(b"\r")
            terminal.expect('"change_state":"approved"')
            assert domain.get_change(change["change_id"], actor=owner)["state"] == "approved"
            with domain.store.reading() as connection:
                reviews = connection.execute(
                    "SELECT data FROM kp_records WHERE kind='review'"
                ).fetchall()
            assert len(reviews) == 1
            assert json.loads(reviews[0]["data"])["actor_id"] == owner.actor_id
            writer = Actor(owner.owner_id, "tui-fixture-writer", "service", frozenset({"*"}))
            domain.apply_change(change["change_id"], actor=writer, idempotency_key="first-apply")
            revision = domain.propose_change(
                {
                    "entry_id": change["entry_id"],
                    "base_revision": 1,
                    "title": "TUI fixture architecture",
                    "body": TEXT + "\n\n" + TEXT,
                    "visibility": "private",
                    "evidence_refs": [{"source_id": source_id}],
                    "risk_flags": ["core_conclusion"],
                },
                actor=generator,
                idempotency_key="old-revision-fixture",
            )
            terminal.command(f"/zhizhi-review {revision['change_id']}")
            dialog = terminal.expect("确认以下具体知识变更？")
            assert "Base: 1" in dialog and TEXT in dialog
            assert revision["patch_hash"] in dialog
            terminal.key(b"\r")
            terminal.expect('"change_state":"approved"')
            assert domain.get_change(revision["change_id"], actor=owner)["state"] == "approved"
            # Pi may create an empty auth store at startup; only its public auth-check API
            # is authoritative. Never parse or export that credential file, even in tests.
            auth = subprocess.run(
                [
                    "node",
                    str(pi_cli),
                    "auth",
                    "check",
                    "--provider",
                    "openai",
                    "--json",
                    "--no-refresh",
                ],
                env=env,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            assert json.loads(auth.stdout).get("status") != "ready"
            return {
                "runtime": "real Pi 1.3.0 TUI",
                "transport": "real loopback HTTP/SQLite",
                "fixture_owner": True,
                "synthetic_keyboard": True,
                "model_calls": 0,
                "passed": [
                    "keep-cancel-and-confirm",
                    "discard-confirm",
                    "review-cancel",
                    "stale-policy-rejected",
                    "bound-user-review-recorded",
                    "exact-old-revision-review",
                ],
            }
        finally:
            terminal.close()
            server.should_exit = True
            thread.join(timeout=10)
            with contextlib.suppress(OSError):
                server_socket.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-cli", type=Path, required=True)
    parser.add_argument("--extension", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.pi_cli.resolve(), args.extension.resolve()), ensure_ascii=False))
