"""Offline fixtures only: no Windows identity, real model, or native process."""
import importlib.util
import ast
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from test_owner_acceptance_launch import launcher
import assemble_product

spec = importlib.util.spec_from_file_location('pi_once', Path(__file__).parent / 'runtime/pi_once.py')
once = importlib.util.module_from_spec(spec)
spec.loader.exec_module(once)


def events():
    from ahadiff.knowledge_platform.pi_acceptance import ARGUMENTS
    return b'\n'.join(json.dumps(row).encode() for row in [
        {'type': 'message_end', 'message': {'role': 'assistant', 'provider': 'zhizhi-codex-cli',
         'model': 'gpt-6.1-sol', 'stopReason': 'toolUse', 'content': [
             {'type': 'toolCall', 'id': 'synthetic-call', 'name': 'zhizhi_search', 'arguments': ARGUMENTS}]}},
        {'type': 'tool_execution_start', 'toolCallId': 'synthetic-call', 'toolName': 'zhizhi_search', 'args': ARGUMENTS},
        {'type': 'tool_execution_end', 'toolCallId': 'synthetic-call', 'toolName': 'zhizhi_search', 'isError': False,
         'result': {'details': {'synthetic': True}}},
        {'type': 'message_end', 'message': {'role': 'assistant', 'provider': 'zhizhi-codex-cli',
         'model': 'gpt-6.1-sol', 'stopReason': 'stop', 'content': [{'type': 'text', 'text': 'PRIVATE-SYNTHETIC'}]}},
    ])


def test_once_wrapper_is_fixed_and_tui_remains(tmp_path):
    assemble_product.write_launchers(tmp_path)
    assert 'owner-pi-once' in (tmp_path / 'owner-pi-once.cmd').read_text()
    assert '%*' not in (tmp_path / 'owner-pi-once.cmd').read_text()
    assert 'pause' not in (tmp_path / 'owner-pi-once.cmd').read_text()
    module = launcher(tmp_path)
    assert module.parse_arguments(['owner-pi-once']).action == 'owner-pi-once'
    for extra in (['--prompt', 'other'], ['--model', 'other'], ['--approve-owner-pi-search']):
        with pytest.raises(SystemExit):
            module.parse_arguments(['owner-pi-once', *extra])
    source = (Path(__file__).parent / 'runtime/launcher.py').read_text()
    assert "new_console=True" in source and "open('CONIN$'" in source
    assert "os.read(sys.stdin.fileno(),3-len(gate))" in source


