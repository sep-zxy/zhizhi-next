"""Clone the audited S01 cloud chain for isolated installed chat rehearsal."""

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
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s01-chat-rehearsal-"):
        raise ValueError("运行 ID 必须标识为 chat rehearsal")
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
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if digest != audit["candidate_sha256"]:
        raise ValueError("安装包与云链候选版本不一致")
    source_repo = source / "fixture" / "repo"
    target_repo = target / "fixture" / "repo"
    target_repo.parent.mkdir(parents=True)
    shutil.copytree(source_repo, target_repo)
    with sqlite3.connect(source_repo / ".ahadiff" / "growth.sqlite") as origin, sqlite3.connect(
        target_repo / ".ahadiff" / "growth.sqlite",
    ) as cloned:
        origin.backup(cloned)
        cloned.execute("UPDATE bindings SET canonical_local_path=?", (str(target_repo),))
        cloned.commit()
    config = target_repo / ".ahadiff" / "config.toml"
    contents = config.read_text(encoding="utf-8")
    if "base_url = \"http://127.0.0.1:" not in contents:
        raise ValueError("来源 provider 配置不是隔离本机服务")
    lines = [f'base_url = "http://127.0.0.1:{args.port}"' if line.startswith("base_url = ")
             else line for line in contents.splitlines()]
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    preparation = {
        "mode": "replay_not_live", "status": "prepared", "run_id": args.run_id,
        "source_run_id": args.source_run, "candidate_sha256": digest,
        "workspace": str(target_repo), "account_id": ui["account_id"],
        "cloud_origin": ui["cloud_origin"], "task_id": ui["task_id"],
        "note_id": ui["local_chain_after"]["note_id"],
        "attempt_ids": ui["local_chain_after"]["attempt_ids"],
        "provider_name": "local", "provider_port": args.port,
    }
    (target / "preparation.json").write_text(
        json.dumps(preparation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(preparation, ensure_ascii=False))


if __name__ == "__main__":
    main()
