"""Native channel journeys use real SQLite/outbox and explicit API fixtures only."""

import asyncio

from ahadiff.knowledge_platform.channels import (
    ChannelSupervisor,
    SenderBindings,
    TelegramLongPoller,
)
from ahadiff.knowledge_platform.intake.contracts import IntakeError


class TelegramNativeFixture:
    def __init__(self):
        self.updates = []
        self.sends = []
        self.failure = None

    async def call(self, method, payload):
        return {"ok": True, "result": {"url": ""} if method == "getWebhookInfo" else self.updates}

    async def send_native(self, method, payload):
        self.sends.append((method, payload))
        if self.failure:
            raise self.failure
        return {
            "ok": True,
            "result": True if method == "answerCallbackQuery" else {"message_id": len(self.sends)},
        }


def telegram_setup(service, actors, *, enabled=True):
    api = TelegramNativeFixture()
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
        native_notifications_enabled=enabled,
    )
    return api, poller, supervisor.notifications


def message(update_id=1, *, sender=42, chat=42, chat_type="private", text="Synthetic evidence."):
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": sender},
            "chat": {"id": chat, "type": chat_type},
            "text": text,
        },
    }


def callback(data, update_id=2, *, sender=42, chat=42, query_id="cb-test"):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": query_id,
            "from": {"id": sender},
            "data": data,
            "message": {"message_id": 2, "chat": {"id": chat, "type": "private"}},
        },
    }


async def drain(dispatcher):
    dispatcher.collect_events()
    for _ in range(30):
        if not await dispatcher.process_once():
            return
    raise AssertionError("Fixture notification queue failed to settle")


