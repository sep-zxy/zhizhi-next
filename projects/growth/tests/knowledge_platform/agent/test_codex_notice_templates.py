"""Diagnostic matches do not authorize unverified versions or mutated templates."""
import json
from pathlib import Path

import pytest

from ahadiff.knowledge_platform.model._codex_notice_templates import (
    EXACT_NOTIFICATION_TEMPLATES,
    OFFICIAL_NOTICE_COMMIT,
)
from ahadiff.knowledge_platform.model.codex_runner import _ExecEvents
from ahadiff.knowledge_platform.model.diagnostics import notification_diagnostic
from ahadiff.knowledge_platform.model.pi_runner import RpcFailure


def complete_then_reject(events):
    for item in [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "isolated"}},
        {"type": "turn.completed", "usage": {"output_tokens": 1}},
    ]:
        events.accept(json.dumps(item).encode())
    with pytest.raises(RpcFailure, match="CODEX_NOTIFICATION_REQUIRES_REVIEW"):
        events.finish(0)
    assert events.provider_turn_state == "completed"


def test_exact_templates_match_reviewed_official_source_record():
    repo = Path(__file__).resolve().parents[5]
    path = repo / 'deploy/windows-portable/codex-notification-review.json'
    record = json.loads(path.read_text())
    assert record['commit'] == OFFICIAL_NOTICE_COMMIT == '5425d3e625daa58eea3ad1de27c087eb3b2ffbd3'
    assert len(record['exact_candidates']) == 7
    assert {r['text']: r['id'] for r in record['exact_candidates']} == EXACT_NOTIFICATION_TEMPLATES
    for item in record['exact_candidates']:
        assert len(item['text'].encode()) == item['utf8_bytes']
        assert len(item['sha']) == 40
        assert item['url'] == (
            'https://github.com/openai/codex/blob/' + OFFICIAL_NOTICE_COMMIT
            + '/' + item['path'] + '#L' + str(item['line'])
        )


@pytest.mark.parametrize(('message', 'code'), EXACT_NOTIFICATION_TEMPLATES.items())
def test_exact_public_template_remains_blocked_without_text_or_actual_model(message, code):
    diagnostic = notification_diagnostic(message)
    assert diagnostic['classifier_version'] == 2
    assert diagnostic['rule_id'] == 'codex_exec_notice_v2:' + code
    assert diagnostic['evidence'] == 'exact_public_template_text'
    assert diagnostic['upstream_kind'] == 'not_preserved_by_exec_jsonl'
    assert diagnostic['input_utf8_bytes'] == len(message.encode())
    assert message not in json.dumps(diagnostic)
    events = _ExecEvents(20, notification_details=True)
    events.accept(json.dumps({'type': 'item.completed', 'item': {
        'type': 'error', 'message': message,
    }}).encode())
    assert events.completed is False and events.text is None
    assert events.notification == diagnostic
    complete_then_reject(events)


@pytest.mark.parametrize('change', [
    lambda text: text.lower(), lambda text: text.upper(),
    lambda text: text + ' ', lambda text: ' ' + text,
    lambda text: 'never-log-account ' + text,
    lambda text: text + ' token=never-log-secret',
    lambda text: text + '\n' + text,
    lambda text: text + 'x' * 8192,
])
@pytest.mark.parametrize('message', EXACT_NOTIFICATION_TEMPLATES)
def test_mutations_and_multiple_notices_cannot_claim_exact_template(message, change):
    changed = change(message)
    assert changed != message
    diagnostic = notification_diagnostic(changed)
    assert diagnostic['evidence'] != 'exact_public_template_text'
    assert diagnostic['code'] not in EXACT_NOTIFICATION_TEMPLATES.values()
    assert 'never-log' not in json.dumps(diagnostic)
    events = _ExecEvents(20, notification_details=True)
    events.accept(json.dumps({'type': 'item.completed', 'item': {
        'type': 'error', 'message': changed,
    }}).encode())
    assert events.completed is False and events.text is None
    complete_then_reject(events)


def test_byte_count_never_classifies_unknown_notice():
    diagnostic = notification_diagnostic('x' * 144)
    assert diagnostic['code'] == 'unknown_notification'
    assert diagnostic['truncated'] is False
    assert diagnostic['evidence'] == 'bounded_text_pattern_hint'