@pytest.mark.parametrize('failure', [None, 'assign', 'spent', 'invalid-events', 'proof', 'service-exit', 'output-limit', 'cancel', 'deadline', 'process-exit', 'pi-errors', 'stop'])
def test_managed_once_fixture_gate_scope_cleanup_and_no_raw_logging(tmp_path, capsys, failure):
    from ahadiff.knowledge_platform import pi_acceptance as pi
    scope = Mock()
    scope.receipts.read.return_value = {'spent': True} if failure == 'spent' else None
    host = SimpleNamespace(ROOT=tmp_path, CHILDREN=[], LOGS=[], JOB=Mock())
    host.within = lambda name: tmp_path / name
    host.load_config = Mock(return_value={})
    host.platform_command = Mock(return_value=(['platform', '--serve-activated-pi-scope'], 8766, 'configured'))
    host.await_health = Mock()
    host.owner_pi_environment = Mock(return_value={'ZHIZHI_AGENT_TOKEN': 'PRIVATE-SYNTHETIC'})
    host.write_json = Mock()
    platform = Mock()
    platform.poll.return_value = None
    if failure == 'service-exit':
        platform.poll.return_value = 1
    host.spawn = Mock(return_value=platform)
    child = Mock()
    child.poll.return_value = 0
    if failure in {'service-exit', 'output-limit', 'deadline', 'stop'}:
        child.poll.return_value = None
    child.returncode = 7 if failure == 'process-exit' else 0
    child.stdout = io.BytesIO(b'{}' if failure == 'invalid-events' else events())
    if failure == 'pi-errors':
        child.stdout = io.BytesIO(json.dumps({'type': 'message_end', 'message': {
            'role': 'assistant', 'stopReason': 'error', 'errorMessage': 'BRIDGE_NOT_READY',
            'content': [{'text': 'PRIVATE-SYNTHETIC'}]}}).encode() + b'\n{partial')
    if failure == 'process-exit':
        child.stdout = io.BytesIO(events() + b'\n{partial')
    child.stderr = io.BytesIO(b'PRIVATE-SYNTHETIC')
    if failure == 'output-limit':
        child.stderr = io.BytesIO(b'x' * 70_000)
    child.stdin = Mock()
    host.JOB.assign.side_effect = RuntimeError('PRIVATE-SYNTHETIC') if failure == 'assign' else None
    if failure == 'cancel':
        host.JOB.assign.side_effect = KeyboardInterrupt()
    summary = {'status': 'pi_source_search_verified', 'final_delivery': False}
    def assigned_then_write(data):
        host.JOB.assign.assert_called_once_with(child)
        assert data == b'GO\n' + pi.PI_PROMPT.encode()
        if failure == 'stop':
            (tmp_path / 'data/stop.request').touch()
    child.stdin.write.side_effect = assigned_then_write
    clock = [0]
    if failure == 'deadline':
        def timed_out_poll():
            clock[0] = once.CHILD_JOURNEY_SECONDS
            return None
        child.poll.side_effect = timed_out_poll
    with patch.object(once.time, 'monotonic', side_effect=lambda: clock[0]), \
         patch.dict('sys.modules', {'msvcrt': Mock()}), \
         patch.object(pi, 'active_scope', return_value=scope), \
         patch.object(pi, 'completion_summary', return_value=summary,
                      side_effect=RuntimeError('PRIVATE-SYNTHETIC') if failure == 'proof' else None), \
         patch.object(once.subprocess, 'Popen', return_value=child) as popen:
        if failure:
            with pytest.raises(RuntimeError, match='^PI_ONCE_FAILED_CLOSED$'):
                once.run_once(host, {'SAFE': 'inert'})
        else:
            once.run_once(host, {'SAFE': 'inert'})
        host.JOB.close.assert_called_once()
        if failure == 'spent':
            popen.assert_not_called()
            host.spawn.assert_not_called()
        else:
            assert '--pi-search-only' in host.spawn.call_args.args[0]
            assert 'ZHIZHI_AGENT_TOKEN' not in host.spawn.call_args.args[2]
            assert popen.call_args.kwargs['env']['ZHIZHI_AGENT_TOKEN'] == 'PRIVATE-SYNTHETIC'
            assert popen.call_args.args[0][-1] == 'pipe'
            assert '--once-json' in popen.call_args.args[0][-2]
            if failure in {'assign', 'cancel'}:
                child.stdin.write.assert_not_called()
    report = host.write_json.call_args.args[1]
    assert report['stdout_bytes'] <= 2_000_001 and report['stderr_bytes'] <= 65_537
    assert report['elapsed_ms'] >= 0
    assert all(type(report[key]) is int and report[key] >= 0 for key in
               ('preflight_ms', 'prompt_pipe_ms', 'child_elapsed_ms', 'verification_ms', 'cleanup_ms'))
    if failure in {'spent', 'assign', 'cancel'}:
        assert report['child_elapsed_ms'] == report['prompt_pipe_ms'] == 0
    if failure == 'deadline':
        assert report['child_elapsed_ms'] == 360_000
    if failure:
        assert report['code'] == 'PI_ONCE_STAGE_FAILED'
        assert report['phase'] in {'scope_verification', 'job_assignment', 'pi_process', 'event_validation', 'receipt_verification'}
    assert 'event_summary' in report
    if failure in {'spent', 'assign', 'cancel'}:
        assert report['event_summary'] == once.event_summary(b'')
    reason = {'service-exit': 'platform_exited', 'output-limit': 'output_limit',
              'deadline': 'timeout', 'process-exit': 'child_exit_nonzero', 'stop': 'stop_requested'}
    assert report['process_failure'] == reason.get(failure)
    if failure == 'pi-errors':
        assert report['event_summary']['error_codes'] == {'BRIDGE_NOT_READY': 1}
        assert report['event_summary']['assistant_stop_reasons'] == {'error': 1}
    if failure in {'pi-errors', 'process-exit'}:
        assert report['event_summary']['malformed_json_count'] == 1
    assert 'PRIVATE-SYNTHETIC' not in capsys.readouterr().out
    assert 'PRIVATE-SYNTHETIC' not in repr(host.write_json.call_args)


