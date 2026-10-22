"""Fixed Pi JSON-mode journey. No console injection, new grant, or model retry."""
from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import threading
import time


# Child journey only, after the launcher's synchronous integrity preflight:
# 90s verified bridge startup + 2 unchanged 120s turns + 30s transport/exit margin.
CHILD_JOURNEY_SECONDS = 90 + 2 * 120 + 30


# Diagnostic projection is independent of acceptance validation. All output keys and
# strings below are fixed literals; never serialize provider text, IDs or arguments.
EVENT_TYPES = frozenset({
    'session', 'agent_start', 'agent_end', 'agent_settled', 'entry_appended', 'turn_start', 'turn_end',
    'message_start', 'message_update', 'message_end', 'tool_execution_start',
    'tool_execution_update', 'tool_execution_end', 'auto_retry_start', 'auto_retry_end',
    'compaction_start', 'compaction_end', 'error',
})
STOP_REASONS = frozenset({'pending', 'stop', 'length', 'toolUse', 'error', 'aborted'})
ERROR_CODES = frozenset({
    'BRIDGE_PROTOCOL_INVALID', 'BRIDGE_NOT_READY', 'BRIDGE_BUSY', 'BRIDGE_CLOSED',
    'BRIDGE_BUSY_OR_CLOSED', 'BRIDGE_CANCELLED', 'BRIDGE_TIMEOUT',
    'BRIDGE_DIAGNOSTIC_TOO_LARGE', 'BRIDGE_INPUT_TOO_LARGE', 'BRIDGE_OUTPUT_TOO_LARGE',
    'BRIDGE_PACKAGED_RUNTIME_REQUIRED', 'BRIDGE_PORTABLE_PATH_REQUIRED',
    'BRIDGE_PIPE_CLOSED', 'BRIDGE_PROCESS_EXITED', 'BRIDGE_PROCESS_UNAVAILABLE',
    'BRIDGE_REQUEST_REJECTED', 'BRIDGE_REQUEST_FAILED', 'BRIDGE_MODEL_FAILED',
    'BRIDGE_MODEL_SELECTION_CHANGED', 'BRIDGE_DOMAIN_ENVELOPE_INVALID',
    'BRIDGE_NEW_USER_MESSAGE_REQUIRED', 'BRIDGE_RETRY_REQUIRES_NEW_USER_MESSAGE',
    'BRIDGE_RUN_UNFINISHED', 'BRIDGE_RUN_NOT_RESTORABLE',
    'BRIDGE_MODEL_BUDGET_EXHAUSTED', 'BRIDGE_PROCESS_BUDGET_EXHAUSTED',
    'BRIDGE_TEXT_ONLY', 'BRIDGE_TRANSCRIPT_INVALID', 'BRIDGE_TRANSCRIPT_CHANGED',
    'BRIDGE_TOOL_ARGUMENTS_INVALID', 'BRIDGE_TOOL_BUDGET_OR_REPLAY_FORBIDDEN',
    'BRIDGE_TOOL_INVALID', 'BRIDGE_TOOL_PROVENANCE_INVALID', 'BRIDGE_TOOL_REPLAY_FORBIDDEN',
    'BRIDGE_TOOL_SCHEMA_CHANGED', 'BRIDGE_TOOL_SCOPE_CHANGED',
    'BRIDGE_ATTEMPT_NOT_AUTHORIZED', 'BRIDGE_CLASSIFICATION_FORBIDDEN',
    'BRIDGE_IDEMPOTENCY_CONFLICT', 'BRIDGE_INPUT_NOT_AUTHORIZED', 'BRIDGE_INVALID_REQUEST',
    'BRIDGE_OUTPUT_INVALID', 'BRIDGE_REQUEST_IN_PROGRESS', 'BRIDGE_RUN_BUDGET_EXHAUSTED',
    'BRIDGE_TOOL_FORBIDDEN', 'BRIDGE_TURN_ORDER_INVALID', 'BRIDGE_LAUNCH_NOT_CONFIGURED',
    'BRIDGE_PROTOCOL_BUDGET_EXHAUSTED', 'BRIDGE_REQUEST_NOT_RUNNING',
    'DOMAIN_REQUEST_FAILED', 'PI_RETRY_POLICY_UNAVAILABLE',
    'APPROVED_EXACT_CODEX_BRIDGE_REQUIRED', 'PACKAGED_CODEX_BRIDGE_REQUIRED',
    'DEDICATED_UNMODIFIED_PI_HOST_REQUIRED',
})
SUMMARY_MAX_BYTES = 2_000_000
SUMMARY_MAX_COUNT = 100_000


