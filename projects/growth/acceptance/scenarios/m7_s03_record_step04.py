"""Record S03-04 after installed UI, CodeGraph and PostgreSQL agree."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


def artifact(run: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(run).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--rehearsal-run", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    rehearsal = args.rehearsal_run.resolve()
    manifest = read_json(run / "manifest.json")
    result = read_json(run / "result.json")
    ui = read_json(rehearsal / "electron-cloud.json")
    local_result = read_json(rehearsal / "result.json")
    source_index = read_json(run / "artifacts" / "code-index.json")
    if manifest["case_id"] != "S03" or hashlib.sha256(
        args.installer.read_bytes()
    ).hexdigest() != manifest["candidate_sha256"]:
        raise AssertionError("S03 安装包候选不一致")
    if result["status"] != "BLOCKED" or not all(
        result["checks"][f"S03-{number:02d}"] for number in range(1, 4)
    ):
        raise AssertionError("S03 前三步没有通过")
    if local_result["status"] != "PASS" or not all(local_result["checks"].values()):
        raise AssertionError("安装版模块操作未通过")
    if local_result["source_run_id"] != run.name or ui["platform"] != "win32-x64":
        raise AssertionError("模块操作不属于当前 Windows 情境")
    if ui["snapshot_id"] != source_index["snapshot_id"] or ui["index_flow"][
        "index_revision"
    ] != source_index["index_revision"]:
        raise AssertionError("模块流程未对应当前源码索引")
    if "S03-04" in (run / "steps.jsonl").read_text(encoding="utf-8"):
        raise AssertionError("S03-04 已有正式记录；不能覆盖")

    account_id = uuid.UUID(ui["account_id"])
    snapshot_id = uuid.UUID(ui["snapshot_id"])
    module_id = uuid.UUID(ui["module_id"])
    with connect(args.dsn) as conn:
        devices = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s",
            (account_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT event_id, trace_id, entity_type, entity_id, event_type, "
            "occurred_at FROM growth_domain_events WHERE account_id=%s "
            "AND entity_id IN (%s, %s) ORDER BY occurred_at, event_type",
            (account_id, snapshot_id, module_id),
        ).fetchall()
    if len(devices) != 1 or [row["event_type"] for row in events] != [
        "snapshot_saved", "module_mapped", "module_user_edited",
    ]:
        raise AssertionError("快照与模块业务事件不完整")
    node_id = read_json(run / "artifacts" / "windows-node.json")["node_id"]

    target = run / "artifacts"
    copied = []
    for source, name in (
        (rehearsal / "electron-cloud.json", "s03-04-windows-ui.json"),
        (rehearsal / "postgres-evidence.json", "s03-04-postgres.json"),
        (rehearsal / "result.json", "s03-04-rehearsal-result.json"),
    ):
        destination = target / name
        shutil.copyfile(source, destination)
        copied.append(artifact(run, destination))
    event_path = target / "s03-04-domain-events.json"
    write_json(event_path, [dict(row) for row in events])
    copied.append(artifact(run, event_path))

    step = {
        "schema_version": 1,
        "run_id": manifest["run_id"],
        "case_id": "S03",
        "step_id": "S03-04",
        "status": "PASS",
        "started_at": events[0]["occurred_at"].isoformat(),
        "finished_at": events[-1]["occurred_at"].isoformat(),
        "device_id": node_id,
        "trace_id": str(events[1]["trace_id"]),
        "related_trace_ids": [str(events[0]["trace_id"]),
                              str(events[2]["trace_id"])],
        "action": "安装版 UI 查看四符号源码流，命名“订单计价”并单独锁定模块",
        "expected": "Controller → Service → Cache 有依据；Service lambda → Gateway 有依据；"
                    "Cache → Gateway 直接边未知；名称与锁定事件可追溯",
        "actual": {
            "cloud_kind": "isolated_local_postgresql",
            "snapshot_id": ui["snapshot_id"],
            "module_id": ui["module_id"],
            "cloud_device_id": str(devices[0]["device_id"]),
            "index_revision": source_index["index_revision"],
            "module_name": "订单计价",
            "locked_revision": 2,
            "flow": ui["index_flow"],
        },
        "artifacts": [artifact(run, run / "artifacts" / "code-index.json"),
                      artifact(run, run / "artifacts" / "windows-node.json"), *copied],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(step, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        for row in events:
            output.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
    chain = read_json(run / "chain.json")
    chain["module_id"] = ui["module_id"]
    write_json(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write(
            "\n\n## S03-04 Windows 安装版模块操作\n\n"
            "四符号源码流同时显示 Service lambda → Gateway 的索引边和"
            "Cache → Gateway 的未知边；用户经安装版界面命名“订单计价”并锁定。"
            "源码索引、UI 请求、模块版本与云业务事件均已留证。"
            "本步使用本机隔离 PostgreSQL；模型、ReMe、Mac 及完整 S03 仍为 BLOCKED。\n"
        )
    print(f"S03-04_PASS {ui['module_id']}")


if __name__ == "__main__":
    main()
