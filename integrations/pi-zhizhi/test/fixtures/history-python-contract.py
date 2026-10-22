"""Synthetic-only ASGI responses from the actual domain, SQLite and Git implementation."""
import json
import tempfile
from pathlib import Path
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient
from ahadiff.knowledge_platform.auth import Actor, DomainAuthenticationMiddleware, TokenAuthenticator
from ahadiff.knowledge_platform.core_routes import create_core_routes
from ahadiff.knowledge_platform.domain import DomainService

owner=Actor('synthetic-owner','synthetic-human','user',frozenset({'*'}))
writer=Actor('synthetic-owner','synthetic-writer','service',frozenset({'*'}))
reader=Actor('synthetic-owner','synthetic-reader','agent',frozenset({'knowledge:read'}))
with tempfile.TemporaryDirectory(prefix='history-contract-') as temporary:
    domain=DomainService(Path(temporary)/'platform.db')
    try:
        intake=domain.create_intake({'text':'Synthetic architecture evidence.','data_classification':'public'},actor=owner,idempotency_key='fixture-intake')
        preview=domain.update_intake(intake['intake_id'],{'state':'preview_ready','preview':{'summary':'Synthetic'}},actor=owner,idempotency_key='fixture-preview')
        source=domain.retain_source(intake['intake_id'],{'raw_text':'Synthetic architecture evidence.','preview_revision':preview['preview_revision'],'rights':'summary_allowed'},actor=owner,idempotency_key='fixture-retain')
        for base in (0,1):
            change=domain.propose_change({'entry_id':'history-example','base_revision':base,'title':'Synthetic architecture','body':'Synthetic boundary.\n'+('Additional boundary.\n' if base else ''),'visibility':'public','evidence_refs':[{'source_id':source['source_id']}]},actor=owner,idempotency_key=f'fixture-propose-{base}')
            if change['state']!='ready':
                challenge=domain.create_decision_challenge(change['change_id'],actor=owner)
                domain.record_review(change['change_id'],{'decision':'approve','expected_patch_hash':change['patch_hash'],'base_revision':base,'decision_nonce':challenge['decision_nonce']},actor=owner,idempotency_key=f'fixture-review-{base}')
            domain.apply_change(change['change_id'],actor=writer,idempotency_key=f'fixture-apply-{base}')
        token='synthetic-in-memory-history-reader-token'
        app=Starlette(routes=create_core_routes(domain),middleware=[Middleware(DomainAuthenticationMiddleware,authenticator=TokenAuthenticator({token:reader}))])
        head=domain.writer.head()
        with TestClient(app) as client:
            responses=[]
            for path in ['/api/knowledge/v1/entries/history-example/versions?limit=20&offset=0','/api/knowledge/v1/entries/history-example/diff?from_revision=1&to_revision=2']:
                response=client.get(path,headers={'Authorization':'Bearer '+token})
                assert response.status_code==200,response.text
                responses.append({'path':path,'body':response.json(),'status':response.status_code})
        assert domain.writer.head()==head
        print(json.dumps(responses))
    finally:
        domain.close()
