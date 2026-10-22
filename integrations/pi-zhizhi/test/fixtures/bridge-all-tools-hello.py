"""Full frozen-schema hello only. No identity credentials, model calls or tool execution."""
import asyncio
import json
import sys
from pathlib import Path
from ahadiff.knowledge_platform.model import pi_bridge
from ahadiff.knowledge_platform.model.pi_bridge_stdio import serve
class NoRunner:
    async def run(self, task):
        raise AssertionError('This fixture must never execute a model turn')
tools=json.loads(Path(pi_bridge.__file__).with_name('pi_tools_v1.json').read_text())['tools']
config=pi_bridge.PiBridgeConfig(owner_id='synthetic-owner',device_id='synthetic-device',deployment_id='synthetic-deployment',allowed_tools=tuple(t['name'] for t in tools),input_classification='public',allowed_classifications=('public',),enabled=True,allow_fixture=True)
asyncio.run(serve(pi_bridge.PiBridge(NoRunner(),config,authorize=lambda request:False,reserve_attempt=lambda task:False),sys.stdin.buffer,sys.stdout))
