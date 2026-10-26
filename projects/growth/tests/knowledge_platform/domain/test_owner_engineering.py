"""Offline real-Git/SQLite fixtures with synthetic DPAPI and model adapters.

No Windows proof, external model call, credential or real activation is used.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ahadiff.growth.history import scan_history
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.knowledge_platform import owner_anchor as anchor, owner_identity as owner
from ahadiff.knowledge_platform import owner_acceptance, owner_engineering as eng
from ahadiff.knowledge_platform.engineering_scope import EngineeringScopeRunner, EngineeringScopeError
from ahadiff.knowledge_platform.model.contracts import ModelResult, canonical_hash


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout.decode().strip()


@pytest.fixture
def case(tmp_path, monkeypatch):
    approved = tmp_path / 'approved'
    old = approved / 'git-delivery/releases' / ('a' * 12 + '-18-1')
    root = approved / 'git-delivery/releases' / ('a' * 12 + '-31-1')
    for path in (old, root):
        (path / 'data').mkdir(parents=True)
        (path / 'release.json').write_text(json.dumps({'source_commit': 'a' * 40, 'release_id': path.name}))
    selected = [old]
    monkeypatch.setattr(owner, 'APPROVED_ROOT', str(approved))
    monkeypatch.setattr(owner, '_acceptance_root', lambda: selected[0])
    monkeypatch.setattr(owner, 'protect_current_user', lambda data, **kw: data)
    owner.initialize_owner_identity(8766)
    def verified(path, source, release):
        assert source == 'a' * 40 and release == path.name
        return {'source_commit': source, 'release_id': release, 'package_root': str(path),
                'distribution_sha256': 'b' * 64}
    monkeypatch.setattr(owner_acceptance, 'verified_binding', verified)
    monkeypatch.setattr(eng, 'verified_binding', verified)
    selected[0] = root
    anchor.reuse_owner_identity(approved=True, new_run=True, anchor_release=old.name)
    prior = files(old)
    repo = approved / 'source-samples/sample'
    repo.mkdir(parents=True)
    git(repo, 'init'); git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'main.py').write_text('answer = 1\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'base')
    parent = git(repo, 'rev-parse', 'HEAD')
    (repo / 'main.py').write_text('answer = 2\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'change')
    commit = git(repo, 'rev-parse', 'HEAD')
    ledger = GrowthLocalRepository(root / 'data/owner-acceptance/desktop/.ahadiff/growth.sqlite')
    project = ledger.create_project('Fixture', trace_id='fixture')
    binding = ledger.bind_repo(project, repo, trace_id='fixture')
    scan_history(ledger, binding)
    feature = ledger.start_feature(project, binding, 'change', parent, trace_id='fixture')
    snapshot = ledger.capture_snapshot(feature, binding, trace_id='fixture', commit_sha=commit)
    refs = [r[0] for r in ledger.connection.execute('SELECT source_ref_id FROM source_refs WHERE snapshot_id=?', (snapshot,))]
    spec = {'project_id': project, 'binding_id': binding, 'snapshot_id': snapshot,
            'source_ref_ids': refs, 'repository_path': str(repo), 'commit_sha': commit,
            'parent_sha': parent, 'request_id': '11111111-1111-4111-8111-111111111111', 'classification': 'personal'}
    executable = tmp_path / 'fake-codex.exe'; executable.write_text('not executable; no real model')
    home = tmp_path / 'existing-home'; home.mkdir()
    scope, payload = eng.prepare(root, spec, codex_exe=executable, codex_home=home)
    yield SimpleNamespace(root=root, old=old, approved=approved, prior=prior, repo=repo,
                          ledger=ledger, spec=spec, scope=scope, payload=payload, exe=executable, home=home)
    ledger.close()


def activate(case):
    return eng.activate(case.root, case.spec, codex_exe=case.exe, codex_home=case.home,
                        approved_scope_hash=canonical_hash(case.scope))


def execution(case, monkeypatch):
    from ahadiff.growth import model_opportunities as model
    monkeypatch.setattr(model, '_provider', lambda *a, **kw: (eng.descriptor(), None, None))
    case.ledger.set_project_policy(case.spec['project_id'], local_processing=True, model_allowed=True, cloud_allowed=False)
    aid, created = case.ledger.queue_live_opportunities(case.spec['snapshot_id'], case.spec['binding_id'],
        case.spec['request_id'], provider_name='subscription', source_ref_ids=case.spec['source_ref_ids'],
        approved_payload_hash=case.scope['approved_payload_hash'], trace_id='fixture')
    assert created
    with case.ledger.connection:
        case.ledger.connection.execute("UPDATE analysis_runs SET status='running' WHERE analysis_id=?", (aid,))
        case.ledger.connection.execute("INSERT INTO analysis_attempts VALUES ('attempt', ?, 1, 'running', NULL,NULL,'now')", (aid,))
    preview, task = eng._input(case.root, case.spec, execution=True)
    return task


def gate(case, delegate, execution=True):
    def rebuild():
        return eng.prepare(case.root, case.spec, codex_exe=case.exe, codex_home=case.home, execution=execution)[0]
    return EngineeringScopeRunner(delegate, eng._receipts(case.root), case.scope, rebuild)


def test_preview_does_not_activate_or_mutate_old_identity(case):
    assert case.scope['max_attempts'] == 1 and case.scope['output_tokens_hard_capped'] is False
    assert not (case.root / 'data/owner-acceptance' / eng.DESKTOP_NAME).exists()
    assert 'answer = 2' in case.payload
    assert files(case.old) == case.prior
    with pytest.raises(EngineeringScopeError):
        eng._receipts(case.root)


def test_activation_is_exact_and_idempotent_never_restores_budget(case):
    with pytest.raises(EngineeringScopeError):
        eng.activate(case.root, case.spec, codex_exe=case.exe, codex_home=case.home, approved_scope_hash='0' * 64)
    assert activate(case) == 'activated_not_started'
    assert activate(case) == 'already_activated_no_refund'
    assert json.loads((case.root / 'data/owner-acceptance/desktop.json').read_text())['model'] == {'enabled': False}
    assert files(case.old) == case.prior
    receipt = eng._receipts(case.root)
    receipt.put('attempt', {'fixture': 'spent'})
    activate(case)
    assert receipt.read('attempt') == {'fixture': 'spent'}


@pytest.mark.parametrize('field,value', [('project_id','other'), ('binding_id','other'), ('snapshot_id','other'),
    ('commit_sha','0'*40), ('parent_sha','0'*40), ('repository_path','/tmp'), ('classification','secret')])
def test_scope_rejects_wrong_inputs(case, field, value):
    spec = case.spec | {field: value}
    with pytest.raises(Exception):
        eng.prepare(case.root, spec, codex_exe=case.exe, codex_home=case.home)


@pytest.mark.parametrize('change', ['content','blob','capture','parent','project','cloud'])
def test_real_git_sqlite_invariants(case, change):
    sql = {
        'content': "UPDATE source_refs SET content=X'616263'",
        'blob': "UPDATE source_refs SET blob_hash='changed'",
        'capture': "UPDATE snapshots SET capture_scope='{}'",
        'parent': "UPDATE snapshots SET resolved_base_sha='changed'",
        'project': "UPDATE bindings SET project_id='changed'",
        'cloud': "UPDATE project_policies SET cloud_allowed=1",
    }[change]
    if change == 'project':
        case.ledger.connection.execute('PRAGMA foreign_keys=OFF')
    with case.ledger.connection:
        case.ledger.connection.execute(sql)
    with pytest.raises(Exception):
        eng.prepare(case.root, case.spec, codex_exe=case.exe, codex_home=case.home)


def test_delete_external_ledger_or_partial_reservation_fails_closed(case):
    activate(case)
    receipt = eng._receipts(case.root)
    shutil.rmtree(receipt.directory)
    with pytest.raises(EngineeringScopeError, match='LEDGER_MISSING'):
        activate(case)


def test_once_success_restart_and_concurrent_requests(case, monkeypatch):
    activate(case); task = execution(case, monkeypatch)
    async def answer(request):
        await asyncio.sleep(.01)
        return ModelResult(task_id=request.task_id, status='succeeded', output={'opportunities':[]},
                           provider_id='openai', input_fingerprint=request.fingerprint)
    runner = SimpleNamespace(run=AsyncMock(side_effect=answer))
    async def run():
        return await asyncio.gather(gate(case,runner).run(task), gate(case,runner).run(task))
    results = asyncio.run(run())
    assert sorted(r.status for r in results) == ['blocked','succeeded']
    assert runner.run.call_count == 1
    again = asyncio.run(gate(case,runner).run(task))
    assert again.status == 'blocked' and runner.run.call_count == 1
    assert files(case.old) == case.prior


@pytest.mark.parametrize('outcome', ['failure','cancel','invalid','fixture'])
def test_failures_never_refund(case, monkeypatch, outcome):
    activate(case); task = execution(case, monkeypatch)
    async def answer(request):
        if outcome == 'failure': raise RuntimeError('synthetic failure')
        if outcome == 'cancel': raise asyncio.CancelledError()
        return ModelResult(task_id=request.task_id, status='invalid_output' if outcome=='invalid' else 'succeeded',
                           provider_id='openai', input_fingerprint=request.fingerprint,
                           fixture=outcome=='fixture', output={'opportunities':[]})
    runner=SimpleNamespace(run=AsyncMock(side_effect=answer))
    try: asyncio.run(gate(case,runner).run(task))
    except (RuntimeError, asyncio.CancelledError): pass
    result=asyncio.run(gate(case,runner).run(task))
    assert result.status=='blocked' and runner.run.call_count==1


@pytest.mark.parametrize('mutation', ['payload','schema','snapshot','binding_context','provider','classification'])
def test_gate_rejects_wrong_task_before_consumption(case, monkeypatch, mutation):
    activate(case); task=execution(case,monkeypatch)
    changes={'payload':{'payload':{'legacy_request':'wrong'}}, 'schema':{'output_schema':{}},
        'snapshot':{'input_refs':('wrong',)}, 'binding_context':{'input_refs':(case.spec['snapshot_id'],'engineering-context:wrong')},
        'provider':{'allowed_provider_ids':('other',)}, 'classification':{'input_classification':'public'}}
    runner=SimpleNamespace(run=AsyncMock())
    assert asyncio.run(gate(case,runner).run(replace(task,**changes[mutation]))).status=='blocked'
    runner.run.assert_not_called()
    assert eng._receipts(case.root).read('attempt') is None


def test_live_request_must_match_exact_request_id_and_first_attempt(case, monkeypatch):
    activate(case); task=execution(case,monkeypatch)
    with case.ledger.connection:
        case.ledger.connection.execute("UPDATE analysis_runs SET request_id='other'")
    runner=SimpleNamespace(run=AsyncMock())
    assert asyncio.run(gate(case,runner).run(task)).status=='blocked'
    runner.run.assert_not_called()


def test_changed_payload_topics_invalidate_activation(case):
    activate(case)
    # Another model route cannot be substituted with a reviewed hash from before.
    changed = case.scope | {'model': case.scope['model'] | {'timeout_seconds': 999}}
    assert canonical_hash(changed) != canonical_hash(case.scope)
    path = eng.desktop_config_path(case.root)
    record=json.loads(path.read_text()); record['model']={'enabled':True}
    path.write_text(json.dumps(record))
    with pytest.raises(EngineeringScopeError): eng.desktop_config_path(case.root)


@pytest.mark.parametrize("invalid_schema", [False, True])
def test_normal_live_pipeline_and_bridge_use_identical_reviewed_task(case, monkeypatch, invalid_schema):
    """Real ledger pipeline and bridge; synthetic runner, not live model evidence."""
    from ahadiff.knowledge_platform import model as model_module
    from ahadiff.serve.routes_growth import _AnalysisInput
    _AnalysisInput.model_validate({'binding_id':case.spec['binding_id'],
        'request_id':case.spec['request_id'], 'mode':'live', 'provider_name':'subscription',
        'source_ref_ids':case.spec['source_ref_ids'],
        'approved_payload_hash':case.scope['approved_payload_hash'], 'approved':True})
    activate(case)
    seen=[]
    async def answer(task):
        seen.append(task)
        assert task.fingerprint==case.scope['task_fingerprint']
        assert len(task.input_refs)==2 and task.input_refs[1].startswith('engineering-context:')
        return ModelResult(task_id=task.task_id,status='succeeded',output={'opportunities':[{}] if invalid_schema else []},
            provider_id='openai',input_fingerprint=task.fingerprint,model_id=eng.MODEL)
    monkeypatch.setattr(model_module,'build_runner_from_config',lambda _:SimpleNamespace(run=answer))
    from ahadiff.knowledge_platform.desktop_config import configure_desktop, reset_desktop
    from ahadiff.knowledge_platform.learning.model_bridge import _execution_loop
    monkeypatch.setattr(owner, 'load_desktop_owner_identity', lambda *a, **k: SimpleNamespace(token='synthetic-fixture-token-only'))
    configure_desktop(eng.desktop_config_path(case.root), state_directory=case.root/'data/owner-acceptance/desktop/.ahadiff', loop=_execution_loop())
    try:
        case.ledger.set_project_policy(case.spec['project_id'],local_processing=True,model_allowed=True,cloud_allowed=False)
        from contextlib import nullcontext
        with pytest.raises(RuntimeError, match='结构要求') if invalid_schema else nullcontext():
            case.ledger.analyze_live_opportunities(case.spec['snapshot_id'],case.spec['binding_id'],case.spec['request_id'],
                provider_name='subscription',source_ref_ids=case.spec['source_ref_ids'],
                approved_payload_hash=case.scope['approved_payload_hash'],trace_id='fixture')
        record=case.ledger.connection.execute('SELECT * FROM analysis_runs WHERE request_id=?',(case.spec['request_id'],)).fetchone()
        assert record['status']==('failed' if invalid_schema else 'succeeded'),record['error']
        assert len(seen)==1
        assert eng._receipts(case.root).read('attempt')['status']=='reserved_consumed'
    finally:
        reset_desktop()


def test_context_hash_prevents_other_binding_dedupe(case):
    from ahadiff.growth.model_opportunities import opportunity_request
    from ahadiff.knowledge_platform.learning.model_bridge import subscription_task
    preview,task=eng._input(case.root,case.spec,execution=False)
    other=replace(preview,binding_id='22222222-2222-4222-8222-222222222222')
    other_task=subscription_task(opportunity_request(other,eng.descriptor()),eng.bridge_configuration('personal'))
    assert other_task.fingerprint!=task.fingerprint
    assert other_task.dedupe_key!=task.dedupe_key


def _reserve_process(root, directory, binding, start, output):
    from ahadiff.knowledge_platform.acceptance_model import ScopeReceipts
    receipt=ScopeReceipts(Path(root),Path(directory),binding,'synthetic-process-key')
    start.wait(15)
    output.put(receipt.put('attempt',{'scope':'same','consumed':True}))


def test_reservation_is_atomic_across_processes(tmp_path):
    import multiprocessing
    context=multiprocessing.get_context('spawn')
    directory=tmp_path/'receipts';directory.mkdir()
    start=context.Event();output=context.Queue()
    children=[context.Process(target=_reserve_process,args=(str(tmp_path),str(directory),{'fixture':True},start,output)) for _ in range(2)]
    for child in children: child.start()
    start.set()
    results=[output.get(timeout=30) for _ in children]
    for child in children:
        child.join(30)
        assert child.exitcode==0
    assert sorted(results)==[False,True]


def test_interrupted_receipt_write_is_not_refunded(case):
    activate(case)
    receipt=eng._receipts(case.root)
    receipt._path('attempt').write_text('{')
    with pytest.raises(Exception): receipt.put('attempt',{'consumed':True})
    # Re-activation must neither remove nor repair this fail-closed reservation.
    activate(case)
    assert receipt._path('attempt').read_text()=='{'


def test_scope_rejects_non_uuid_request_id(case):
    with pytest.raises(ValueError):
        eng.prepare(case.root,case.spec|{'request_id':'not-a-uuid'},codex_exe=case.exe,codex_home=case.home)


def test_git_alternate_object_store_is_rejected(case):
    path=case.repo/'.git/objects/info/alternates'
    path.write_text('/outside/objects\n')
    with pytest.raises(EngineeringScopeError, match='ALTERNATE_OBJECTS'):
        eng.prepare(case.root,case.spec,codex_exe=case.exe,codex_home=case.home)

def test_changed_distribution_binding_invalidates_activation(case, monkeypatch):
    activate(case)
    previous=eng.verified_binding
    monkeypatch.setattr(eng,'verified_binding',lambda *a: previous(*a) | {'distribution_sha256':'c'*64})
    with pytest.raises(EngineeringScopeError, match='STALE'):
        eng.desktop_config_path(case.root)
