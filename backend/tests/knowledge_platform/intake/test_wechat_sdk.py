"""Real pinned wechatpy crypto with synthetic keys only; no account or network I/O."""

import asyncio
import base64
import time
import xml.etree.ElementTree as ET

import pytest

from ahadiff.knowledge_platform.channels import ChannelSupervisor, SenderBindings
from ahadiff.knowledge_platform.channels.wechat_kf import (
    WechatKFAdapter,
    WechatKFConfig,
    create_wechat_crypto,
)
from ahadiff.knowledge_platform.intake.contracts import IntakeError


def sdk_config(corp="fixture-corp"):
    pytest.importorskip("wechatpy", reason="Optional wechat extra is required for real SDK crypto")
    return WechatKFConfig(
        account_id="fixture-sdk",
        corp_id=corp,
        open_kfid="fixture-kf",
        enabled=True,
        api_permission_confirmed=True,
        api_managed_confirmed=True,
        callback_token="public-synthetic-test-token",
        encoding_aes_key=base64.b64encode(b"0123456789abcdef0123456789abcdef").decode().rstrip("="),
    )


def encrypted(sdk, plaintext):
    raw = sdk.encrypt_message(plaintext, "fixture-nonce", str(int(time.time())))
    root = ET.fromstring(raw)
    return (
        raw.encode(),
        {
            "signature": root.findtext("MsgSignature"),
            "timestamp": root.findtext("TimeStamp"),
            "nonce": root.findtext("Nonce"),
        },
        root.findtext("Encrypt"),
    )


def test_real_sdk_callback_roundtrip_and_idempotent_task(service, actors):
    config = sdk_config()
    sdk = create_wechat_crypto(config)
    adapter = WechatKFAdapter(service, config, object(), SenderBindings({}), state_actor=actors[1])
    raw, params, _ = encrypted(
        sdk,
        "<xml><Event>kf_msg_or_event</Event><OpenKfId>fixture-kf</OpenKfId>"
        "<Token>synthetic-short-lived-sync-token</Token></xml>",
    )
    first = adapter.receive_callback(raw, **params)
    assert first == adapter.receive_callback(raw, **params)
    task = service.domain.read_task(first["task_id"], actor=actors[1])
    assert task["kind"] == "channel.wechat_kf.sync"
    assert "synthetic-short-lived-sync-token" not in str(task)
    _, challenge_params, echo = encrypted(sdk, "fixture-url-challenge")
    assert adapter.verify_url(encrypted=echo, **challenge_params) == "fixture-url-challenge"


def test_real_sdk_rejects_wrong_corporation_after_valid_outer_signature(service, actors):
    adapter = WechatKFAdapter(
        service, sdk_config(), object(), SenderBindings({}), state_actor=actors[1]
    )
    wrong_sdk = create_wechat_crypto(sdk_config("wrong-corporation"))
    raw, params, _ = encrypted(
        wrong_sdk, "<xml><Event>kf_msg_or_event</Event><OpenKfId>fixture-kf</OpenKfId></xml>"
    )
    with pytest.raises(IntakeError) as error:
        adapter.receive_callback(raw, **params)
    assert error.value.code == "INVALID_SIGNATURE"
    assert (
        service.domain.claim_task("sdk-test", actor=actors[1], kinds=["channel.wechat_kf.sync"])
        is None
    )


def test_wechat_native_text_journey_with_real_crypto_and_fixture_api(service, actors):
    class WechatNativeAPIFixture:
        def __init__(self):
            self.messages = []
            self.sends = []
            self.cursor = 0

        async def sync_messages(self, **kwargs):
            self.cursor += 1
            return {
                "errcode": 0,
                "next_cursor": f"cursor-{self.cursor}",
                "has_more": 0,
                "msg_list": self.messages,
            }

        async def send_native(self, payload):
            self.sends.append(payload)
            assert len(payload["text"]["content"].encode()) <= 2048
            return {"errcode": 0, "msgid": payload["msgid"]}

    async def run():
        config, api = sdk_config(), WechatNativeAPIFixture()
        sdk = create_wechat_crypto(config)
        adapter = WechatKFAdapter(
            service,
            config,
            api,
            SenderBindings({("wechat_kf", "fixture-sdk", "fixture-kf", "customer"): actors[0]}),
            state_actor=actors[1],
        )
        supervisor = ChannelSupervisor(
            service.domain,
            actor=actors[1],
            wechat_adapters=[adapter],
            native_notifications_enabled=True,
        )
        outbox = supervisor.notifications

        async def deliver(text, message_id):
            api.messages = [
                {
                    "origin": 3,
                    "open_kfid": "fixture-kf",
                    "external_userid": "customer",
                    "msgid": message_id,
                    "text": {"content": text},
                }
            ]
            raw, params, _ = encrypted(
                sdk, "<xml><Event>kf_msg_or_event</Event><OpenKfId>fixture-kf</OpenKfId></xml>"
            )
            queued = adapter.receive_callback(raw, **params)
            await supervisor.process_wechat_once()
            return service.domain.read_task(queued["task_id"], actor=actors[1])

        async def drain():
            outbox.collect_events()
            while await outbox.process_once():
                pass

        received = await deliver("Synthetic native WeChat evidence.", "message-1")
        intake_id = received["result"]["receipt_ids"][0]
        await drain()
        await service.process_next(actor=actors[1])
        await drain()
        assert any(f"知枝收藏 {intake_id} 1" in item["text"]["content"] for item in api.sends)
        assert all(
            item["touser"] == "customer" and item["open_kfid"] == "fixture-kf" for item in api.sends
        )
        await deliver(f"知枝收藏 {intake_id} 1", "decision-1")
        await drain()
        assert service.domain.read_intake(intake_id, actor=actors[0])["source_id"]
        assert any("知识处理已排队" in item["text"]["content"] for item in api.sends)
        assert len({item["msgid"] for item in api.sends}) == len(api.sends)

    asyncio.run(run())
