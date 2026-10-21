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


@pytest.mark.parametrize('failure', [None, 'assign', 'spent', 'invalid-events', 'proof', 'service-exit', 'output-limit', 'cancel'])
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
    if failure in {'service-exit', 'output-limit'}:
        child.poll.return_value = None
    child.returncode = 0
    child.stdout = io.BytesIO(b'{}' if failure == 'invalid-events' else events())
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
    child.stdin.write.side_effect = assigned_then_write
    with patch.dict('sys.modules', {'msvcrt': Mock()}), \
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