def test_pipe_limits_and_event_validation():
    import threading
    failed = threading.Event()
    output = []
    once.consume(io.BytesIO(b'x' * 100), 10, output, failed)
    assert failed.is_set() and not output
    parsed = once.checked_events(events())
    assert parsed['assistant_turns'] == 2 and parsed['search_tools'] == 1
    assert len(parsed['answer_sha256']) == len(parsed['search_result_hash']) == 64
    for raw in (b'{}', events().replace(b'zhizhi_search', b'other'),
                events().replace(b'"isError": false', b'"isError": true')):
        with pytest.raises(ValueError):
            once.checked_events(raw)
    rows = events().split(b'\n')
    for raw in (b'\n'.join([rows[1], rows[0], *rows[2:]]),
                b'\n'.join([*rows[:2], rows[2].replace(b'synthetic-call', b'wrong-call'), rows[3]])):
        with pytest.raises(ValueError):
            once.checked_events(raw)


def test_once_outer_cancellation_before_runtime_is_nonzero():
    # Execute the real final dispatch guard, without running Windows setup.
    tree = ast.parse((Path(__file__).parent / 'runtime/launcher.py').read_text())
    guard = next(node for node in tree.body[-1].body if isinstance(node, ast.Try))
    code = compile(ast.Module(body=[guard], type_ignores=[]), 'launcher-dispatch', 'exec')
    with patch.object(sys, 'argv', ['launcher.py', 'owner-pi-once']):
        with pytest.raises(SystemExit) as stopped:
            exec(code, {'main': Mock(side_effect=KeyboardInterrupt()), 'sys': sys,
                        'JOB': None, 'CHILDREN': [], 'LOGS': []})
    assert stopped.value.code == 130


def test_safe_projection_pi13_errors_retries_and_hostile_fields():
    secret = 'SECRET_CANARY_DO_NOT_SERIALIZE'
    assistant = {'role': 'assistant', 'stopReason': 'error',
                 'errorMessage': 'BRIDGE_NOT_READY', 'content': [{'text': secret}],
                 'id': secret, 'provider': secret}
    rows = [
        {'type': 'message_update', 'message': assistant,
         'assistantMessageEvent': {'type': 'error', 'reason': 'error', 'error': assistant}},
        {'type': 'message_end', 'message': assistant},
        {'type': 'auto_retry_start', 'attempt': secret, 'maxAttempts': 9999999999,
         'delayMs': secret, 'errorMessage': 'BRIDGE_TIMEOUT'},
        {'type': 'auto_retry_end', 'success': False, 'attempt': -42, 'finalError': secret},
        {'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': secret,
                                           'errorMessage': 'BRIDGE_TIMEOUT ' + secret}},
        {'type': secret, 'errorMessage': secret, 'env': {secret: secret}},
        {'type': 'tool_execution_end', 'toolName': secret, 'args': {secret: secret}, 'result': secret},
        {'type': ['hostile', secret]},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'error', 'error': [secret]}},
        {'type': 'message_end', 'message': [secret]},
        {'type': 'message_end', 'message': {'role': 'user', 'errorMessage': secret}},
        {'type': 'error', 'error_code': {'secret': secret}},
    ]
    raw = b'\n'.join(json.dumps(row).encode() for row in rows)
    summary = once.event_summary(raw + b'\n{"partial":\n\xff\nnull\n[]')
    assert summary['error_codes'] == {'BRIDGE_NOT_READY': 2, 'BRIDGE_TIMEOUT': 1, 'OTHER': 4}
    assert summary['assistant_stop_reasons'] == {'error': 1, 'OTHER': 1}
    assert summary['retry_counts'] == {'start': 1, 'end': 1}
    assert summary['malformed_json_count'] == 2
    assert summary['non_object_count'] == 2
    assert summary['event_counts']['OTHER'] == 2
    encoded = json.dumps(summary)
    assert secret not in encoded and 'attempt' not in encoded and 'toolName' not in encoded
    assert len(encoded) < 2000
    # Prefix/suffix/newline variants must not be extracted from arbitrary text.
    for value in ['xBRIDGE_TIMEOUT', 'BRIDGE_TIMEOUT\n', 'BRIDGE_TIMEOUT: private', {}, [], None]:
        projected = once.event_summary(json.dumps({'type': 'message_end', 'message': {
            'role': 'assistant', 'stopReason': 'error', 'errorMessage': value}}).encode())
        assert projected['error_codes'] == {'OTHER': 1}


