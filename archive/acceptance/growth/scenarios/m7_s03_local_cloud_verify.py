"""Verify installed UI module mapping against isolated PostgreSQL records."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

from ahadiff.growth.cloud.db import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    source = args.source_run.resolve()
    ui = json.loads((run / "electron-cloud.json").read_text(encoding="utf-8"))
    index = json.loads((source / "artifacts" / "code-index.json").read_text(encoding="utf-8"))
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    account_id = uuid.UUID(ui["account_id"])
    module_id = uuid.UUID(ui["module_id"])
    with connect(args.dsn) as conn:
        module = conn.execute(
            "SELECT project_id, snapshot_id, index_revision, effective_tree_hash, "
            "name, member_paths, flow, locked, revision "
            "FROM growth_modules WHERE account_id=%s AND module_id=%s",
            (account_id, module_id),
        ).fetchone()
        snapshot = conn.execute(
            "SELECT project_id, effective_tree_hash FROM growth_snapshots "
            "WHERE account_id=%s AND snapshot_id=%s",
            (account_id, uuid.UUID(ui["snapshot_id"])),
        ).fetchone()
        events = conn.execute(
            "SELECT event_type, revision FROM growth_change_feed "
            "WHERE account_id=%s AND entity_type='module' AND entity_id=%s "
            "ORDER BY change_seq",
            (account_id, module_id),
        ).fetchall()
    response_statuses = {
        item["path"]: item["status"] for item in ui["responses"]
    }
    expected_flow: dict[str, Any] = index["flow"]
    checks = {
        "same_candidate": manifest["candidate_sha256"] == args.candidate_sha256.lower(),
        "independent_snapshot_upload": ui["preview_operation_kinds"]
        == ["project", "feature", "snapshot"] and snapshot is not None,
        "installed_ui_map_and_lock": response_statuses.get(
            "/api/growth/local/cloud/modules/map"
        ) == 201 and response_statuses.get(
            f"/api/growth/local/cloud/modules/{module_id}/update"
        ) == 200 and "已锁定" in ui["module_text"],
        "account_project_snapshot_link": module is not None and snapshot is not None
        and str(module["project_id"]) == ui["project_id"]
        and str(module["snapshot_id"]) == ui["snapshot_id"]
        and str(snapshot["project_id"]) == ui["project_id"],
        "same_index_revision_and_flow": module is not None
        and module["index_revision"].strip() == index["index_revision"]
        and module["effective_tree_hash"].strip() == index["effective_tree_hash"]
        and snapshot is not None
        and snapshot["effective_tree_hash"].strip() == index["effective_tree_hash"]
        and module["flow"] == ui["index_flow"]
        and ui["index_flow"]["index_revision"] == expected_flow["index_revision"]
        and ui["index_flow"]["effective_tree_hash"]
        == expected_flow["effective_tree_hash"],
        "lambda_gateway_edge_and_dynamic_unknown": module is not None
        and {(edge["from"], edge["to"]): edge["status"]
             for edge in module["flow"]["edges"]} == {
                 ("OrderController.quote", "OrderService.quote"): "indexed",
                 ("OrderService.quote", "PriceCache.get"): "indexed",
                 ("OrderService.quote", "PricingGateway.priceCents"): "indexed",
                 ("PriceCache.get", "PricingGateway.priceCents"): "unknown",
             },
        "user_named_and_locked": module is not None and module["name"] == "订单计价"
        and module["locked"] and module["revision"] == 2
        and [item["event_type"] for item in events]
        == ["module_mapped", "module_user_edited"],
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    result = {
        "run_id": run.name,
        "status": status,
        "stage_status": "BLOCKED",
        "scope": "S03 Windows 安装版与本机隔离 PostgreSQL 模块链路演练",
        "candidate_sha256": args.candidate_sha256.lower(),
        "source_run_id": source.name,
        "account_id": str(account_id),
        "project_id": ui["project_id"],
        "snapshot_id": ui["snapshot_id"],
        "module_id": str(module_id),
        "checks": checks,
    }
    (run / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (run / "postgres-evidence.json").write_text(
        json.dumps({
            "module": {
                "name": module["name"] if module else None,
                "locked": module["locked"] if module else None,
                "revision": module["revision"] if module else None,
                "index_revision": module["index_revision"].strip() if module else None,
                "member_paths": module["member_paths"] if module else None,
                "flow": module["flow"] if module else None,
            },
            "events": [dict(item) for item in events],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (run / "report.md").write_text(
        "# M5 Windows 安装版模块命名与锁定演练\n\n"
        f"候选 SHA-256：`{args.candidate_sha256.lower()}`。安装版界面使用"
        "本机隔离 PostgreSQL 云 API，经用户批准同步项目、Feature、源码快照，"
        "随后显示源码调用依据，保存“订单计价”候选并单独锁定。\n\n"
        f"七项链路检查：**{status}**。原始界面响应见 [UI 证据](electron-cloud.json)，"
        "数据库模块、调用边和事件见 [PostgreSQL 证据](postgres-evidence.json)。"
        "本机隔离服务不等于用户的真实云项目；完整 S03 仍为 BLOCKED。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(checks)
    print(run)


if __name__ == "__main__":
    main()
