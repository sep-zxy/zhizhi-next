"""Clone the audited S01 cloud ledger for an installed review UI rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from m7_s01_model_rehearsal import ROOT, RUNS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s01-review-rehearsal-"):
        raise ValueError("目标运行 ID 必须标识为 review rehearsal")
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    recovered = json.loads((source / "reme-recovery.json").read_text(encoding="utf-8"))
    ui = json.loads((source / "artifacts" / "cloud-ui.json").read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or recovered["status"] != "PASS":
        raise ValueError("来源云链或 ReMe 未通过审计")
    if audit["mode"] != "replay_not_live" or ui["mode"] != "replay_not_live":
        raise ValueError("来源必须是隔离回放")
    installer = args.installer.resolve()
    if not installer.is_file() or installer.parent != (ROOT / "dist" / "desktop").resolve():
        raise ValueError("安装包必须位于当前项目 dist/desktop")
    target_repo = target / "fixture" / "repo"
    target_repo.parent.mkdir(parents=True)
    source_repo = source / "fixture" / "repo"
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
        "source_candidate_sha256": audit["candidate_sha256"],
        "review_candidate_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "workspace": str(target_repo),
        "account_id": ui["account_id"],
        "cloud_origin": ui["cloud_origin"],
        "task_id": ui["task_id"],
        "note_id": ui["local_chain_after"]["note_id"],
    }
    (target / "preparation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
