"""Durable task leases, finite transitions and transactional consumer delivery."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .auth import Actor, authenticated
from .contracts import TASK_STATES, TASK_TERMINAL, new_id, now, required_text, validate_payload
from .errors import DomainError, invalid


def lease_deadline(seconds: int) -> str:
    """Bound lease duration so accidentally infinite work cannot monopolize tasks."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 3600:
        raise invalid("lease_seconds must be between 1 and 3600")
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


class TaskMethods:
    """DomainService implementation mixin; all state writes use its shared store."""

    def control_task(
        self,
        task_id: str,
        action: str,
        expected_revision: int,
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Explicit user control; replay precedes state checks and never steals a lease."""
        authenticated(actor, "knowledge:write")
        if actor.kind != "user":
            raise DomainError(
                "FORBIDDEN", "Task control requires the authenticated user", status_code=403
            )
        if action not in {"resume", "cancel"}:
            raise invalid("Unsupported task control")
        if type(expected_revision) is not int or expected_revision < 1:
            raise invalid("expected_revision must be a positive integer")
        waiting = {
            "waiting_auth",
            "waiting_quota",
            "waiting_user",
            "waiting_review",
            "waiting_input",
            "blocked",
            "partial",
            "retry_wait",
        }

        def control(connection: Any) -> dict[str, Any]:
            task = self._protect_read(
                connection,
                self.store.get(connection, "task", task_id, actor.owner_id),
                actor,
            )
            self._check_revision(task, {"expected_revision": expected_revision})
            allowed = waiting if action == "resume" else waiting | {"queued"}
            if task["state"] not in allowed:
                raise DomainError(
                    "INVALID_STATE",
                    "Only non-running eligible tasks can be controlled",
                    status_code=409,
                )
            if task.get("reconciliation_required"):
                raise DomainError(
                    "RECONCILIATION_REQUIRED",
                    "Reconcile uncertain effects before task control",
                    status_code=409,
                )
            task.update(
                state="queued" if action == "resume" else "cancelled",
                next_run_at=None,
                lease_owner=None,
                lease_token=None,
                lease_until=None,
                revision=task["revision"] + 1,
                updated_at=now(),
            )
            self.store.put(connection, "task", task_id, task)
            self.store.event(
                connection, "task.updated.v1", task, task_id, {"state": task["state"]}, trace_id
            )
            return task

        return self._mutate(
            actor,
            f"control_task:{task_id}:{action}",
            idempotency_key,
            {"expected_revision": expected_revision},
            control,
        )

    def create_task(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one queued task and acceptance event, with exact replay semantics."""
        authenticated(actor, "knowledge:write")
        payload = validate_payload(payload)
        required_text({"kind": kind}, "kind", limit=100)

        def create(connection: Any) -> dict[str, Any]:
            timestamp = now()
            task = {
                "task_id": new_id("tsk"),
                "owner_id": actor.owner_id,
                "kind": kind,
                "state": "queued",
                "payload": payload,
                "result": None,
                "error": None,
                "attempt": 0,
                "max_attempts": 5,
                "lease_owner": None,
                "lease_token": None,
                "lease_until": None,
                "next_run_at": None,
                "revision": 1,
                "trace_id": trace_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            self.store.put(connection, "task", task["task_id"], task)
            self.store.event(connection, "task.queued.v1", task, task["task_id"])
            return task

        return self._mutate(
            actor, "create_task", idempotency_key, {"kind": kind, "payload": payload}, create
        )

    def read_task(self, task_id: str, *, actor: Actor) -> dict[str, Any]:
        """Read an owned task, including durable wait/retry/error information."""
        return self._read("task", task_id, actor)

    def list_tasks(
        self,
        *,
        actor: Actor,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List owned, readable tasks newest-first without revealing hidden counts."""
        authenticated(actor, "knowledge:read")
        if state is not None and (not isinstance(state, str) or state not in TASK_STATES):
            raise invalid("Unknown task state")
        return self._list_owned_records(
            "task", actor=actor, state=state, limit=limit, offset=offset
        )

    def update_task(
        self,
        task_id: str,
        patch: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Update task state; every running worker result must prove its current lease."""
        authenticated(actor, "knowledge:write")
        patch = validate_payload(patch)
        allowed = {
            "state",
            "result",
            "error",
            "lease_token",
            "next_run_at",
            "progress",
            "max_attempts",
            "expected_revision",
            "checkpoint",
        }
        if set(patch) - allowed:
            raise invalid("Unsupported task patch fields")

        def update(connection: Any) -> dict[str, Any]:
            task = self.store.get(connection, "task", task_id, actor.owner_id)
            self._check_revision(task, patch)
            state = patch.get("state", task["state"])
            if state not in TASK_STATES:
                raise invalid("Unknown task state")
            if task["state"] in TASK_TERMINAL:
                raise DomainError("INVALID_STATE", "Terminal tasks are immutable", status_code=409)
            if state == "running" and task["state"] != "running":
                raise DomainError(
                    "INVALID_STATE", "Use claim_task to acquire a task lease", status_code=409
                )
            if task["state"] == "running":
                actor.require("tasks:work")
                if (
                    not patch.get("lease_token")
                    or patch["lease_token"] != task["lease_token"]
                    or task["lease_until"] <= now()
                ):
                    raise DomainError(
                        "LEASE_STALE", "Task lease has expired or changed", status_code=409
                    )
            elif state in {"succeeded", "partial", "failed"}:
                raise DomainError(
                    "INVALID_STATE", "Claim the task before recording an outcome", status_code=409
                )
            if state == "retry_wait" and not patch.get("next_run_at"):
                raise invalid("retry_wait requires next_run_at")
            if "max_attempts" in patch and (
                isinstance(patch["max_attempts"], bool)
                or not isinstance(patch["max_attempts"], int)
                or not 1 <= patch["max_attempts"] <= 100
            ):
                raise invalid("max_attempts must be between 1 and 100")
            if patch.get("next_run_at"):
                try:
                    parsed = datetime.fromisoformat(patch["next_run_at"])
                    if parsed.tzinfo is None:
                        raise ValueError("naive time")
                    patch["next_run_at"] = parsed.astimezone(UTC).isoformat()
                except (TypeError, ValueError) as exc:
                    raise invalid("next_run_at must include timezone") from exc
            for key in allowed - {"lease_token", "expected_revision"}:
                if key in patch:
                    task[key] = patch[key]
            if state != "running":
                task.update(lease_owner=None, lease_token=None, lease_until=None)
            task.update(state=state, revision=task["revision"] + 1, updated_at=now())
            self.store.put(connection, "task", task_id, task)
            self.store.event(
                connection, "task.updated.v1", task, task_id, {"state": state}, trace_id
            )
            return task

        return self._mutate(actor, f"update_task:{task_id}", idempotency_key, patch, update)

    def claim_task(
        self,
        worker_id: str,
        *,
        actor: Actor,
        kinds: list[str] | None = None,
        lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        """Atomically acquire due work or reclaim an expired lease with a new token.

        Reclaimed work must reconcile its operation ID before external side effects.
        A lease never asserts that the previous process did not execute its effect.
        """
        authenticated(actor, "tasks:work")
        required_text({"worker_id": worker_id}, "worker_id", limit=128)
        deadline = lease_deadline(lease_seconds)
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT data FROM kp_records WHERE kind='task' AND owner_id=? ORDER BY rowid",
                (actor.owner_id,),
            ).fetchall()
            for row in rows:
                task = json.loads(row["data"])
                if kinds is not None and task["kind"] not in kinds:
                    continue
                try:
                    task = self._protect_read(connection, task, actor)
                except DomainError as exc:
                    if exc.code != "CLASSIFICATION_FORBIDDEN":
                        raise
                    continue
                state = task["state"]
                due = state == "queued" or (
                    state == "retry_wait" and task.get("next_run_at", "") <= now()
                )
                expired = state == "running" and task.get("lease_until", "") <= now()
                if not (due or expired):
                    continue
                if task["attempt"] >= task["max_attempts"]:
                    task.update(
                        state="failed",
                        error={"code": "ATTEMPTS_EXHAUSTED"},
                        revision=task["revision"] + 1,
                        updated_at=now(),
                        lease_owner=None,
                        lease_token=None,
                        lease_until=None,
                    )
                    self.store.put(connection, "task", task["task_id"], task)
                    self.store.event(connection, "task.failed.v1", task, task["task_id"])
                    continue
                task.update(
                    state="running",
                    lease_owner=worker_id,
                    lease_token=new_id("lease"),
                    lease_until=deadline,
                    attempt=task["attempt"] + 1,
                    revision=task["revision"] + 1,
                    updated_at=now(),
                    reconciliation_required=expired,
                )
                self.store.put(connection, "task", task["task_id"], task)
                self.store.event(
                    connection,
                    "task.claimed.v1",
                    task,
                    task["task_id"],
                    {"attempt": task["attempt"], "reclaimed": expired},
                )
                return task
        return None

    def renew_task_lease(
        self, task_id: str, lease_token: str, *, actor: Actor, lease_seconds: int = 60
    ) -> dict[str, Any]:
        """Extend only a live matching lease, never resurrect an expired worker."""
        authenticated(actor, "tasks:work")
        deadline = lease_deadline(lease_seconds)
        with self.store.transaction() as connection:
            task = self.store.get(connection, "task", task_id, actor.owner_id)
            self._protect_read(connection, task, actor)
            if (
                task["state"] != "running"
                or task["lease_token"] != lease_token
                or task["lease_until"] <= now()
            ):
                raise DomainError(
                    "LEASE_STALE", "Task lease has expired or changed", status_code=409
                )
            task.update(lease_until=deadline, revision=task["revision"] + 1, updated_at=now())
            self.store.put(connection, "task", task_id, task)
            return task

    def resume_due_quota_tasks(self, *, actor: Actor, limit: int = 100) -> list[dict[str, Any]]:
        """Resume only due bounded quota waits; auth and human decisions remain untouched."""
        authenticated(actor, "tasks:work")
        actor.require("knowledge:write")
        if actor.kind != "service":
            raise DomainError("FORBIDDEN", "Quota maintenance requires a service", status_code=403)
        self._validate_page(limit, 0)
        timestamp = datetime.now(UTC)
        resumed = []
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT data FROM kp_records WHERE kind='task' AND owner_id=? ORDER BY rowid",
                (actor.owner_id,),
            ).fetchall()
            for row in rows:
                if len(resumed) >= limit:
                    break
                task = json.loads(row["data"])
                if task["state"] != "waiting_quota" or task["attempt"] >= task["max_attempts"]:
                    continue
                result = task.get("result")
                model = result.get("model") if isinstance(result, dict) else None
                retry_after = model.get("retry_after") if isinstance(model, dict) else None
                if not isinstance(retry_after, str):
                    continue
                try:
                    due = datetime.fromisoformat(retry_after)
                    if due.tzinfo is None or due.astimezone(UTC) > timestamp:
                        continue
                except ValueError:
                    continue
                try:
                    self._protect_read(connection, task, actor)
                except DomainError as exc:
                    if exc.code != "CLASSIFICATION_FORBIDDEN":
                        raise
                    continue
                task.update(
                    state="queued",
                    revision=task["revision"] + 1,
                    updated_at=timestamp.isoformat(),
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    next_run_at=None,
                )
                self.store.put(connection, "task", task["task_id"], task)
                self.store.event(
                    connection,
                    "task.resumed.v1",
                    task,
                    task["task_id"],
                    {"reason": "quota_retry_due", "state": "queued"},
                )
                resumed.append(task)
        return resumed

    def complete_learning_task(
        self,
        task_id: str,
        outcome_id: str,
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Complete an accepted human exercise using real bound learning evidence.

        This is not a worker lease bypass: only user-authored outcomes can finish
        an explicitly accepted learning task; arbitrary task kinds remain leased.
        """
        authenticated(actor, "knowledge:write")
        if actor.kind != "user":
            raise DomainError(
                "FORBIDDEN", "Only the user can complete an exercise", status_code=403
            )

        def complete(connection: Any) -> dict[str, Any]:
            task = self.store.get(connection, "task", task_id, actor.owner_id)
            if task["kind"] != "learning":
                raise DomainError("INVALID_STATE", "Task is not a human exercise", status_code=409)
            outcome = self.store.get(connection, "learning", outcome_id, actor.owner_id)
            facts = outcome["payload"]
            suggestion_id = task["payload"].get("suggestion_id")
            metadata = facts.get("metadata", {})
            if (
                not isinstance(suggestion_id, str)
                or not isinstance(metadata, dict)
                or metadata.get("radar_suggestion_id") != suggestion_id
                or not facts.get("entry_id")
                or facts["entry_id"] not in task["payload"].get("target_entry_ids", [])
            ):
                raise DomainError(
                    "EVIDENCE_REQUIRED", "Outcome is not bound to this exercise", status_code=422
                )
            suggestion = self.store.get(connection, "task", suggestion_id, actor.owner_id)
            acceptance = self.store.get(
                connection,
                "channel",
                self._channel_id("radar:" + suggestion_id, actor),
                actor.owner_id,
            )["state"]
            if (
                suggestion["kind"] != "radar_suggestion"
                or facts["entry_id"] not in suggestion["payload"].get("target_entry_ids", [])
                or acceptance.get("learning_id") != task_id
                or acceptance.get("suggestion_state") not in {"accepted", "completed"}
            ):
                raise DomainError(
                    "INVALID_STATE", "Exercise was not accepted by the user", status_code=409
                )
            if task["state"] == "succeeded":
                if (task.get("result") or {}).get("learning_outcome_id") == outcome_id:
                    return task
                raise DomainError(
                    "IDEMPOTENCY_CONFLICT",
                    "Exercise already has a completed outcome",
                    status_code=409,
                )
            if task["state"] not in {"queued", "waiting_user"}:
                raise DomainError(
                    "INVALID_STATE", "Exercise is not awaiting user completion", status_code=409
                )
            task.update(
                state="succeeded",
                result={
                    "status": "completed",
                    "suggestion_id": suggestion_id,
                    "learning_outcome_id": outcome_id,
                },
                revision=task["revision"] + 1,
                updated_at=now(),
            )
            self.store.put(connection, "task", task_id, task)
            self.store.event(
                connection,
                "learning.task.completed.v1",
                task,
                task_id,
                {"learning_outcome_id": outcome_id, "suggestion_id": suggestion_id},
                trace_id,
            )
            return task

        return self._mutate(
            actor,
            f"complete_learning_task:{task_id}",
            idempotency_key,
            {"outcome_id": outcome_id},
            complete,
        )

    def list_events(
        self,
        *,
        actor: Actor,
        after: int = 0,
        types: list[str] | None = None,
        aggregate_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read an owned ordered event stream for resumable projections."""
        authenticated(actor, "knowledge:read")
        self._validate_page(limit, after)
        query = "SELECT sequence,data FROM kp_events WHERE owner_id=? AND sequence>?"
        params: list[Any] = [actor.owner_id, after]
        if aggregate_id:
            query += " AND aggregate_id=?"
            params.append(aggregate_id)
        if types is not None:
            if not types:
                return []
            query += f" AND type IN ({','.join('?' for _ in types)})"
            params.extend(types)
        query += " ORDER BY sequence"
        with self.store.reading() as connection:
            return self._visible_records(
                connection,
                [
                    {**json.loads(row["data"]), "sequence": row["sequence"]}
                    for row in connection.execute(query, params)
                ],
                actor,
            )[:limit]

    def task_events(
        self, task_id: str, *, actor: Actor, after: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Read only events of an accessible task."""
        self.read_task(task_id, actor=actor)
        return self.list_events(actor=actor, after=after, aggregate_id=task_id, limit=limit)

    def claim_events(
        self, consumer: str, *, actor: Actor, limit: int = 100, lease_seconds: int = 60
    ) -> list[dict[str, Any]]:
        """Lease outbox events independently per consumer, without losing undelivered events."""
        authenticated(actor, "tasks:work")
        self._validate_page(limit, 0)
        required_text({"consumer": consumer}, "consumer", limit=128)
        deadline = lease_deadline(lease_seconds)
        namespace = f"{actor.owner_id}:{consumer}"
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT e.sequence,e.data FROM kp_events e LEFT JOIN kp_deliveries d "
                "ON d.event_id=e.event_id AND d.consumer=? WHERE e.owner_id=? AND "
                "(d.event_id IS NULL OR (d.state='leased' AND d.lease_until<=?)) "
                "ORDER BY e.sequence",
                (namespace, actor.owner_id, now()),
            ).fetchall()
            results = []
            for row in rows:
                if len(results) >= limit:
                    break
                event = json.loads(row["data"])
                try:
                    event = self._protect_read(connection, event, actor)
                except DomainError as exc:
                    if exc.code != "CLASSIFICATION_FORBIDDEN":
                        raise
                    continue
                token = new_id("delivery")
                connection.execute(
                    "INSERT INTO kp_deliveries"
                    "(consumer,event_id,state,lease_token,lease_until,attempts) "
                    "VALUES(?,?,'leased',?,?,1) ON CONFLICT(consumer,event_id) DO UPDATE SET "
                    "state='leased',lease_token=excluded.lease_token,"
                    "lease_until=excluded.lease_until,attempts=attempts+1",
                    (namespace, event["event_id"], token, deadline),
                )
                results.append(
                    {
                        **event,
                        "sequence": row["sequence"],
                        "lease_token": token,
                        "lease_until": deadline,
                    }
                )
            return results

    def acknowledge_event(
        self,
        event_id: str,
        consumer: str,
        lease_token: str,
        *,
        actor: Actor,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        """Acknowledge one delivered event only for the current consumer lease."""
        authenticated(actor, "tasks:work")
        from .contracts import canonical

        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE kp_deliveries SET state='delivered',receipt=? "
                "WHERE consumer=? AND event_id=? AND state='leased' "
                "AND lease_token=? AND lease_until>?",
                (
                    canonical(receipt or {}),
                    f"{actor.owner_id}:{consumer}",
                    event_id,
                    lease_token,
                    now(),
                ),
            )
            if cursor.rowcount != 1:
                raise DomainError(
                    "LEASE_STALE", "Delivery lease has expired or changed", status_code=409
                )
