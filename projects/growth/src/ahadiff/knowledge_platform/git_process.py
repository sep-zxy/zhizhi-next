"""Bounded, invocation-owned Git subprocesses; never inherit protocol stdin."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .model._codex_job import CodexOwnedJob


def run_git(command: list[str], *, cwd: Path, env: dict[str, str],
            input_data: bytes | None = None, timeout: float = 30) -> subprocess.CompletedProcess[bytes]:
    """Capture transient output without inherited-pipe drains after a parent exits.

    Windows starts an stdin-gated helper inside its own nested kill-on-close Job.
    POSIX owns a new process group. Only this invocation's tree is terminated.
    """
    windows = os.name == "nt"
    original_command = command
    if windows:
        gate = (
            "import os,subprocess,sys; "
            "sys.exit(subprocess.call(sys.argv[2:],stdin=None if sys.argv[1]=='input' "
            "else subprocess.DEVNULL) if os.read(0,1)==b'\\0' else 2)"
        )
        command = [sys.executable, "-B", "-s", "-c", gate,
                   "input" if input_data is not None else "none", *command]
    deadline = time.monotonic() + timeout
    job = None
    process = None
    writer = None
    errors: list[BaseException] = []
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                command, cwd=cwd, env=env,
                stdin=subprocess.PIPE if windows or input_data is not None else subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, bufsize=0,
                start_new_session=not windows,
            )
            if windows:
                job = CodexOwnedJob()
                job.assign(process.pid)
            if process.stdin is not None:
                payload = (b"\0" if windows else b"") + (input_data or b"")
                def feed() -> None:
                    try:
                        remaining = memoryview(payload)
                        while remaining:
                            count = process.stdin.write(remaining)
                            if not count:
                                raise OSError("Git input pipe closed")
                            remaining = remaining[count:]
                    except BrokenPipeError:
                        pass  # Git's exit code determines whether rejected input is an error.
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        try:
                            process.stdin.close()
                        except OSError as exc:
                            errors.append(exc)
                writer = threading.Thread(target=feed, daemon=True)
                writer.start()
            process.wait(timeout=max(0, deadline - time.monotonic()))
        finally:
            # Close the entire owned tree BEFORE waiting for pipes or the direct parent.
            cleanup_error = False
            if job is not None:
                try:
                    job.close()
                except OSError:
                    cleanup_error = True
                    # A failed close must not leave descendants running merely because
                    # the direct helper can be killed. Never target the outer Job.
                    try:
                        job.terminate()
                    except OSError:
                        pass
                    try:
                        job.close()
                    except OSError:
                        pass
            elif process is not None:
                if windows:
                    # Assignment failed: helper has not received GO and cannot spawn Git.
                    if process.poll() is None:
                        process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if writer is not None:
                    writer.join(timeout=5)
                    if writer.is_alive():
                        raise RuntimeError("Git input cleanup failed")
                elif process.stdin is not None:
                    process.stdin.close()
            if cleanup_error:
                raise RuntimeError("Git process cleanup failed") from None
        if errors:
            raise RuntimeError("Git input transfer failed") from None
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(original_command, process.returncode,
                                           stdout.read(), stderr.read())
