"""Runnable lifecycle integration with real queue state and explicit API fixtures."""

import asyncio
from datetime import UTC, datetime

import pytest

from ahadiff.knowledge_platform.channels import (
    ChannelSupervisor,
    SenderBindings,
    TelegramLongPoller,
)
from ahadiff.knowledge_platform.channels.wechat_kf import WechatKFAdapter, WechatKFConfig
from ahadiff.knowledge_platform.intake import create_intake_routes
from ahadiff.knowledge_platform.intake.contracts import IntakeError


def test_telegram_lifecycle_recovers_and_stops_without_sending(service, actors):
    async def run():
        stop = asyncio.Event()

        class TelegramAPIFixture:
            failures = 0
            methods = []

            async def call(self, method, payload):
                self.methods.append(method)
                if method == "getWebhookInfo":
                    return {"ok": True, "result": {"url": ""}}
                if self.failures == 0:
                    self.failures += 1
                    raise RuntimeError("synthetic-secret-must-not-persist")
                stop.set()
                return {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 17,
                            "message": {
                                "from": {"id": 42},
                                "chat": {"id": 42},
                                "text": "Synthetic received source.",
                            },
                        }
                    ],
                }

        api = TelegramAPIFixture()
        poller = TelegramLongPoller(
            service,
            api,
            SenderBindings({("telegram", "test", "42", "42"): actors[0]}),
            account_id="test",
            state_actor=actors[1],
        )
        supervisor = ChannelSupervisor(
            service.domain,
            actor=actors[1],
            telegram_pollers=[poller],
            poll_seconds=0.001,
            retry_initial_seconds=0.001,
        )
        await asyncio.wait_for(supervisor.run_forever(stop), timeout=2)
        assert set(api.methods) == {"getUpdates", "getWebhookInfo"}
        assert api.methods.count("getWebhookInfo") == 2
        assert supervisor.health["telegram:test"] == {"state": "ready", "error_code": None}
        assert service.domain.get_channel_state("telegram:test", actor=actors[1])["offset"] == 18
        with service.domain.store.reading() as connection:
            assert all(
                "synthetic-secret" not in row["data"]
                for row in connection.execute("SELECT data FROM kp_records")
            )

    asyncio.run(run())


def test_stop_cancels_inflight_long_poll(service, actors):
    async def run():
        stop, entered, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class SleepingAPIFixture:
            async def call(self, method, payload):
                if method == "getWebhookInfo":
                    return {"ok": True, "result": {"url": ""}}
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        poller = TelegramLongPoller(
            service,
            SleepingAPIFixture(),
            SenderBindings({}),
            account_id="test",
            state_actor=actors[1],
        )
        supervisor = ChannelSupervisor(service.domain, actor=actors[1], telegram_pollers=[poller])
        running = asyncio.create_task(supervisor.run_forever(stop))
        await asyncio.wait_for(entered.wait(), timeout=2)
        stop.set()
        await asyncio.wait_for(running, timeout=2)
        assert cancelled.is_set()

    asyncio.run(run())


class CryptoNotUsedFixture:
    """Explicit placeholder: these tests exercise sync lifecycle, not SDK crypto."""

    def check_signature(self, *args):
        raise AssertionError("No callback crypto is invoked in this fixture")

    def decrypt_message(self, *args):
        raise AssertionError("No callback crypto is invoked in this fixture")


def make_wechat(service, actors, api):
    return WechatKFAdapter(
        service,
        WechatKFConfig(
            account_id="test",
            corp_id="corp",
            open_kfid="kf",
            enabled=True,
            api_permission_confirmed=True,
            api_managed_confirmed=True,
            callback_token="fixture-token",
            encoding_aes_key="fixture-not-real-key",
        ),
        api,
        SenderBindings({("wechat_kf", "test", "kf", "sender"): actors[0]}),
        state_actor=actors[1],
        crypto=CryptoNotUsedFixture(),
    )


