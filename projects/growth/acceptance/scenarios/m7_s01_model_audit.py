"""Audit isolated S01 model-driver rehearsal without promoting it to live PASS."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    prepared = read_json(run / "preparation.json")
    ui = read_json(run / "artifacts" / "model-ui.json")
    requests = json.loads((run / "provider-requests.json").read_text(encoding="utf-8"))
    source_run = run.parent / prepared["source_run_id"]
    source_manifest = read_json(source_run / "manifest.json")
    source_steps = [json.loads(line) for line in
                    (source_run / "steps.jsonl").read_text(encoding="utf-8-sig").splitlines()
                    if line.strip()]
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        batch = db.execute(
            "SELECT * FROM opportunity_batches WHERE analysis_id=?",
            (ui["submission"]["analysis_id"],),
        ).fetchone()
        policy = db.execute(
            "SELECT model_allowed FROM project_policies WHERE project_id=?",
            (ui["project_id"],),
        ).fetchone()
        approved = db.execute(
            "SELECT approved_payload_hash, source_ref_ids_json FROM analysis_model_requests "
            "WHERE analysis_id=?",
            (ui["submission"]["analysis_id"],),
        ).fetchone()
        response = db.execute(
            "SELECT provider_request_id, input_tokens, output_tokens, elapsed_ms "
            "FROM analysis_model_responses WHERE analysis_id=?",
            (ui["submission"]["analysis_id"],),
        ).fetchone()
    checks = {
        "candidate_matches_source": prepared["candidate_sha256"]
        == source_manifest["candidate_sha256"],
        "isolated_replay_only": prepared["mode"] == ui["mode"] == "replay_not_live"
        and Path(prepared["workspace"]).resolve().is_relative_to(run),
        "project_policy_saved": ui["project_model_allowed"] is True
        and bool(policy["model_allowed"]),
        "exact_ui_payload_sent": len(requests) == 1
        and ui["preview"]["ui_payload_exact_match"] is True
        and ui["preview"]["payload_sha256"] == requests[0]["prompt_sha256"],
        "approved_source_persisted": approved is not None
        and approved["approved_payload_hash"] == ui["preview"]["approval_hash"]
        and json.loads(approved["source_ref_ids_json"]) == ui["preview"]["source_ref_ids"],
        "native_schema_request": len(requests) == 1 and requests[0]["has_native_schema"] is True
        and requests[0]["stream"] is False,
        "opportunity_persisted_and_visible": ui["status"] == "observed_pending_semantic_review"
        and ui["analysis"]["status"] == "succeeded" and len(ui["opportunities"]) == 1
        and json.loads(ui["opportunities"][0]["source_refs_json"])
        == ui["preview"]["source_ref_ids"],
        "scenario_learning_goal_exact": len(ui["opportunities"]) == 1
        and ui["opportunities"][0]["learning_goal"]
        == "解释任务由哪个执行器执行，join() 是否阻塞调用者。",
        "replay_provider_identified": batch is not None
        and batch["model_name"] == "acceptance-replay-model"
        and batch["provider_request_id"] == "opportunity-replay-1",
        "response_usage_persisted": response is not None
        and response["provider_request_id"] == "opportunity-replay-1"
        and response["input_tokens"] == 42 and response["output_tokens"] == 27
        and response["elapsed_ms"] >= 0,
        "formal_s01_not_promoted": [step["step_id"] for step in source_steps]
        == ["S01-01", "S01-02", "S01-03"],
    }
    audit = {
        "run_id": prepared["run_id"],
        "mode": "replay_not_live",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "scope": "Windows 安装版 S01-04 模型驱动预演；不计入正式情境",
        "candidate_sha256": prepared["candidate_sha256"],
        "source_run_id": prepared["source_run_id"],
        "analysis_id": ui["submission"]["analysis_id"],
        "opportunity_id": ui["opportunities"][0]["opportunity_id"],
        "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False))
    if audit["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
