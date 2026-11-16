"""Record one targeted external event and one installed-app Git capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from m7_s04_record_step02 import artifact, read, write

from ahadiff.growth.cloud.db import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read(run / "manifest.json")
    chain = read(run / "chain.json")
    event_path = run / "artifacts" / "s04-05-event-send.json"
    ui_path = run / "artifacts" / "s04-05-event-ui.json"
    audit_path = run / "artifacts" / "s04-05-event-audit.json"
    event, ui, audit = read(event_path), read(ui_path), read(audit_path)
    steps = [json.loads(line) for line in (run / "steps.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    digest = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    if manifest["case_id"] != "S04" or manifest["candidate_sha256"] != digest:
        raise AssertionError("安装包与 S04 候选不一致")
    if [item["step_id"] for item in steps] != [
            "S04-01", "S04-02", "S04-03", "S04-04",
    ] or any(item["status"] != "PASS" for item in steps):
        raise AssertionError("S04-01 至 S04-04 必须先连续通过")
    if event["status"] != "PASS" or ui["status"] != "PASS" \
            or audit["status"] != "PASS" or not all(event["checks"].values()) \
            or not all(ui["checks"].values()) or not all(audit["checks"].values()):
        raise AssertionError("外部事件、安装版捕获或双端审计未通过")
    old_ui = read(run / "artifacts" / "s04-01-windows-ui.json")
    local_setup = read(run / "artifacts" / "s04-01-rehearsal-result.json")
    local_run = run.parent / local_setup["run_id"]
    local_result = read(local_run / "result.json")
    event_id = uuid.UUID(event["event_id"])
    account = uuid.UUID(event["account_id"])
    with connect(args.dsn) as conn:
        received = conn.execute(
            "SELECT event_id, trace_id, entity_type, entity_id, event_type, "
            "payload, occurred_at FROM growth_domain_events "
            "WHERE account_id=%s AND entity_type='development_event' "
            "AND entity_id=%s AND event_type='development_event_received'",
            (account, event_id),
        ).fetchall()
    checks = {
        "same_account_project_and_head": event["account_id"] == old_ui["account_id"]
        and event["event_payload"]["project_id"] == chain["project_id"]
        and event["event_payload"]["feature_id"] == chain["feature_id"]
        and event["head_sha"] == chain["head_sha"]
        and audit["base_sha"] == chain["base_sha"],
        "targeted_device_and_deduplication": event["device_id"] == steps[0]["device_id"]
        and event["event_payload"]["target_device_id"] == steps[0]["device_id"]
        and [item["status"] for item in event["responses"]] == [202, 202, 409]
        and len(received) == 1,
        "installed_capture_once": local_result["candidate_sha256"] == digest
        and local_result["source_run_id"] == run.name
        and ui["workspace"] == str(run / "fixture" / "repo")
        and ui["event_id"] == event["event_id"]
        and ui["snapshot_id"] == audit["snapshot_id"]
        and ui["responses"][-2]["status"] == 201
        and ui["responses"][-1]["status"] == 200
        and audit["local_receipt_count"] == 1
        and audit["cloud_change_count"] == 1,
        "captured_sources_match_commit": audit["head_sha"] == chain["head_sha"]
        and audit["source_paths"] == ["src/access.ts", "src/document.ts"],
    }
    if not all(checks.values()):
        raise AssertionError(checks)
    record = {
        "schema_version": 1, "run_id": manifest["run_id"], "case_id": "S04",
        "step_id": "S04-05", "status": "PASS",
        "started_at": event["event_payload"]["occurred_at"],
        "finished_at": ui["finished_at"],
        "device_id": steps[0]["device_id"],
        "trace_id": str(received[0]["trace_id"]),
        "action": "外部 Agent 向真实事件 API 投递同一 event_id 两次，安装版核对并捕获 Git 快照",
        "expected": "事件去重；目标设备、绑定和 HEAD 相符；只有一个分析触发快照",
        "actual": {"cloud_kind": "isolated_local_postgresql",
                   "event_id": event["event_id"], "snapshot_id": ui["snapshot_id"],
                   "head_sha": chain["head_sha"], "checks": checks},
        "artifacts": [artifact(run, path) for path in
                      (event_path, ui_path, audit_path)],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(received[0]), ensure_ascii=False, default=str) + "\n")
    chain["development_event_id"] = event["event_id"]
    chain["snapshot_id"] = ui["snapshot_id"]
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\n\n## S04-05 开发事件与同包代码捕获\n\n"
                     "模拟外部 Agent 通过真实 API 投递携带目标设备与 HEAD 的事件；"
                     "同 event_id 重发去重，改变载荷被拒绝。安装版界面捕获一次快照，"
                     "再次捕获复用原 snapshot_id；SQLite、PostgreSQL 与源码哈希"
                     "审计见 `artifacts/s04-05-*`。\n")
    print(f"S04-05_PASS {event['event_id']} {ui['snapshot_id']}")


if __name__ == "__main__":
    main()
