"""Fixed Pi JSON-mode journey. No console injection, new grant, or model retry."""
from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import threading
import time


def consume(stream, limit, output, failed):
    """Drain both pipes without logging model text, diagnostics, or credentials."""
    total = 0
    try:
        while block := stream.read(8192):
            total += len(block)
            if total > limit:
                failed.set()
            elif output is not None:
                output.append(block)
    except (OSError, ValueError):
        failed.set()
    finally:
        stream.close()


def checked_events(raw):
    """Return fixed counters only; success also requires independently signed receipts."""
    from ahadiff.knowledge_platform.pi_acceptance import ARGUMENTS
    from ahadiff.knowledge_platform.model.contracts import canonical_hash
    records = [json.loads(line) for line in raw.decode('utf-8').split('\n') if line.strip()]
    if any(not isinstance(row, dict) for row in records):
        raise ValueError('PI_ONCE_EVENTS_INCOMPLETE')
    tools = [row for row in records if row.get('type') == 'tool_execution_end']
    starts = [row for row in records if row.get('type') == 'tool_execution_start']
    messages = [row['message'] for row in records
                if row.get('type') == 'message_end' and row.get('message', {}).get('role') == 'assistant']
    if (len(tools) != 1 or len(starts) != 1 or tools[0].get('toolName') != 'zhizhi_search'
            or tools[0].get('isError') is not False
            or [message.get('stopReason') for message in messages] != ['toolUse', 'stop']
            or any(message.get('provider') != 'zhizhi-codex-cli'
                   or message.get('model') != 'gpt-6.1-sol' for message in messages)):
        raise ValueError('PI_ONCE_EVENTS_INCOMPLETE')
    content = messages[0].get('content')
    final = messages[1].get('content')
    if (not isinstance(content, list) or len(content) != 1
            or content[0].get('type') != 'toolCall' or content[0].get('name') != 'zhizhi_search'
            or content[0].get('arguments') != ARGUMENTS or not content[0].get('id')
            or starts[0].get('toolName') != 'zhizhi_search' or starts[0].get('args') != ARGUMENTS
            or starts[0].get('toolCallId') != content[0]['id']
            or tools[0].get('toolCallId') != content[0]['id']
            or not isinstance(final, list) or not final
            or any(part.get('type') != 'text' or not isinstance(part.get('text'), str) for part in final)):
        raise ValueError('PI_ONCE_EVENTS_INCOMPLETE')
    lifecycle = [row.get('type') for row in records if row.get('type') in
                 {'tool_execution_start', 'tool_execution_end'} or
                 (row.get('type') == 'message_end' and row.get('message', {}).get('role') == 'assistant')]
    if lifecycle != ['message_end', 'tool_execution_start', 'tool_execution_end', 'message_end']:
        raise ValueError('PI_ONCE_EVENTS_INCOMPLETE')
    text = ''.join(part['text'] for part in final)
    return {'assistant_turns': 2, 'search_tools': 1,
            'answer_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'search_result_hash': canonical_hash(tools[0].get('result', {}).get('details'))}


def run_once(host, env):
    """Own platform and Pi under the existing kill-on-close job, then exit."""
    import msvcrt
    from ahadiff.knowledge_platform.pi_acceptance import PI_PROMPT, active_scope, completion_summary

    lock_path = host.within('data/launcher.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open('a+b')
    owned = False
    readers = []
    result = {'status': 'blocked', 'code': 'PI_ONCE_INCOMPLETE', 'live_e2e': 'not_verified'}
    try:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        owned = True
        stop = host.within('data/stop.request')
        if stop.exists():
            stop.unlink()
        scope = active_scope(host.ROOT)
        scope.read_source()
        # A previous attempt is never a reason to run the model again.
        if any(scope.receipts.read(key) is not None for key in
               ('run', 'model-attempt:1', 'model-attempt:2', 'search-attempt', 'search-result')):
            raise ValueError('PI_ONCE_ATTEMPT_ALREADY_EXISTS')
        config = host.load_config(owner_acceptance=True)
        args, port, authentication = host.platform_command(
            config, owner_acceptance=True, owner_model=True, owner_pi=True)
        args.append('--pi-search-only')
        platform = host.spawn(args, 'platform', env)
        host.await_health(platform, port, 'platform', authentication)
        pi_env = dict(env)
        pi_env.update(host.owner_pi_environment())
        command = [str(host.ROOT / 'runtime/node/node.exe'), '--no-experimental-strip-types',
                   str(host.ROOT / 'app/pi/launch-codex-interactive.mjs'), '--once-json']
        helper = [sys.executable, '-B', '-s', '-X', 'utf8', str(host.ROOT / 'app/launcher.py'),
                  '_child', json.dumps(command), 'pipe']
        child = subprocess.Popen(helper, cwd=host.within('data/owner-acceptance/pi'), env=pi_env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        host.CHILDREN.append(child)
        host.JOB.assign(child)  # No child execution before the ownership gate.
        failed = threading.Event()
        output = []
        for stream, limit, target in ((child.stdout, 2_000_000, output), (child.stderr, 65_536, None)):
            thread = threading.Thread(target=consume, args=(stream, limit, target, failed), daemon=True)
            thread.start()
            readers.append(thread)
        child.stdin.write(b'GO\n' + PI_PROMPT.encode('utf-8'))
        child.stdin.close()  # Official Pi print mode reads the exact prompt until EOF.
        deadline = time.monotonic() + 300
        while child.poll() is None:
            if failed.is_set() or stop.exists() or time.monotonic() >= deadline or platform.poll() is not None:
                raise ValueError('PI_ONCE_INTERRUPTED')
            time.sleep(.05)
        for thread in readers:
            thread.join(timeout=2)
        if child.returncode != 0 or failed.is_set() or any(thread.is_alive() for thread in readers):
            raise ValueError('PI_ONCE_PROCESS_FAILED')
        counts = checked_events(b''.join(output))
        answer = counts.pop('answer_sha256')
        search = counts.pop('search_result_hash')
        result = {**completion_summary(host.ROOT, answer_sha256=answer, search_result_hash=search), **counts}
    except (Exception, KeyboardInterrupt):
        # Never expose exception strings from subprocesses or private runtime state.
        raise RuntimeError('PI_ONCE_FAILED_CLOSED') from None
    finally:
        if host.JOB is not None:
            host.JOB.close()
        for child in reversed(host.CHILDREN):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for thread in readers:
            thread.join(timeout=2)
        for child in host.CHILDREN:
            for name in ('stdin', 'stdout', 'stderr'):
                stream = getattr(child, name, None)
                if stream is not None:
                    stream.close()
        for stream in host.LOGS:
            stream.close()
        lock.close()
        if owned:
            host.write_json('reports/pi-once.json', result)
        print(json.dumps(result), flush=True)
