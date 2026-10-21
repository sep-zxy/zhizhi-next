"""Record S04-01 from the installed Windows UI and isolated PostgreSQL evidence."""

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
    return {
        "path": path.relative_to(run).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--rehearsal-run", type=Path, required=True)
    parser.add_argument("--probe-run", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    rehearsal = args.rehearsal_run.resolve()
    probe_run = args.probe_run.resolve()
    manifest = read_json(run / "manifest.json")
    ui = read_json(rehearsal / "electron-s04.json")
    rehearsal_result = read_json(rehearsal / "result.json")
    probe = read_json(probe_run / "result.json")
    installer_hash = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    if manifest["case_id"] != "S04" or manifest["candidate_sha256"] != installer_hash:
        raise AssertionError("安装包与 S04 候选不一致")
    if rehearsal_result["status"] != "PASS" or not all(rehearsal_result["checks"].values()):
        raise AssertionError("S04-01 界面和数据库前置演练未通过")
    if rehearsal_result["source_run_id"] != run.name or probe["source_run_id"] != run.name:
        raise AssertionError("证据不属于本次 S04 运行")
    if probe["status"] != "PASS" or not probe["checks"]["baseline_bob_can_edit"]:
        raise AssertionError("基线行为 Probe 未通过")
    if ui["base_sha"] != manifest["fixture"]["base_sha"] or ui["platform"] != "win32-x64":
        raise AssertionError("Windows Git 基线不一致")
    if Path(ui["workspace"]).resolve() != run / "fixture" / "repo":
        raise AssertionError("界面绑定的仓库不是本次素材")
    if (run / "steps.jsonl").read_text(encoding="utf-8").strip():
        raise AssertionError("正式步骤已有记录；不能覆盖")
    if (run / "events.jsonl").read_text(encoding="utf-8").strip():
        raise AssertionError("正式事件已有记录；不能覆盖")

    account_id = uuid.UUID(ui["account_id"])
    topic_id = uuid.UUID(ui["topic_id"])
    note_id = uuid.UUID(ui["note_id"])
    with connect(args.dsn) as conn:
        devices = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s",
            (account_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT event_id, trace_id, entity_type, entity_id, event_type, "
            "occurred_at FROM growth_domain_events WHERE account_id=%s "
            "AND entity_id IN (%s, %s) ORDER BY occurred_at",
            (account_id, topic_id, note_id),
        ).fetchall()
    if len(devices) != 1 or [row["event_type"] for row in events] != [
        "topic_user_created", "note_saved",
    ] or [row["entity_id"] for row in events] != [topic_id, note_id]:
        raise AssertionError("已注册设备或业务事件与界面操作不一致")

    target = run / "artifacts"
    target.mkdir(exist_ok=True)
    copied = []
    for source, name in (
        (rehearsal / "electron-s04.json", "s04-01-windows-ui.json"),
        (rehearsal / "postgres-evidence.json", "s04-01-postgres.json"),
        (rehearsal / "result.json", "s04-01-rehearsal-result.json"),
        (probe_run / "artifacts" / "probe-baseline.json", "s04-01-probe-baseline.json"),
    ):
        destination = target / name
        shutil.copyfile(source, destination)
        copied.append(artifact(run, destination))
    event_path = target / "s04-01-domain-events.json"
    write_json(event_path, [dict(row) for row in events])
    copied.append(artifact(run, event_path))
    device_path = target / "s04-01-windows-device.json"
    write_json(device_path, {"device_id": devices[0]["device_id"],
                             "account_id": account_id,
                             "cloud_kind": "isolated_local_postgresql"})
    copied.append(artifact(run, device_path))

    step = {
        "schema_version": 1,
        "run_id": manifest["run_id"],
        "case_id": "S04",
        "step_id": "S04-01",
        "status": "PASS",
        "started_at": ui["started_at"],
        "finished_at": ui["finished_at"],
        "device_id": str(devices[0]["device_id"]),
        "trace_id": str(events[0]["trace_id"]),
        "related_trace_ids": [str(events[1]["trace_id"])],
        "action": "安装版 Windows 界面建立旧主题和手写旧笔记；核对真实 Git C0 与基线 Probe",
        "expected": "只有一个旧主题；原文笔记保留；基线 Bob 可修改 Alice 文档",
        "actual": {
            "cloud_kind": "isolated_local_postgresql",
            "base_sha": ui["base_sha"],
            "project_id": ui["project_id"],
            "binding_id": ui["binding_id"],
            "feature_id": ui["feature_id"],
            "topic_id": ui["topic_id"],
            "note_id": ui["note_id"],
            "note_sha256": rehearsal_result["note_sha256"],
            "topic_count_before": ui["topic_count_before"],
            "topic_count_after": ui["topic_count_after"],
            "baseline_bob": "OK",
        },
        "artifacts": copied,
    }
    (run / "steps.jsonl").write_text(json.dumps(step, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    (run / "events.jsonl").write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n"
                for row in events), encoding="utf-8",
    )
    write_json(run / "chain.json", {
        "project_id": ui["project_id"],
        "binding_id": ui["binding_id"],
        "feature_id": ui["feature_id"],
        "topic_id": ui["topic_id"],
        "note_id": ui["note_id"],
        "base_sha": ui["base_sha"],
    })
    current = read_json(run / "result.json")
    current["proof"] = {"windows_app": artifact(run, target / "s04-01-windows-ui.json")}
    write_json(run / "result.json", current)
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write(
            "\n\n## S04-01 Windows 安装版实测\n\n"
            "真实 Git 基线和行为 Probe 已核对；安装版界面创建项目、绑定仓库、"
            "批准同步，并建立唯一旧主题“登录机制”和手写原文笔记。"
            "账号设备、两条云业务事件、trace 与正文哈希见 `artifacts/s04-01-*`。"
            "此步使用本机隔离 PostgreSQL；后续模型、跨设备与完整 S04 仍为 BLOCKED。\n"
        )
    print(f"S04-01_PASS {ui['topic_id']} {ui['note_id']}")


if __name__ == "__main__":
    main()
