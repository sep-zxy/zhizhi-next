"""Opt-in native replies with bound recipients and a durable, restricted outbox.

No arbitrary recipient/body entry point is exposed. Templates read only the
corresponding owner's intake. A started send with an uncertain result is never
automatically repeated, because Bot API sendMessage has no idempotency key.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from ..auth import classification_labels
from ..errors import DomainError
from ..intake.contracts import IntakeError
from ..intake.publication import status_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..auth import Actor
    from ..intake.service import IntakeService

_DECISION = re.compile(r"zk:([kd]):(in_[A-Za-z0-9_-]{1,64}):([1-9][0-9]{0,8})")
_WECHAT_DECISION = re.compile(r"知枝(收藏|丢弃) (in_[A-Za-z0-9_-]{1,64}) ([1-9][0-9]{0,8})")
_STAGES = {
    "receipt",
    "preview",
    "retained",
    "discarded",
    "expired",
    "processing",
    "callback_ack",
    "decision_rejected",
}


def _excerpt(value: str, limit: int = 900) -> str:
    clean = "".join(c for c in value if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    return clean.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


class NativeNotificationDispatcher:
    """Trusted assembly only; merely constructing this object performs no sends."""

    consumer = "channel-native-notifications-v1"

    def __init__(self, intake: IntakeService, *, actor: Actor, adapters: Sequence[Any]) -> None:
        self.intake, self.domain, self.actor = intake, intake.domain, actor
        self.adapters = {adapter.channel_key: adapter for adapter in adapters}
        for adapter in adapters:
            if (
                adapter.intake.domain is not self.domain
                or adapter.state_actor.owner_id != actor.owner_id
            ):
                raise ValueError("Notification adapters must share the domain and owner")
            adapter.notifications = self

    def _route(self, intake_id: str) -> dict[str, Any]:
        return self.domain.get_channel_state("native-route:" + intake_id, actor=self.actor)

    def _bound(self, route: dict[str, Any]) -> tuple[Any, Actor]:
        adapter = self.adapters.get(route.get("channel_key"))
        if adapter is None:
            raise IntakeError("CHANNEL_NOT_CONFIGURED", "原生回执通道未配置。")
        actor = adapter.bindings.resolve(
            route["channel"], route["account_id"], route["chat_id"], route["sender_id"]
        )
        if actor is None or actor.kind != "user" or actor.owner_id != self.actor.owner_id:
            raise IntakeError("FORBIDDEN", "回执接收者绑定已失效。", status_code=403)
        return adapter, actor

    def register_receipt(
        self,
        adapter: Any,
        receipt: dict[str, Any],
        *,
        channel: str,
        account_id: str,
        chat_id: str,
        sender_id: str,
    ) -> None:
        """Called only after verified inbound sender mapping; HTTP cannot set a route."""
        route = {
            "channel_key": adapter.channel_key,
            "channel": channel,
            "account_id": account_id,
            "chat_id": chat_id,
            "sender_id": sender_id,
        }
        _, actor = self._bound(route)
        record = self.domain.read_intake(receipt["intake_id"], actor=actor)
        if record["channel"] != adapter.channel_key:
            raise IntakeError("FORBIDDEN", "收件渠道与回执目标不匹配。", status_code=403)
        self.domain.set_channel_state(
            "native-route:" + receipt["intake_id"],
            route,
            actor=self.actor,
            idempotency_key="native-route:" + receipt["intake_id"],
        )
        self._queue(record, "receipt")
        if record.get("source_id"):
            self._queue(record, "retained")
        elif record["state"] in {"discarded", "expired"}:
            self._queue(record, record["state"])
        elif record.get("preview"):
            self._queue(record, "preview")

    def _queue(
        self,
        record: dict[str, Any],
        stage: str,
        *,
        callback_id: str | None = None,
        callback_ok: bool = True,
        processing_state: str | None = None,
    ) -> dict[str, Any]:
        if stage not in _STAGES:
            raise ValueError("Unknown notification template")
        payload = {
            "intake_id": record["intake_id"],
            "stage": stage,
            "preview_revision": record.get("preview_revision", 0) if stage == "preview" else 0,
        }
        if callback_id is not None:
            payload.update(callback_id=callback_id, callback_ok=callback_ok)
        if processing_state is not None:
            payload["processing_state"] = processing_state
        return self.domain.create_task(
            "channel.notification",
            payload,
            actor=self.actor,
            idempotency_key=f"native:{record['intake_id']}:{stage}:{payload['preview_revision']}:"
            + (
                hashlib.sha256(callback_id.encode()).hexdigest()
                if callback_id
                else processing_state or ""
            ),
        )

    def collect_events(self) -> int:
        """Queue fixed templates before acknowledging the domain event lease."""
        events = self.domain.claim_events(self.consumer, actor=self.actor, limit=100)
        for event in events:
            kind = event["type"]
            intake_id: str | None = None
            processing_state = None
            stage = None
            if kind in {"intake.preview_ready.v1", "intake.partial.v1"}:
                intake_id, stage = event["aggregate_id"], "preview"
            elif kind in {"intake.discarded.v1", "intake.expired.v1", "intake.retained.v1"}:
                intake_id, stage = event["aggregate_id"], kind.split(".")[1]
            elif kind == "source.retained.v1":
                intake_id, stage = event["payload"].get("intake_id"), "retained"
            elif kind == "task.updated.v1":
                task = self.domain.read_task(event["aggregate_id"], actor=self.actor)
                if task["kind"] == "knowledge.generate" and task["state"] not in {
                    "queued",
                    "running",
                }:
                    intake_id, stage = task["payload"].get("intake_id"), "processing"
                    processing_state = task["state"]
            if intake_id and stage:
                route = self._route(intake_id)
                if route:
                    try:
                        self._bound(route)
                        record = self.domain.read_intake(intake_id, actor=self.actor)
                        self._queue(record, stage, processing_state=processing_state)
                    except (IntakeError, DomainError) as error:
                        if error.code not in {
                            "FORBIDDEN",
                            "NOT_FOUND",
                            "CHANNEL_NOT_CONFIGURED",
                            "CLASSIFICATION_FORBIDDEN",
                        }:
                            raise
            self.domain.acknowledge_event(
                event["event_id"],
                self.consumer,
                event["lease_token"],
                actor=self.actor,
                receipt={"notification_projection": "checked"},
            )
        return len(events)

    def decide(
        self,
        adapter: Any,
        *,
        data: str,
        chat_id: str,
        sender_id: str,
        external_id: str,
        callback_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Process only explicit native decisions, never ordinary source text instructions."""
        matched = _DECISION.fullmatch(data)
        if matched is None:
            return None
        decision, intake_id, revision_text = matched.groups()
        route = self._route(intake_id)
        if (
            not route
            or route.get("channel_key") != adapter.channel_key
            or (route.get("chat_id"), route.get("sender_id")) != (chat_id, sender_id)
        ):
            return None
        _, actor = self._bound(route)
        record = self.domain.read_intake(intake_id, actor=actor)
        decision_key = (
            "native-decision:"
            + hashlib.sha256((adapter.channel_key + ":" + external_id).encode()).hexdigest()
        )
        previous = self.domain.get_channel_state(decision_key, actor=self.actor)
        if previous:
            if (previous.get("intake_id"), previous.get("decision"), previous.get("revision")) != (
                intake_id,
                decision,
                revision_text,
            ):
                return None
            ok = previous["accepted"]
            if callback_id:
                self._queue(record, "callback_ack", callback_id=callback_id, callback_ok=ok)
            elif not ok:
                self._queue(record, "decision_rejected", callback_id=external_id)
            return {"intake_id": intake_id, "decision_accepted": ok}
        ok = True
        try:
            if decision == "k":
                self.intake.keep(
                    intake_id,
                    preview_revision=int(revision_text),
                    actor=actor,
                    idempotency_key=decision_key + ":keep",
                )
            else:
                self.intake.discard(
                    intake_id,
                    preview_revision=int(revision_text),
                    actor=actor,
                    idempotency_key=decision_key + ":discard",
                )
        except (IntakeError, DomainError) as error:
            if error.code not in {"BASE_STALE", "INTAKE_EXPIRED", "INVALID_STATE", "FORBIDDEN"}:
                raise
            ok = False
        self.domain.set_channel_state(
            decision_key,
            {
                "intake_id": intake_id,
                "decision": decision,
                "revision": revision_text,
                "accepted": ok,
            },
            actor=self.actor,
            idempotency_key=decision_key,
        )
        if callback_id:
            self._queue(record, "callback_ack", callback_id=callback_id, callback_ok=ok)
        elif not ok:
            self._queue(record, "decision_rejected", callback_id=external_id)
        return {"intake_id": intake_id, "decision_accepted": ok}

    def wechat_decision(
        self, adapter: Any, text: str, *, sender_id: str, external_id: str
    ) -> dict[str, Any] | None:
        match = _WECHAT_DECISION.fullmatch(text)
        if match is None:
            return None
        verb, intake_id, revision = match.groups()
        return self.decide(
            adapter,
            data=f"zk:{'k' if verb == '收藏' else 'd'}:{intake_id}:{revision}",
            chat_id=adapter.config.open_kfid,
            sender_id=sender_id,
            external_id=external_id,
        )

    def _render(
        self, task: dict[str, Any], route: dict[str, Any], record: dict[str, Any]
    ) -> dict[str, Any] | None:
        payload, stage = task["payload"], task["payload"].get("stage")
        if stage not in _STAGES:
            raise IntakeError("INVALID_NOTIFICATION", "回执模板无效。")
        if stage == "callback_ack":
            return {
                "callback_query_id": payload["callback_id"],
                "text": "决定已记录。"
                if payload.get("callback_ok")
                else "决定未生效，请查看当前预览。",
            }
        revision = record.get("preview_revision", 0)
        text = ""
        buttons = None
        if stage == "receipt":
            if record.get("source_id") or record["state"] in {"discarded", "expired"}:
                return None
            text = "已收件，正在准备预览。尚未收藏，不会自动公开。"
        elif stage == "preview":
            if (
                record.get("source_id")
                or record["state"] in {"discarded", "expired"}
                or revision != payload.get("preview_revision")
            ):
                return None
            preview = record.get("preview") or {}
            document = preview.get("document") or {}
            text = "预览已就绪。尚未收藏。\n解析状态：" + str(
                record.get("parse_status", "needs_input")
            )
            labels = classification_labels(record, default="personal")
            if labels <= {"public", "personal"}:
                value = (
                    preview.get("summary")
                    or document.get("body_text")
                    or document.get("description")
                )
                if isinstance(value, str) and value:
                    text += "\n来源摘要/节选（不可信来源内容）：\n" + _excerpt(value)
            else:
                text += "\n此分类的正文不会通过消息渠道回传，请在受保护入口查看。"
            if route["channel"] == "telegram":
                buttons = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "收藏",
                                "callback_data": f"zk:k:{record['intake_id']}:{revision}",
                            },
                            {
                                "text": "丢弃",
                                "callback_data": f"zk:d:{record['intake_id']}:{revision}",
                            },
                        ]
                    ]
                }
            else:
                text += f"\n收藏请单独回复：知枝收藏 {record['intake_id']} {revision}"
                text += f"\n丢弃请单独回复：知枝丢弃 {record['intake_id']} {revision}"
        elif stage == "retained":
            text = "已按你的决定收藏，知识处理已排队。尚不代表处理完成或已公开。"
        elif stage == "discarded":
            text = "已丢弃临时收件，未创建保留来源。"
        elif stage == "expired":
            text = "临时预览已过期，未自动收藏。"
        elif stage == "decision_rejected":
            text = "决定未生效，预览可能已更新或过期。请查看当前预览后重新决定。"
        else:
            states = {
                "succeeded": "已结束，请在知识库核对结果",
                "failed": "失败",
                "partial": "需补充材料",
                "waiting_auth": "等待认证",
                "waiting_quota": "等待限额恢复",
                "waiting_user": "等待你的决定",
                "blocked": "受策略限制",
                "cancelled": "已取消",
                "waiting_review": "等待复核",
            }
            text = (
                "知识处理："
                + states.get(payload.get("processing_state"), "等待处理")
                + "。公开资格与发布状态请核对知识条目。"
            )
        result: dict[str, Any] = {"text": text, "stage": stage}
        if stage in {"receipt", "preview", "retained", "processing"}:
            result["text"] += "\n" + status_text(
                self.intake.publication_status(record["intake_id"], actor=self.actor)
            )
        if buttons:
            result["reply_markup"] = buttons
        return result

    def _update(self, task: dict[str, Any], patch: dict[str, Any], stage: str) -> dict[str, Any]:
        return self.domain.update_task(
            task["task_id"],
            {**patch, "lease_token": task["lease_token"]},
            actor=self.actor,
            idempotency_key=f"notify:{task['task_id']}:{task['lease_token']}:{stage}",
        )

    async def process_once(self) -> bool:
        """Send at most one owned fixed template; uncertain delivery requires review."""
        task = self.domain.claim_task(
            "native-outbox", actor=self.actor, kinds=["channel.notification"], lease_seconds=120
        )
        if task is None:
            return False
        if (task.get("checkpoint") or {}).get("dispatch_started"):
            self._update(
                task,
                {"state": "waiting_user", "error": {"code": "DELIVERY_UNCERTAIN"}},
                "uncertain-reclaim",
            )
            return True
        started = False
        try:
            intake_id = task["payload"]["intake_id"]
            route = self._route(intake_id)
            adapter, _ = self._bound(route)
            record = self.domain.read_intake(intake_id, actor=self.actor)
            rendered = self._render(task, route, record)
            if rendered is None:
                self._update(
                    task, {"state": "succeeded", "result": {"suppressed": True}}, "suppressed"
                )
                return True
            self._update(task, {"checkpoint": {"dispatch_started": True}}, "dispatch-started")
            started = True
            result = await adapter.send_native(route, rendered, task["task_id"])
            self._update(
                task,
                {"state": "succeeded", "result": {"api_accepted": True, "message_id": result}},
                "accepted",
            )
        except (IntakeError, DomainError) as error:
            if error.code == "LEASE_STALE":
                return True
            if error.code in {"NEEDS_AUTH", "RATE_LIMITED", "CHANNEL_REJECTED"}:
                state = (
                    "waiting_auth"
                    if error.code == "NEEDS_AUTH"
                    else "waiting_quota"
                    if error.code == "RATE_LIMITED"
                    else "waiting_user"
                )
                self._update(
                    task,
                    {
                        "state": state,
                        "checkpoint": {"dispatch_started": False},
                        "error": {"code": error.code},
                    },
                    "rejected",
                )
            else:
                self._update(
                    task,
                    {
                        "state": "waiting_user" if started else "blocked",
                        "error": {
                            "code": "DELIVERY_UNCERTAIN" if started else "NOTIFICATION_BLOCKED"
                        },
                    },
                    "failed",
                )
        except Exception:
            self._update(
                task,
                {
                    "state": "waiting_user" if started else "blocked",
                    "error": {"code": "DELIVERY_UNCERTAIN" if started else "NOTIFICATION_BLOCKED"},
                },
                "unknown-error",
            )
        return True
