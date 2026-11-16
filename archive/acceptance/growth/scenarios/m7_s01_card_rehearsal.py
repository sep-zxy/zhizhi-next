"""Clone a model rehearsal opportunity into an isolated local learning rehearsal."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from m7_s01_model_rehearsal import RUNS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s01-card-rehearsal-"):
        raise ValueError("目标运行 ID 必须标识为 card rehearsal")
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    source_audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    source_ui = json.loads((source / "artifacts" / "model-ui.json").read_text(encoding="utf-8"))
    if source_audit["status"] != "PASS" or source_audit["mode"] != "replay_not_live":
        raise ValueError("来源必须是通过审计的隔离模型预演")
    if len(source_ui["opportunities"]) != 1 or len(source_ui["topic_proposals"]) != 1:
        raise ValueError("来源必须恰有一个机会和一条主题建议")
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
    result = {
        "mode": "replay_not_live",
        "status": "prepared",
        "run_id": args.run_id,
        "source_run_id": args.source_run,
        "candidate_sha256": source_audit["candidate_sha256"],
        "workspace": str(target_repo),
        "opportunity_id": source_ui["opportunities"][0]["opportunity_id"],
        "proposal_id": source_ui["topic_proposals"][0]["proposal_id"],
    }
    (target / "preparation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
