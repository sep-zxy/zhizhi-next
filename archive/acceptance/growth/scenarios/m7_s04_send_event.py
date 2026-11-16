"""Send a real targeted development event to an acceptance cloud."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--local-db", type=Path,
                        help="目标设备账本位置；默认使用工作区内 .ahadiff/growth.sqlite")
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true",
                        help="复用输出文件中的原始事件载荷")
    args = parser.parse_args()
    setup = json.loads(args.setup.read_text(encoding="utf-8"))
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
    if setup["workspace"].casefold() != str(args.workspace.resolve()).casefold():
        raise ValueError("项目绑定与当前工作区不一致")
    if setup["account_id"] != credentials["account_id"]:
        raise ValueError("云账号与本机绑定不一致")
    head = subprocess.run(
        ["git", "-C", str(args.workspace), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(args.workspace), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if status:
        raise ValueError("工作区存在未提交变更")
    database = args.local_db or args.workspace / ".ahadiff" / "growth.sqlite"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT device_id, bootstrapped FROM sync_accounts WHERE account_id=?",
            (setup["account_id"],),
        ).fetchone()
    if row is None or row[1] != 1:
        raise ValueError("目标设备尚未完成云端登记")
    device_id = row[0]
    cloud_url = credentials.get("cloud_url") or f"http://127.0.0.1:{credentials['port']}"
    event = {
        "operation_id": str(uuid.uuid4()),
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "project_id": setup["project_id"],
        "feature_id": setup["feature_id"],
        "type": "coding_task_finished",
        "occurred_at": datetime.now(UTC).isoformat(),
        "source": "codex",
        "target_device_id": device_id,
        "expected_head_sha": head,
    }
    if args.resume:
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        event = previous["event_payload"]
        if event["expected_head_sha"] != head or event["target_device_id"] != device_id:
            raise ValueError("待复用事件与当前仓库 HEAD 或设备不一致")
    headers = {
        "Authorization": f"Bearer {credentials['access_token']}",
        "X-Device-Id": device_id,
    }
    with httpx.Client(base_url=cloud_url, timeout=20.0) as client:
        first = client.post("/v1/development-events", json=event, headers=headers)
        retry = client.post(
            "/v1/development-events",
            json={**event, "operation_id": str(uuid.uuid4())}, headers=headers,
        )
        altered = client.post(
            "/v1/development-events",
            json={**event, "operation_id": str(uuid.uuid4()),
                  "expected_head_sha": setup["base_sha"]},
            headers=headers,
        )
    created = first.json()
    repeated = retry.json()
    changed = altered.json()
    checks = {
        "created_targeted": first.status_code == 202
        and created.get("status") == "targeted"
        and created.get("target_device_id") == device_id,
        "retry_deduplicated": retry.status_code == 202
        and all(repeated.get(key) == created.get(key) for key in (
            "event_id", "status", "target_device_id", "change_seq")),
        "changed_payload_rejected": altered.status_code == 409
        and changed.get("code") == "EVENT_ID_REUSED",
    }
    evidence = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "local_cloud_rehearsal",
        "workspace": str(args.workspace.resolve()),
        "cloud_origin": cloud_url,
        "account_id": setup["account_id"],
        "binding_id": setup["binding_id"],
        "device_id": device_id,
        "head_sha": head,
        "event_id": event["event_id"],
        "event_payload": event,
        "responses": [
            {"status": first.status_code, "body": created},
            {"status": retry.status_code, "body": repeated},
            {"status": altered.status_code, "body": changed},
        ],
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if evidence["status"] != "PASS":
        raise AssertionError(f"外部事件提交失败：{checks}")
    print(f"S04_EVENT_SEND_PASS {event['event_id']} {head}")


if __name__ == "__main__":
    main()
