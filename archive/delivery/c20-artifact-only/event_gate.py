"""Read-only gate for the single approved push transition; no dispatch/API writes."""
import json
import os
from pathlib import Path
import re
import subprocess

BASE = '8986f8686ee0ae388fcb85ce6e45ad7eace19bbd'
BASE_TREE = '6d5a584684f05c9e6ad2b2b02a75ac56a85d5c4f'
NONCE = 'c20-diagnostic-once-20261028-8986f868-c9a42e71'
MARKER = 'deploy/c20-artifact-only/once-8986f868-c9a42e71.json'
CHANGES = frozenset({'.github/workflows/c20-artifact-only.yml', MARKER,
    'deploy/c20-artifact-only/event_gate.py', 'deploy/c20-artifact-only/acceptance.py',
    'deploy/c20-artifact-only/bounded_command.py', 'deploy/c20-artifact-only/test_bounded_command.py',
    'deploy/c20-artifact-only/prepare_and_run.py', 'deploy/c20-artifact-only/test_prepare_and_run.py',
    'deploy/c20-artifact-only/README.md'})
MARKER_VALUE = {'base_commit': BASE, 'base_tree': BASE_TREE, 'nonce': NONCE,
                'scope': 'one-approved-docker-synthetic-acceptance-no-deployment'}


def validate_event(env, event):
    if env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch':
        return
    if not (env.get('GITHUB_EVENT_NAME') == 'push'
            and env.get('GITHUB_RUN_ATTEMPT') == '1'
            and env.get('GITHUB_REF') == 'refs/heads/main'
            and event.get('ref') == 'refs/heads/main'
            and event.get('before') == BASE
            and event.get('after') == env.get('GITHUB_SHA')
            and re.fullmatch('[0-9a-f]{40}', env.get('GITHUB_SHA', ''))
            and event.get('repository', {}).get('full_name') == 'sep-zxy/zhizhi-platform'
            and event.get('forced') is False and event.get('created') is False
            and event.get('deleted') is False):
        raise ValueError('Unapproved push transition or replay')


def require_event():
    name = os.environ.get('GITHUB_EVENT_NAME')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()) if name == 'push' else {}
    validate_event(os.environ, event)


def bind(repo):
    require_event()
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()
    source = os.environ['GITHUB_SHA']
    if git('rev-parse', 'HEAD') != source or git('status', '--porcelain'):
        raise ValueError('Checkout is not clean bound source')
    if os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch':
        if os.environ.get('EXPECTED_SOURCE') != source:
            raise ValueError('Manual expected source mismatch')
        return
    if git('show', '-s', '--format=%P', 'HEAD') != BASE or git('rev-parse', 'HEAD^1^{tree}') != BASE_TREE:
        raise ValueError('Push parent or baseline tree mismatch')
    if set(git('diff', '--name-only', BASE, 'HEAD').splitlines()) != CHANGES:
        raise ValueError('Push changed paths outside reviewed increment')
    marker = Path(repo) / MARKER
    if marker.is_symlink() or json.loads(marker.read_text()) != MARKER_VALUE:
        raise ValueError('Push marker mismatch')


if __name__ == '__main__':
    bind(Path(os.environ['GITHUB_WORKSPACE']))
