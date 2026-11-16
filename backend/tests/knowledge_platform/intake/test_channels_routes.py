"""Official-channel protocol fixtures and real in-process HTTP/DomainService tests."""

import asyncio
import hashlib
import time

import httpx
import pytest
from starlette.applications import Starlette

from ahadiff.knowledge_platform.channels import SenderBindings, TelegramLongPoller
from ahadiff.knowledge_platform.channels.wechat_kf import WechatKFAdapter, WechatKFConfig
from ahadiff.knowledge_platform.intake import create_intake_routes
from ahadiff.knowledge_platform.intake.contracts import IntakeError


class TelegramFixture:
    def __init__(self, updates, webhook_url=""):
        self.updates = updates
        self.calls = []
        self.webhook_url = webhook_url

    async def call(self, method, payload):
        self.calls.append((method, payload))
        return {
            "ok": True,
            "result": {"url": self.webhook_url} if method == "getWebhookInfo" else self.updates,
        }


def telegram_message(update_id, user_id=42, chat_id=42):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "is_bot": False},
            "chat": {"id": chat_id, "type": "private"},
            "text": "Synthetic channel evidence.",
        },
    }


def test_telegram_replay_persists_offset_and_stable_receipt(service, actors):
    api = TelegramFixture([telegram_message(10)])
    bindings = SenderBindings({("telegram", "fixture-bot", "42", "42"): actors[0]})
    poller = TelegramLongPoller(
        service, api, bindings, account_id="fixture-bot", state_actor=actors[1]
    )
    first = asyncio.run(poller.poll_once())
    restarted = TelegramLongPoller(
        service, api, bindings, account_id="fixture-bot", state_actor=actors[1]
    )
    second = asyncio.run(restarted.poll_once())
    assert len(first) == 1 and second == []
    assert service.domain.get_channel_state("telegram:fixture-bot", actor=actors[1])["offset"] == 11
    assert api.calls[-1][1]["offset"] == 11


def test_telegram_requires_exact_chat_and_sender_binding(service, actors):
    api = TelegramFixture([telegram_message(10, chat_id=1000), telegram_message(11, user_id=999)])
    bindings = SenderBindings({("telegram", "fixture-bot", "42", "42"): actors[0]})
    poller = TelegramLongPoller(
        service, api, bindings, account_id="fixture-bot", state_actor=actors[1]
    )
    assert asyncio.run(poller.poll_once()) == []
    assert service.domain.claim_task("worker", actor=actors[1], kinds=["intake.preview"]) is None


def test_telegram_does_not_advance_after_failed_receipt(service, actors, monkeypatch):
    api = TelegramFixture([telegram_message(10)])
    bindings = SenderBindings({("telegram", "fixture-bot", "42", "42"): actors[0]})
    poller = TelegramLongPoller(
        service, api, bindings, account_id="fixture-bot", state_actor=actors[1]
    )

    def fail(*args, **kwargs):
        raise RuntimeError("simulated durable-storage failure")

    monkeypatch.setattr(service, "receive", fail)
    with pytest.raises(RuntimeError):
        asyncio.run(poller.poll_once())
    assert service.domain.get_channel_state("telegram:fixture-bot", actor=actors[1]) == {}


def test_telegram_does_not_delete_conflicting_webhook(service, actors):
    api = TelegramFixture([], webhook_url="https://already-configured.example/hook")
    poller = TelegramLongPoller(
        service, api, SenderBindings({}), account_id="fixture-bot", state_actor=actors[1]
    )
    with pytest.raises(IntakeError) as error:
        asyncio.run(poller.check_ready())
    assert error.value.code == "CHANNEL_CONFLICT"
    assert [method for method, _ in api.calls] == ["getWebhookInfo"]


class CryptoFixture:
    """No real decryption: outer signature and binding behavior are tested separately."""

    def check_signature(self, signature, timestamp, nonce, echo_str):
        return "fixture-challenge"

    def decrypt_message(self, msg, signature, timestamp, nonce):
        return (
            "<xml><Event>kf_msg_or_event</Event><OpenKfId>kf-fixture</OpenKfId>"
            "<Token>fixture-transient-token</Token></xml>"
        )


class WechatAPIFixture:
    async def sync_messages(self, *, open_kfid, cursor, token):
        return {
            "errcode": 0,
            "next_cursor": "fixture-next",
            "has_more": 0,
            "msg_list": [
                {
                    "msgid": "fixture-message",
                    "origin": 3,
                    "open_kfid": "kf-fixture",
                    "external_userid": "fixture-customer",
                    "msgtype": "text",
                    "text": {"content": "Synthetic WeChat source."},
                }
            ],
        }


