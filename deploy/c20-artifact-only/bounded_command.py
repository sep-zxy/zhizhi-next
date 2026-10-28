"""Bounded, sanitized diagnostics for synthetic-only acceptance subprocesses.

Never used by the product or to capture real private inputs. No raw log file,
command arguments, environment values or successful output are exported.
"""
import json
import os
import re
import selectors
import signal
import subprocess
import time

MAX_BYTES = 1024 * 1024
TAIL_BYTES = 64 * 1024
SECRET_LINE = re.compile(r'(?i)(authorization|bearer\s|cookie|password|passwd|secret|token|api[_-]?key|private.key|-----BEGIN)')
TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+|AKIA[A-Z0-9]{16}|[A-Za-z0-9_+/=-]{40,})')
ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|$))')


def sanitize(data):
    text = ANSI.sub('', data.decode('utf-8', errors='replace'))
    text = re.sub(r'-----BEGIN[^\n]*PRIVATE KEY-----.*?(?:-----END[^\n]*PRIVATE KEY-----|$)',
                  '[redacted private key block]', text, flags=re.DOTALL)
    lines = []
    for line in text.splitlines():
        if SECRET_LINE.search(line):
            line = '[redacted sensitive-looking diagnostic line]'
        else:
            line = TOKEN.sub('[redacted token-like value]', line)
            line = re.sub(r'https?://\S+', '[redacted URL]', line)
        lines.append(''.join(c for c in line if c >= ' ' or c == '\t'))
    return '\n'.join(lines)


def read_chunk(fd, size):
    return os.read(fd, size)


def run_synthetic(argv, *, cwd, env, timeout=900, label, max_bytes=MAX_BYTES):
    if not re.fullmatch(r'template-[12]', label):
        raise ValueError('Unapproved synthetic diagnostic label')
    if not 0 < max_bytes <= MAX_BYTES or not 0 < timeout <= 900:
        raise ValueError('Unbounded diagnostic request')
    # argv/env are the existing explicit clean synthetic invocation; never echo.
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    captured = bytearray()
    deadline = time.monotonic() + timeout
    failure = None
    success_completed = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = 'timeout'
                    break
                for key, _ in selector.select(min(remaining, 0.2)):
                    chunk = read_chunk(key.fileobj.fileno(), min(8192, max_bytes - len(captured) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif len(captured) + len(chunk) > max_bytes:
                        failure = 'output_limit'
                        break
                    else:
                        captured.extend(chunk)
                if failure:
                    break
            if failure is None:
                try:
                    child.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    failure = 'timeout'
        success_completed = failure is None and child.returncode == 0
    finally:
        if not success_completed:
            # Only the child session created above, never container/host-wide.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Synthetic Quartz child reap timeout; container cleanup required")
        finally:
            child.stdout.close()
    if failure or child.returncode:
        # Sanitize BEFORE taking the tail so a truncated token cannot evade it.
        data = bytes(captured)
        if failure and not data.endswith(b"\n"):
            # A cap/deadline may split a credential token; omit unfinished line.
            data = data.rsplit(b"\n", 1)[0] + b"\n" if b"\n" in data else b""
        safe = sanitize(data)
        tail = safe.encode('utf-8')[-TAIL_BYTES:].decode('utf-8', errors='replace')
        print(json.dumps({'event': 'synthetic_quartz_failed', 'label': label,
            'failure': failure or 'nonzero_exit', 'exit_code': child.returncode,
            'captured_bytes': len(captured), 'diagnostic_tail': tail,
            'scope': 'synthetic_template_only_not_acceptance'}, ensure_ascii=True), flush=True)
        if failure:
            raise RuntimeError('Synthetic Quartz diagnostic ' + failure)
        raise subprocess.CalledProcessError(child.returncode, ['synthetic-quartz', label])
