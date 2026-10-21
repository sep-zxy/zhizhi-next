"""Real domain state, synthetic sealed-secret fault; no real credentials used."""

import asyncio

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.channel_runtime import DeferredChannelRuntime
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.intake.contracts import IntakeError
from ahadiff.knowledge_platform.secrets import SecretUnavailable


def test_sealed_secret_waits_and_recovers_without_acknowledging_callback(tmp_path):
    domain = DomainService(tmp_path / "db", tmp_path / "vault")
    owner = Actor("owner", "user", "user", frozenset({"*"}))
    attempts = 0
    async def check():
        nonlocal attempts
        ready = asyncio.Event()
        stop = asyncio.Event()
        class Supervisor:
            async def run_forever(self, event):
                ready.set()
                await event.wait()
        class Adapter:
            def verify_url(self, **params):
                return "verified-fixture"
        def factory():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise SecretUnavailable("SECRET_UNAVAILABLE")
            return Supervisor(), Adapter()
        runtime = DeferredChannelRuntime(domain, "owner", factory, retry_seconds=0.01)
        with pytest.raises(IntakeError) as failure:
            runtime.verify_url(encrypted="untrusted")
        assert failure.value.status_code == 503
        worker = asyncio.create_task(runtime.run_forever(stop))
        await asyncio.wait_for(ready.wait(), timeout=3)
        assert runtime.status == "ready"
        assert runtime.verify_url(encrypted="fixture") == "verified-fixture"
        assert domain.get_channel_state("bootstrap", actor=owner)["state"] == "ready"
        stop.set()
        await asyncio.wait_for(worker, timeout=3)
        assert runtime.adapter is None
        assert attempts == 2
    asyncio.run(check())