def wechat_adapter(service, actors):
    config = WechatKFConfig(
        account_id="fixture-corp",
        corp_id="fixture-corp",
        open_kfid="kf-fixture",
        enabled=True,
        api_permission_confirmed=True,
        api_managed_confirmed=True,
        callback_token="synthetic-callback-token",
        encoding_aes_key="fixture-not-a-real-key",
    )
    bindings = SenderBindings(
        {("wechat_kf", "fixture-corp", "kf-fixture", "fixture-customer"): actors[0]}
    )
    return WechatKFAdapter(
        service, config, WechatAPIFixture(), bindings, state_actor=actors[1], crypto=CryptoFixture()
    )


def callback_fixture(adapter, timestamp=None):
    timestamp = timestamp or str(int(time.time()))
    nonce, encrypted = "fixture-nonce", "fixture-encrypted-payload"
    signature = hashlib.sha1(
        "".join(sorted((adapter.config.callback_token, timestamp, nonce, encrypted))).encode()
    ).hexdigest()
    raw = f"<xml><Encrypt>{encrypted}</Encrypt></xml>".encode()
    return raw, {"signature": signature, "timestamp": timestamp, "nonce": nonce}


def test_wechat_unconfigured_is_not_a_personal_bot(service, actors):
    with pytest.raises(IntakeError) as error:
        WechatKFAdapter(
            service,
            WechatKFConfig("fixture", "fixture", "fixture"),
            WechatAPIFixture(),
            SenderBindings({}),
            state_actor=actors[1],
            crypto=CryptoFixture(),
        )
    assert error.value.code == "CHANNEL_NOT_CONFIGURED"


def test_wechat_signature_replay_and_sender_mapping(service, actors):
    adapter = wechat_adapter(service, actors)
    raw, params = callback_fixture(adapter)
    receipt = adapter.receive_callback(raw, **params)
    again = adapter.receive_callback(raw, **params)
    assert receipt["task_id"] == again["task_id"]
    task = service.domain.claim_task("kf-worker", actor=actors[1], kinds=["channel.wechat_kf.sync"])
    assert "Token" not in str(task["payload"])
    result = asyncio.run(adapter.process_claimed(task))
    assert result["state"] == "succeeded"
    assert len(result["result"]["receipt_ids"]) == 1
    assert (
        service.domain.get_channel_state(adapter.channel_key, actor=actors[1])["cursor"]
        == "fixture-next"
    )


def test_wechat_bad_signature_and_stale_callback_fail_closed(service, actors):
    adapter = wechat_adapter(service, actors)
    raw, params = callback_fixture(adapter)
    with pytest.raises(IntakeError) as error:
        adapter.receive_callback(raw, **{**params, "signature": "0" * 40})
    assert error.value.code == "INVALID_SIGNATURE"
    raw, params = callback_fixture(adapter, str(int(time.time()) - 1000))
    with pytest.raises(IntakeError):
        adapter.receive_callback(raw, **params)
    assert (
        service.domain.claim_task("worker", actor=actors[1], kinds=["channel.wechat_kf.sync"])
        is None
    )


def test_http_receipt_preview_and_explicit_keep(service, actors):
    app = Starlette(routes=create_intake_routes(service.domain))

    async def authorized(scope, receive, send):
        scope.setdefault("state", {})["actor"] = actors[0]
        await app(scope, receive, send)

    async def journey():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=authorized), base_url="http://fixture"
        ) as client:
            received = await client.post(
                "/api/knowledge/v1/intakes",
                json={"text": "HTTP fixture evidence."},
                headers={"Idempotency-Key": "http-receive"},
            )
            assert received.status_code == 202
            receipt = received.json()["data"]
            assert receipt["publication"]["publication_intent"] == "public"
            assert receipt["publication"]["publication_eligible"] is False
            await service.process_next(actor=actors[1])
            preview = await client.get("/api/knowledge/v1/intakes/" + receipt["intake_id"])
            assert preview.status_code == 200
            assert "cache_ref" not in preview.json()["data"]["preview"]
            assert preview.json()["data"]["publication"]["reason_codes"]
            kept = await client.post(
                "/api/knowledge/v1/intakes/" + receipt["intake_id"] + "/decision",
                json={"decision": "keep", "preview_revision": 1},
                headers={"Idempotency-Key": "http-keep"},
            )
            assert kept.status_code == 200
            assert kept.json()["data"]["state"] == "retained"
            assert kept.json()["task_id"]
            assert "object_path" not in kept.text
            assert kept.json()["data"]["publication"]["publication_intent"] == "public"
            assert kept.json()["data"]["publication"]["raw_rights"] == "unknown"

    asyncio.run(journey())


def test_http_requires_authenticated_context(service):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=Starlette(routes=create_intake_routes(service.domain))
            ),
            base_url="http://fixture",
        ) as client:
            response = await client.post(
                "/api/knowledge/v1/intakes",
                json={"text": "x", "owner_id": "owner"},
                headers={"Idempotency-Key": "spoof"},
            )
            assert response.status_code == 401

    asyncio.run(request())