def test_safe_projection_bounds_and_does_not_relax_acceptance(monkeypatch):
    monkeypatch.setattr(once, 'SUMMARY_MAX_COUNT', 2)
    raw = b'\n'.join([b'{"type":"auto_retry_start","errorMessage":"BRIDGE_TIMEOUT"}'] * 5)
    summary = once.event_summary(raw)
    assert summary['event_counts'] == {'auto_retry_start': 2}
    assert summary['retry_counts'] == {'start': 2, 'end': 0}
    assert summary['error_codes'] == {'BRIDGE_TIMEOUT': 2}
    monkeypatch.setattr(once, 'SUMMARY_MAX_BYTES', 10)
    assert once.event_summary(raw)['truncated'] is True
    monkeypatch.undo()
    summary = once.event_summary(events())
    assert summary['assistant_stop_reasons'] == {'toolUse': 1, 'stop': 1}
    assert summary['error_codes'] == {}
    with pytest.raises(ValueError):
        once.checked_events(raw)


@pytest.mark.parametrize('code', ['PI_RETRY_POLICY_UNAVAILABLE', 'APPROVED_EXACT_CODEX_BRIDGE_REQUIRED',
                                  'PACKAGED_CODEX_BRIDGE_REQUIRED', 'DEDICATED_UNMODIFIED_PI_HOST_REQUIRED'])
def test_safe_wrapper_waiting_projection(code):
    raw = json.dumps({'status': 'waiting_auth', 'code': code, 'detail': 'SECRET_CANARY'}).encode()
    summary = once.event_summary(raw)
    assert summary['wrapper_statuses'] == {'waiting_auth': 1}
    assert summary['error_codes'] == {code: 1}
    assert 'SECRET_CANARY' not in json.dumps(summary)
    with pytest.raises(ValueError):
        once.checked_events(raw)


def test_wrapper_projection_unknown_values_are_not_copied():
    for status, code in [('SECRET_CANARY', 'SECRET_CANARY'), (['SECRET_CANARY'], {'secret': 'SECRET_CANARY'})]:
        summary = once.event_summary(json.dumps({'status': status, 'code': code}).encode())
        assert summary['wrapper_statuses'] == {'OTHER': 1}
        assert summary['error_codes'] == {'OTHER': 1}
        assert 'SECRET_CANARY' not in json.dumps(summary)


