"""Actual isolated intake ASGI/CAS receipts for synthetic owner text, no model or network."""
import json
import tempfile
from pathlib import Path
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient
from ahadiff.knowledge_platform.auth import Actor, DomainAuthenticationMiddleware, TokenAuthenticator
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.intake.routes import create_intake_routes
text='Synthetic owner supplement about architecture boundaries.'
owner=Actor('synthetic-owner','synthetic-human','user',frozenset({'knowledge:read','knowledge:write'}))
with tempfile.TemporaryDirectory(prefix='material-contract-') as temporary:
    domain=DomainService(Path(temporary)/'platform.db')
    try:
        intake=domain.create_intake({'text':'Synthetic original material.'},actor=owner,idempotency_key='synthetic-create')
        token='synthetic-owner-intake-token'
        app=Starlette(routes=create_intake_routes(domain),middleware=[Middleware(DomainAuthenticationMiddleware,authenticator=TokenAuthenticator({token:owner}))])
        with TestClient(app) as client:
            path='/api/knowledge/v1/intakes/'+intake['intake_id'];headers={'Authorization':'Bearer '+token}
            saved=client.get(path,headers=headers);assert saved.status_code==200,saved.text
            payload={'expected_preview_revision':saved.json()['data']['preview_revision'],'text':text}
            headers['Idempotency-Key']='synthetic-supplement-key'
            changed=client.post(path+'/supplement',json=payload,headers=headers);assert changed.status_code==200,changed.text
            again=client.post(path+'/supplement',json=payload,headers=headers);assert again.status_code==200,again.text
            assert changed.json()['data']==again.json()['data']
        print(json.dumps({'id':intake['intake_id'],'text':text,'payload':payload,'saved':saved.json(),'changed':changed.json()}))
    finally:
        domain.close()
