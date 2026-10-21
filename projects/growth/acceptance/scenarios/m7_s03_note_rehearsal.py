"""Clone an audited S03 rebase chain before saving the exact N1 note."""

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
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if not args.run_id.startswith("m7-s03-note-rehearsal-") or target.exists():
        raise ValueError("需要全新 S03 note rehearsal 运行 ID")
    audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    prepared = json.loads((source / "preparation.json").read_text(encoding="utf-8"))
    ui = json.loads((source / "artifacts" / "rebase-ui.json").read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or audit["cross_candidate"]:
        raise ValueError("来源必须是同候选 S03 rebase 审计通过的预演")
    source_repo = source / "fixture" / "repo"
    target_repo = target / "fixture" / "repo"
    target_repo.parent.mkdir(parents=True)
    shutil.copytree(source_repo, target_repo)
    with sqlite3.connect(source_repo / ".ahadiff" / "growth.sqlite") as origin, sqlite3.connect(
        target_repo / ".ahadiff" / "growth.sqlite"
    ) as cloned:
        origin.backup(cloned)
        cloned.execute("UPDATE bindings SET canonical_local_path=?", (str(target_repo),))
        note_count = cloned.execute(
            "SELECT COUNT(*) FROM engineering_notes WHERE task_id=?", (prepared["task_id"],)
        ).fetchone()[0]
        if note_count:
            raise ValueError("来源卡片已有笔记")
        cloned.commit()
    output = {
        "mode": "replay_not_live", "status": "prepared", "run_id": args.run_id,
        "source_run_id": args.source_run, "candidate_sha256": prepared["candidate_sha256"],
        "workspace": str(target_repo), "account_id": prepared["account_id"],
        "cloud_origin": prepared["cloud_origin"], "task_id": prepared["task_id"],
        "module_id": prepared["module_id"],
        "old_module_index_revision": prepared["module_index_revision"],
        "old_snapshot_id": ui["old_snapshot_id"],
        "new_snapshot_id": ui["new_snapshot"]["snapshot_id"],
        "new_index_revision": ui["new_index"]["index_revision"],
    }
    (target / "preparation.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