@pytest.mark.parametrize('stage', ['initialize', 'tool_scope', 'turn', 'SECRET_CANARY', ['SECRET_CANARY'], None])
def test_provider_failure_stage_projection_is_fixed_and_duplicate_observations(stage):
    message = {'role': 'assistant', 'stopReason': 'error', 'errorMessage': 'BRIDGE_NOT_READY',
               'diagnostics': [{'type': 'zhizhi.codex_cli.failure', 'timestamp': 'SECRET_CANARY',
                                'details': {'stage': stage, 'text': 'SECRET_CANARY', 'id': 'SECRET_CANARY'}},
                               {'type': 'SECRET_CANARY', 'details': {'stage': 'turn'}},
                               {'type': 'zhizhi.codex_cli', 'details': {'stage': 'turn'}},
                               ['SECRET_CANARY']]}
    rows = [{'type': 'message_end', 'message': message},
            {'type': 'message_update', 'assistantMessageEvent': {'type': 'error', 'error': message}}]
    summary = once.event_summary(b'\n'.join(json.dumps(row).encode() for row in rows))
    expected = stage if isinstance(stage, str) and stage in {'initialize', 'tool_scope', 'turn'} else 'OTHER'
    assert summary['provider_failure_stages'] == {expected: 2}
    assert 'SECRET_CANARY' not in json.dumps(summary)


def test_provider_failure_projection_rejects_wrong_shapes_and_bounds(monkeypatch):
    diagnostic = {'type': 'zhizhi.codex_cli.failure', 'details': {'stage': 'turn'}}
    for message in [None, [], {'role': 'user', 'diagnostics': [diagnostic]},
                    {'role': 'assistant', 'diagnostics': {'SECRET_CANARY': diagnostic}}]:
        raw = json.dumps({'type': 'message_update', 'assistantMessageEvent': {'type': 'error', 'error': message}}).encode()
        assert once.event_summary(raw)['provider_failure_stages'] == {}
    monkeypatch.setattr(once, 'SUMMARY_MAX_COUNT', 2)
    raw = json.dumps({'type': 'message_end', 'message': {'role': 'assistant',
                     'diagnostics': [{'type': 'zhizhi.codex_cli.failure', 'details': ['SECRET_CANARY']}] * 5}}).encode()
    summary = once.event_summary(raw)
    assert summary['provider_failure_stages'] == {'OTHER': 2}
    assert 'SECRET_CANARY' not in json.dumps(summary)


@pytest.mark.parametrize('timeout', [False, True])
def test_preflight_prompt_child_verification_and_cleanup_have_distinct_clocks(tmp_path, timeout):
    from ahadiff.knowledge_platform import pi_acceptance as pi
    clock = [0.0]
    def advance(seconds):
        clock[0] += seconds
    scope = Mock()
    scope.receipts.read.return_value = None
    host = SimpleNamespace(ROOT=tmp_path, CHILDREN=[], LOGS=[], JOB=Mock())
    host.within = lambda name: tmp_path / name
    host.load_config = Mock(return_value={})
    host.platform_command = Mock(return_value=(['platform'], 8766, 'configured'))
    host.await_health = Mock(side_effect=lambda *args: advance(400))
    host.owner_pi_environment = Mock(return_value={})
    host.write_json = Mock()
    platform = Mock()
    platform.poll.return_value = None
    host.spawn = Mock(return_value=platform)
    child = Mock(returncode=0, stdout=io.BytesIO(events()), stderr=io.BytesIO())
    child.stdin.write.side_effect = lambda data: advance(7)
    child.stdin.close.side_effect = lambda: advance(3) if clock[0] == 407 else None
    polls = [0]
    def poll():
        if polls[0] == 0:
            advance(360 if timeout else 20)
        polls[0] += 1
        return None if timeout else 0
    child.poll.side_effect = poll
    host.JOB.close.side_effect = lambda: advance(4)
    def complete(*args, **kwargs):
        advance(6)
        return {'status': 'pi_source_search_verified', 'final_delivery': False}
    with patch.object(once.time, 'monotonic', side_effect=lambda: clock[0]), \
         patch.dict('sys.modules', {'msvcrt': Mock()}), \
         patch.object(pi, 'active_scope', return_value=scope), \
         patch.object(pi, 'completion_summary', side_effect=complete), \
         patch.object(once.subprocess, 'Popen', return_value=child):
        if timeout:
            with pytest.raises(RuntimeError, match='PI_ONCE_FAILED_CLOSED'):
                once.run_once(host, {})
        else:
            once.run_once(host, {})
    report = host.write_json.call_args.args[1]
    assert once.CHILD_JOURNEY_SECONDS == 360
    assert report['preflight_ms'] == 400_000
    assert report['prompt_pipe_ms'] == 10_000
    assert report['child_elapsed_ms'] == (360_000 if timeout else 20_000)
    assert report['verification_ms'] == (0 if timeout else 6_000)
    assert report['cleanup_ms'] == 4_000
    assert report['elapsed_ms'] == (774_000 if timeout else 440_000)
    assert report['elapsed_ms'] > once.CHILD_JOURNEY_SECONDS * 1000
    assert report['process_failure'] == ('timeout' if timeout else None)
    if not timeout:
        assert report['status'] == 'pi_source_search_verified'

