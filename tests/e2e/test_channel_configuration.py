"""Official adapter assembly with fake credentials and no external calls."""

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.channel_config import build_channels
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.intake import IntakeService


def test_channel_sender_configuration_is_exact_and_owner_scoped(tmp_path):
    domain = DomainService(tmp_path / "db", tmp_path / "vault")
    intake = IntakeService(domain)
    actor = Actor("owner", "me", "user", frozenset({"*"}))
    config = {"bindings": [{"channel": "telegram", "account_id": "test-bot", "chat_id": "101",
                             "sender_id": "101", "actor_id": "me"}],
              "telegram": {"enabled": True, "account_id": "test-bot", "token_reference": "bot"}}
    supervisor, wechat = build_channels(config, domain=domain, intake=intake, owner="owner",
                                       identities=[actor], resolve_secret=lambda _: "123:fixture")
    assert wechat is None
    poller = supervisor.telegram_pollers[0]
    assert poller.bindings.resolve("telegram", "test-bot", "101", "101") is actor
    assert poller.bindings.resolve("telegram", "test-bot", "102", "101") is None
    with pytest.raises(ValueError, match="owner identity"):
        build_channels(config, domain=domain, intake=intake, owner="other", identities=[actor],
                       resolve_secret=lambda _: "123:fixture")


def test_disabled_channels_do_not_read_secrets(tmp_path):
    domain = DomainService(tmp_path / "db", tmp_path / "vault")
    def no_secret(_):
        pytest.fail("Disabled channels may not fetch credentials")
    supervisor, adapter = build_channels({}, domain=domain, intake=IntakeService(domain),
                                         owner="owner", identities=[], resolve_secret=no_secret)
    assert supervisor.telegram_pollers == ()
    assert adapter is None
