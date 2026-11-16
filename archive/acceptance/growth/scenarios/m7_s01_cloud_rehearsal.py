"""Clone an audited S01 feedback chain for isolated installed cloud sync."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3

from m7_s01_model_rehearsal import RUNS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s01-cloud-rehearsal-"):
        raise ValueError("目标运行 ID 必须标识为 cloud rehearsal")
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    ui = json.loads((source / "artifacts" / "feedback-ui.json").read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or audit["mode"] != "replay_not_live":
        raise ValueError("来源必须是已通过审计的隔离反馈预演")
    source_repo = source / "fixture" / "repo"
    target_repo = target / "fixture" / "repo"
    target_repo.parent.mkdir(parents=True)
    shutil.copytree(source_repo, target_repo)
    with sqlite3.connect(source_repo / ".ahadiff" / "growth.sqlite") as origin, sqlite3.connect(
        target_repo / ".ahadiff" / "growth.sqlite"
    ) as cloned:
        origin.backup(cloned)
        cloned.execute("UPDATE bindings SET canonical_local_path=?", (str(target_repo),))
        cloned.commit()
    preparation = {
        "mode": "replay_not_live",
        "status": "prepared",
        "run_id": args.run_id,
        "source_run_id": args.source_run,
        "candidate_sha256": audit["candidate_sha256"],
        "workspace": str(target_repo),
        "task_id": ui["task_id"],
        "attempt_ids": [turn["attempt_id"] for turn in ui["turns"]],
        "note_id": ui["final_chain"]["notes"][0]["note_id"],
    }
    (target / "preparation.json").write_text(
        json.dumps(preparation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(preparation, ensure_ascii=False))


if __name__ == "__main__":
    main()