def test_telegram_receipt_preview_keep_and_notice_are_native_and_idempotent(service, actors):
    async def run():
        api, poller, outbox = telegram_setup(service, actors)
        api.updates = [message()]
        receipt = (await poller.poll_once())[0]
        await drain(outbox)
        assert "尚未收藏" in api.sends[0][1]["text"]
        assert "公开意图：公开" in api.sends[0][1]["text"]
        assert "当前资格：受阻" in api.sends[0][1]["text"]
        assert (
            service.domain.read_intake(receipt["intake_id"], actor=actors[0])["source_id"] is None
        )
        await service.process_next(actor=actors[1])
        await drain(outbox)
        preview = api.sends[-1][1]
        assert "Synthetic evidence." in preview["text"]
        assert preview["chat_id"] == "42"
        assert preview["protect_content"] is True
        assert "parse_mode" not in preview
        data = preview["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        assert len(data.encode()) <= 64
        api.updates = [callback(data)]
        assert (await poller.poll_once())[0]["decision_accepted"] is True
        await drain(outbox)
        kept = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
        assert kept["source_id"]
        assert (
            service.domain.read_task(kept["task_id"], actor=actors[0])["kind"]
            == "knowledge.generate"
        )
        assert any("知识处理已排队" in payload["text"] for _, payload in api.sends)
        assert any("来源权利未核实" in payload["text"] for _, payload in api.sends)
        sent_count = len(api.sends)
        assert await poller.poll_once() == []
        await drain(outbox)
        assert len(api.sends) == sent_count

    asyncio.run(run())


def test_telegram_discard_button_clears_cache_without_retaining(service, actors):
    async def run():
        api, poller, outbox = telegram_setup(service, actors)
        api.updates = [message()]
        receipt = (await poller.poll_once())[0]
        await service.process_next(actor=actors[1])
        await drain(outbox)
        data = api.sends[-1][1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
        api.updates = [callback(data)]
        await poller.poll_once()
        await drain(outbox)
        record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
        assert record["state"] == "discarded" and record["source_id"] is None
        assert not list(service.cache.root.rglob("*.json"))
        assert any("已丢弃" in payload["text"] for _, payload in api.sends)

    asyncio.run(run())


def test_callbacks_cannot_keep_for_another_sender_or_stale_revision(service, actors):
    async def run():
        api, poller, outbox = telegram_setup(service, actors)
        api.updates = [message()]
        receipt = (await poller.poll_once())[0]
        await service.process_next(actor=actors[1])
        await drain(outbox)
        data = api.sends[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        api.updates = [callback(data, sender=99, chat=99)]
        assert await poller.poll_once() == []
        api.updates = [callback(data.rsplit(":", 1)[0] + ":99", update_id=3, query_id="stale")]
        assert (await poller.poll_once())[0]["decision_accepted"] is False
        await drain(outbox)
        assert (
            service.domain.read_intake(receipt["intake_id"], actor=actors[0])["source_id"] is None
        )
        assert any("未生效" in payload["text"] for _, payload in api.sends)

    asyncio.run(run())


def test_native_notifications_are_off_unless_explicitly_enabled(service, actors):
    api, poller, outbox = telegram_setup(service, actors, enabled=False)
    api.updates = [message()]
    asyncio.run(poller.poll_once())
    assert outbox is None and api.sends == []
    assert (
        service.domain.claim_task("outbox", actor=actors[1], kinds=["channel.notification"]) is None
    )


def test_malformed_callback_without_sender_or_chat_ids_is_ignored(service, actors):
    api, poller, _ = telegram_setup(service, actors)
    event = callback("zk:k:in_synthetic:1")
    event["callback_query"]["from"] = {}
    event["callback_query"]["message"]["chat"] = {"type": "private"}
    api.updates = [event]
    assert asyncio.run(poller.poll_once()) == []
    assert api.sends == []


def test_long_callback_id_and_replayed_decision_have_stable_outcome(service, actors):
    async def run():
        api, poller, outbox = telegram_setup(service, actors)
        api.updates = [message()]
        receipt = (await poller.poll_once())[0]
        await service.process_next(actor=actors[1])
        await drain(outbox)
        data = api.sends[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        query_id = "q" * 200
        assert (
            outbox.decide(
                poller,
                data=data,
                chat_id="42",
                sender_id="42",
                external_id=query_id,
                callback_id=query_id,
            )["decision_accepted"]
            is True
        )
        await drain(outbox)
        first_source = service.domain.read_intake(receipt["intake_id"], actor=actors[0])[
            "source_id"
        ]
        assert (
            outbox.decide(
                poller,
                data=data,
                chat_id="42",
                sender_id="42",
                external_id=query_id,
                callback_id=query_id,
            )["decision_accepted"]
            is True
        )
        assert (
            service.domain.read_intake(receipt["intake_id"], actor=actors[0])["source_id"]
            == first_source
        )
        await drain(outbox)
        assert sum(method == "answerCallbackQuery" for method, _ in api.sends) == 1

    asyncio.run(run())


def test_uncertain_send_is_not_automatically_retried_even_if_requeued(service, actors):
    async def run():
        api, poller, outbox = telegram_setup(service, actors)
        api.updates = [message()]
        receipt = (await poller.poll_once())[0]
        api.failure = RuntimeError("Synthetic connection lost after request may have reached API")
        await outbox.process_once()
        task = service.domain.create_task(
            "channel.notification",
            {"intake_id": receipt["intake_id"], "stage": "receipt", "preview_revision": 0},
            actor=actors[1],
            idempotency_key=f"native:{receipt['intake_id']}:receipt:0:",
        )
        current = service.domain.read_task(task["task_id"], actor=actors[1])
        assert current["state"] == "waiting_user"
        assert current["error"]["code"] == "DELIVERY_UNCERTAIN"
        service.domain.update_task(
            task["task_id"],
            {"state": "queued"},
            actor=actors[1],
            idempotency_key="unsafe-requeue-fixture",
        )
        api.failure = None
        await outbox.process_once()
        assert len(api.sends) == 1
        assert service.domain.read_task(task["task_id"], actor=actors[1])["state"] == "waiting_user"

    asyncio.run(run())


def test_known_api_rejection_is_wait_not_delivery_success(service, actors):
    async def run():
        api, poller, outbox = telegram_setup(service, actors)
        api.updates = [message()]
        receipt = (await poller.poll_once())[0]
        api.failure = IntakeError("RATE_LIMITED", "Fixture rejection")
        await outbox.process_once()
        task = service.domain.create_task(
            "channel.notification",
            {"intake_id": receipt["intake_id"], "stage": "receipt", "preview_revision": 0},
            actor=actors[1],
            idempotency_key=f"native:{receipt['intake_id']}:receipt:0:",
        )
        current = service.domain.read_task(task["task_id"], actor=actors[1])
        assert current["state"] == "waiting_quota"
        assert current["checkpoint"]["dispatch_started"] is False

    asyncio.run(run())


def test_protected_preview_is_not_copied_into_native_message(service, actors):
    from ahadiff.knowledge_platform.auth import Actor
    from ahadiff.knowledge_platform.intake.service import IntakeService

    worker = Actor(
        actors[1].owner_id,
        "protected-worker",
        "service",
        actors[1].scopes,
        allowed_classifications=frozenset({"public", "personal", "employer"}),
    )
    service = IntakeService(service.domain, input_classification="employer")

    async def run():
        api, poller, outbox = telegram_setup(service, (actors[0], worker, actors[2]))
        api.updates = [message(text="Synthetic protected body must not be echoed.")]
        await poller.poll_once()
        await service.process_next(actor=worker)
        await drain(outbox)
        text = "\n".join(payload["text"] for _, payload in api.sends)
        assert "Synthetic protected body" not in text
        assert "不会通过消息渠道回传" in text

    asyncio.run(run())
