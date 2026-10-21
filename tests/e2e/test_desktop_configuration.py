"""Configuration integration, with ephemeral test credentials and no model login."""

import asyncio
import json

import pytest

from ahadiff.knowledge_platform.desktop_config import configure_desktop, reset_desktop
from ahadiff.knowledge_platform.desktop_config import replay_desktop_outbox
from ahadiff.knowledge_platform.learning.desktop import desktop_backend
from ahadiff.knowledge_platform.learning.model_bridge import unified_mode_enabled


@pytest.fixture
def configuration(tmp_path):
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"token": "test-only-not-a-real-token-00000001"}))
    identity.chmod(0o600)
    config = tmp_path / "desktop.json"
    config.write_text(json.dumps({"service_url": "http://127.0.0.1:8766",
                                  "identity_file": "identity.json", "device_id": "test-device",
                                  "providers": [], "model": {"enabled": False}}))
    yield tmp_path, config, identity
    reset_desktop()


def test_desktop_explicit_wiring_and_token_rotation(configuration):
    state, config, identity = configuration
    async def check():
        backend = configure_desktop(config, state_directory=state,
                                    loop=asyncio.get_running_loop())
        assert desktop_backend() is backend
        assert unified_mode_enabled()
        identity.write_text(json.dumps({"token": "test-only-not-a-real-token-00000002"}))
        assert backend.token_provider().endswith("00000002")
        assert not list((state / "knowledge-outbox").glob("*.json"))
    asyncio.run(check())


def test_desktop_rejects_publicly_readable_credentials(configuration):
    state, config, identity = configuration
    identity.chmod(0o644)
    async def check():
        with pytest.raises(ValueError, match="group or other"):
            configure_desktop(config, state_directory=state, loop=asyncio.get_running_loop())
    asyncio.run(check())


def test_desktop_rejects_remote_cleartext(configuration):
    state, config, _ = configuration
    value = json.loads(config.read_text())
    value["service_url"] = "http://example.com"
    config.write_text(json.dumps(value))
    async def check():
        with pytest.raises(ValueError, match="HTTPS"):
            configure_desktop(config, state_directory=state, loop=asyncio.get_running_loop())
    asyncio.run(check())


def test_desktop_replays_without_another_user_click():
    class Backend:
        calls = 0
        async def flush(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated transient transport failure")
            stop.set()
    async def check():
        nonlocal stop
        stop = asyncio.Event()
        backend = Backend()
        await asyncio.wait_for(replay_desktop_outbox(backend, stop, interval=0.01), timeout=2)
        assert backend.calls == 2
    stop = None
    asyncio.run(check())