@pytest.mark.parametrize('code', ['OUTPUT_SCHEMA_INVALID', 'CODEX_INCOMPLETE_TURN', 'MODEL_DEADLINE_EXCEEDED', 'PI_ACCEPTANCE_SCOPE_BLOCKED'])
def test_model_failure_projection_preserves_only_fixed_vocabulary(code):
    message = {'role': 'assistant', 'stopReason': 'error', 'errorMessage': code,
               'diagnostics': [{'type': 'zhizhi.codex_cli.failure', 'details': {
                   'stage': 'turn', 'model_failure': {'stage': 'output', 'kind': 'output',
                   'summary': 'output_validation_failed', 'raw': 'PRIVATE_SENTINEL'}}}]}
    projected = once.event_summary(json.dumps({'type': 'message_end', 'message': message}).encode())
    assert projected['error_codes'] == {code: 1}
    assert projected['model_failure_stages'] == {'output': 1}
    assert projected['model_failure_kinds'] == {'output': 1}
    assert projected['model_failure_summaries'] == {'output_validation_failed': 1}
    assert 'PRIVATE_SENTINEL' not in json.dumps(projected)
    message['errorMessage'] = 'PRIVATE_SENTINEL'
    message['diagnostics'][0]['details']['model_failure'] = dict.fromkeys(['stage', 'kind', 'summary'], 'PRIVATE_SENTINEL')
    projected = once.event_summary(json.dumps({'type': 'message_end', 'message': message}).encode())
    assert projected['error_codes'] == {'OTHER': 1}
    assert all(projected[key] == {'OTHER': 1} for key in ['model_failure_stages', 'model_failure_kinds', 'model_failure_summaries'])
    assert 'PRIVATE_SENTINEL' not in json.dumps(projected)


def test_failure_vocabulary_matches_python_runner_and_typescript_protocol():
    from ahadiff.knowledge_platform.model import diagnostics
    protocol = (Path(__file__).parents[2] / 'integrations/pi-zhizhi/src/codex-bridge-protocol.ts').read_text()
    import re
    for key, values in [('stage', diagnostics.FAILURE_STAGES), ('kind', diagnostics.FAILURE_KINDS), ('summary', diagnostics.FAILURE_SUMMARIES)]:
        matched = re.search(r'!\[(.*?)\]\.includes\(value\.' + key + r'\)', protocol)
        assert matched is not None
        assert set(json.loads('[' + matched.group(1) + ']')) == values
        for value in values:
            message = {'role': 'assistant', 'stopReason': 'error', 'diagnostics': [
                {'type': 'zhizhi.codex_cli.failure', 'details': {'stage': 'turn', 'model_failure': {key: value}}}]}
            projected = once.event_summary(json.dumps({'type': 'message_end', 'message': message}).encode())
            plural = {'stage': 'stages', 'kind': 'kinds', 'summary': 'summaries'}[key]
            assert projected['model_failure_' + plural] == {value: 1}