def queue_sync(service, actors, account="test"):
    return service.domain.create_task(
        "channel.wechat_kf.sync",
        {"account_id": account, "open_kfid": "kf"},
        actor=actors[1],
        idempotency_key="sync:" + account,
    )


def test_wechat_supervisor_claims_durable_sync_after_restart(service, actors):
    class WechatAPIFixture:
        async def sync_messages(self, *, open_kfid, cursor, token):
            assert token is None
            return {
                "errcode": 0,
                "next_cursor": "next",
                "has_more": 0,
                "msg_list": [
                    {
                        "origin": 3,
                        "open_kfid": "kf",
                        "external_userid": "sender",
                        "msgid": "id-1",
                        "text": {"content": "Synthetic WeChat source."},
                    }
                ],
            }

    task = queue_sync(service, actors)
    supervisor = ChannelSupervisor(
        service.domain,
        actor=actors[1],
        wechat_adapters=[make_wechat(service, actors, WechatAPIFixture())],
    )
    assert asyncio.run(supervisor.process_wechat_once()) is True
    result = service.domain.read_task(task["task_id"], actor=actors[1])
    assert result["state"] == "succeeded"
    assert len(result["result"]["receipt_ids"]) == 1
    assert (
        service.domain.get_channel_state("wechat_kf:test:kf", actor=actors[1])["cursor"] == "next"
    )
    assert asyncio.run(supervisor.process_wechat_once()) is False


@pytest.mark.parametrize(
    "code,state", [("RATE_LIMITED", "retry_wait"), ("NEEDS_AUTH", "waiting_auth")]
)
def test_wechat_waits_are_durable_and_sanitized(service, actors, code, state):
    class ErrorAPIFixture:
        async def sync_messages(self, **kwargs):
            raise IntakeError(code, "synthetic-secret-response", retryable=True)

    task = queue_sync(service, actors)
    supervisor = ChannelSupervisor(
        service.domain,
        actor=actors[1],
        wechat_adapters=[make_wechat(service, actors, ErrorAPIFixture())],
    )
    assert asyncio.run(supervisor.process_wechat_once()) is True
    result = service.domain.read_task(task["task_id"], actor=actors[1])
    assert result["state"] == state
    assert result["error"] == {"code": code}
    assert "synthetic-secret" not in str(result)
    if state == "retry_wait":
        assert datetime.fromisoformat(result["next_run_at"]) > datetime.now(UTC)
    assert asyncio.run(supervisor.process_wechat_once()) is False


def test_unknown_wechat_account_is_not_sent_to_another_adapter(service, actors):
    class ForbiddenAPIFixture:
        async def sync_messages(self, **kwargs):
            raise AssertionError("No request may be sent for an unknown account")

    task = queue_sync(service, actors, account="unconfigured")
    supervisor = ChannelSupervisor(
        service.domain,
        actor=actors[1],
        wechat_adapters=[make_wechat(service, actors, ForbiddenAPIFixture())],
    )
    asyncio.run(supervisor.process_wechat_once())
    result = service.domain.read_task(task["task_id"], actor=actors[1])
    assert result["state"] == "waiting_auth"
    assert result["error"]["code"] == "CHANNEL_NOT_CONFIGURED"


def test_http_factory_accepts_shared_intake_instance(service):
    routes = create_intake_routes(service.domain, intake_service=service)
    assert {(route.path, tuple(sorted(route.methods))) for route in routes} == {
        ("/api/knowledge/v1/intakes", ("GET", "HEAD")),
        ("/api/knowledge/v1/intakes", ("POST",)),
        ("/api/knowledge/v1/intakes/capabilities", ("GET", "HEAD")),
        ("/api/knowledge/v1/intakes/{intake_id}", ("GET", "HEAD")),
        ("/api/knowledge/v1/intakes/{intake_id}/decision", ("POST",)),
    }
    with pytest.raises(ValueError):
        create_intake_routes(object(), intake_service=service)