def event_summary(raw):
    """Project bounded JSONL diagnostics, including malformed or partial output.

    Counts are observations, not unique failures: Pi can repeat the same error in
    message_update and message_end (including failure-stage observations). Retry counts count events, never trust attempt.
    Only terminal message_end contributes assistant stop reasons.
    """
    summary = {'wrapper_statuses': {}, 'event_counts': {}, 'assistant_stop_reasons': {}, 'error_codes': {},
               'provider_failure_stages': {}, 'retry_counts': {'start': 0, 'end': 0}, 'malformed_json_count': 0,
               'non_object_count': 0, 'truncated': len(raw) > SUMMARY_MAX_BYTES}

    def increment(counts, value, allowed):
        key = value if isinstance(value, str) and value in allowed else 'OTHER'
        counts[key] = min(SUMMARY_MAX_COUNT, counts.get(key, 0) + 1)

    def error_code(value):
        increment(summary['error_codes'], value, ERROR_CODES)

    def failure_stages(message):
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            return
        diagnostics = message.get('diagnostics')
        if not isinstance(diagnostics, list):
            return
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict) or diagnostic.get('type') != 'zhizhi.codex_cli.failure':
                continue
            details = diagnostic.get('details')
            stage = details.get('stage') if isinstance(details, dict) else None
            increment(summary['provider_failure_stages'], stage, {'initialize', 'tool_scope', 'turn'})

    for line in raw[:SUMMARY_MAX_BYTES].split(b'\n'):
        if not line.strip():
            continue
        try:
            row = json.loads(line.decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError):
            summary['malformed_json_count'] = min(SUMMARY_MAX_COUNT, summary['malformed_json_count'] + 1)
            continue
        if not isinstance(row, dict):
            summary['non_object_count'] = min(SUMMARY_MAX_COUNT, summary['non_object_count'] + 1)
            continue
        kind = row.get('type')
        increment(summary['event_counts'], kind, EVENT_TYPES)
        if 'type' not in row and ('status' in row or 'code' in row):
            increment(summary['wrapper_statuses'], row.get('status'), {'waiting_auth'})
            error_code(row.get('code'))
        elif kind == 'message_end':
            message = row.get('message')
            if isinstance(message, dict) and message.get('role') == 'assistant':
                failure_stages(message)
                increment(summary['assistant_stop_reasons'], message.get('stopReason'), STOP_REASONS)
                if 'errorMessage' in message:
                    error_code(message['errorMessage'])
        elif kind == 'message_update':
            event = row.get('assistantMessageEvent')
            if isinstance(event, dict) and event.get('type') == 'error':
                message = event.get('error')
                failure_stages(message)
                error_code(message.get('errorMessage') if isinstance(message, dict) else None)
        elif kind == 'auto_retry_start':
            summary['retry_counts']['start'] = min(SUMMARY_MAX_COUNT, summary['retry_counts']['start'] + 1)
            if 'errorMessage' in row:
                error_code(row['errorMessage'])
        elif kind == 'auto_retry_end':
            summary['retry_counts']['end'] = min(SUMMARY_MAX_COUNT, summary['retry_counts']['end'] + 1)
            if 'finalError' in row:
                error_code(row['finalError'])
        elif kind == 'error':
            error_code(row.get('error_code'))
    return summary


def consume(stream, limit, output, failed, metrics=None):
    """Drain both pipes without logging model text, diagnostics, or credentials."""
    total = 0
    try:
        while block := stream.read(8192):
            total += len(block)
            if metrics is not None:
                metrics["bytes"] = min(total, limit + 1)
            if total > limit:
                if metrics is not None:
                    metrics["output_limit"] = True
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
    started = time.monotonic()
    phase = 'lock'
    child = None
    stdout_metrics = {'bytes': 0}
    stderr_metrics = {'bytes': 0}
    output = []
    process_failure = None
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
        phase = 'scope_verification'
        scope = active_scope(host.ROOT)
        scope.read_source()
        # A previous attempt is never a reason to run the model again.
        if any(scope.receipts.read(key) is not None for key in
               ('run', 'model-attempt:1', 'model-attempt:2', 'search-attempt', 'search-result')):
            raise ValueError('PI_ONCE_ATTEMPT_ALREADY_EXISTS')
        phase = 'platform_configuration'
        config = host.load_config(owner_acceptance=True)
        args, port, authentication = host.platform_command(
            config, owner_acceptance=True, owner_model=True, owner_pi=True)
        args.append('--pi-search-only')
        phase = 'platform_start'
        platform = host.spawn(args, 'platform', env)
        phase = 'platform_health'
        host.await_health(platform, port, 'platform', authentication)
        phase = 'pi_environment'
        pi_env = dict(env)
        pi_env.update(host.owner_pi_environment())
        command = [str(host.ROOT / 'runtime/node/node.exe'), '--no-experimental-strip-types',
                   str(host.ROOT / 'app/pi/launch-codex-interactive.mjs'), '--once-json']
        helper = [sys.executable, '-B', '-s', '-X', 'utf8', str(host.ROOT / 'app/launcher.py'),
                  '_child', json.dumps(command), 'pipe']
        phase = 'helper_spawn'
        child = subprocess.Popen(helper, cwd=host.within('data/owner-acceptance/pi'), env=pi_env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        host.CHILDREN.append(child)
        phase = 'job_assignment'
        host.JOB.assign(child)  # No child execution before the ownership gate.
        failed = threading.Event()
        for stream, limit, target, metrics in ((child.stdout, 2_000_000, output, stdout_metrics),
                                               (child.stderr, 65_536, None, stderr_metrics)):
            thread = threading.Thread(target=consume, args=(stream, limit, target, failed, metrics), daemon=True)
            thread.start()
            readers.append(thread)
        phase = 'prompt_pipe'
        child.stdin.write(b'GO\n' + PI_PROMPT.encode('utf-8'))
        child.stdin.close()  # Official Pi print mode reads the exact prompt until EOF.
        phase = 'pi_process'
        deadline = time.monotonic() + CHILD_JOURNEY_SECONDS
        while child.poll() is None:
            if failed.is_set():
                process_failure = 'output_limit' if stdout_metrics.get('output_limit') or stderr_metrics.get('output_limit') else 'pipe_read_failed'
            elif stop.exists():
                process_failure = 'stop_requested'
            elif time.monotonic() >= deadline:
                process_failure = 'timeout'
            elif platform.poll() is not None:
                process_failure = 'platform_exited'
            if process_failure is not None:
                raise ValueError('PI_ONCE_INTERRUPTED')
            time.sleep(.05)
        for thread in readers:
            thread.join(timeout=2)
        if child.returncode != 0 or failed.is_set() or any(thread.is_alive() for thread in readers):
            process_failure = ('output_limit' if stdout_metrics.get('output_limit') or stderr_metrics.get('output_limit')
                               else 'pipe_read_failed' if failed.is_set()
                               else 'pipe_drain_timeout' if any(thread.is_alive() for thread in readers)
                               else 'child_exit_nonzero')
            raise ValueError('PI_ONCE_PROCESS_FAILED')
        phase = 'event_validation'
        counts = checked_events(b''.join(output))
        answer = counts.pop('answer_sha256')
        search = counts.pop('search_result_hash')
        phase = 'receipt_verification'
        result = {**completion_summary(host.ROOT, answer_sha256=answer, search_result_hash=search), **counts}
    except (Exception, KeyboardInterrupt) as error:
        result.update(phase=phase, code='PI_ONCE_STAGE_FAILED',
                      cancelled=isinstance(error, KeyboardInterrupt))
        # Never expose exception strings from subprocesses or private runtime state.
        raise RuntimeError('PI_ONCE_FAILED_CLOSED') from None
    finally:
        pi_child = child
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
        projection = event_summary(b''.join(output))
        projection['truncated'] = projection['truncated'] or bool(stdout_metrics.get('output_limit'))
        result.update(elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                      stdout_bytes=stdout_metrics['bytes'], stderr_bytes=stderr_metrics['bytes'],
                      child_exit_code=pi_child.returncode if pi_child is not None and type(pi_child.returncode) is int else None,
                      event_summary=projection, process_failure=process_failure)
        if owned:
            host.write_json('reports/pi-once.json', result)
        print(json.dumps(result), flush=True)
