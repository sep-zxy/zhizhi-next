"""Clone S03 N1/A1/A2 after real ReMe recovery for an installed chat rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from m7_s01_model_rehearsal import ROOT, RUNS


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if not args.run_id.startswith("m7-s03-chat-rehearsal-") or target.exists():
        raise ValueError("需要全新 S03 chat rehearsal 运行 ID")
    prep = load(source / "preparation.json")
    ui = load(source / "artifacts" / "note-ui.json")
    outage = load(source / "outage-audit.json")
    recovery = load(source / "recovery-audit.json")
    if outage["status"] != recovery["status"] != "PASS":
        raise ValueError("来源 N1/ReMe 停机与恢复未通过")
    installer = args.installer.resolve()
    if not installer.is_file() or installer.parent != (ROOT / "dist" / "desktop").resolve():
        raise ValueError("安装包必须位于当前项目 dist/desktop")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if digest != prep["candidate_sha256"]:
        raise ValueError("安装包与 S03 来源候选不一致")
    source_repo = source / "fixture" / "repo"
    target_repo = target / "fixture" / "repo"
    target_repo.parent.mkdir(parents=True)
    shutil.copytree(source_repo, target_repo)
    with sqlite3.connect(source_repo / ".ahadiff" / "growth.sqlite") as origin, sqlite3.connect(
        target_repo / ".ahadiff" / "growth.sqlite"
    ) as cloned:
        origin.backup(cloned)
        cloned.execute("UPDATE bindings SET canonical_local_path=?", (str(target_repo),))
        attempts = cloned.execute(
            "SELECT attempt_id FROM learning_attempts WHERE task_id=? "
            "ORDER BY created_at,rowid", (prep["task_id"],),
        ).fetchall()
        if len(attempts) != 2:
            raise ValueError("A1/A2 来源不完整")
        cloned.commit()
    config = target_repo / ".ahadiff" / "config.toml"
    contents = config.read_text(encoding="utf-8")
    lines = [
        f'base_url = "http://127.0.0.1:{args.port}"'
        if line.startswith("base_url = ") else line
        for line in contents.splitlines()
    ]
    if lines == contents.splitlines():
        raise ValueError("来源模型配置缺少本机 provider")
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output = {
        "mode": "replay_not_live", "status": "prepared", "run_id": args.run_id,
        "source_run_id": args.source_run, "candidate_sha256": digest,
        "workspace": str(target_repo), "account_id": prep["account_id"],
        "cloud_origin": prep["cloud_origin"], "task_id": prep["task_id"],
        "note_id": ui["note_id"], "attempt_ids": [item[0] for item in attempts],
        "old_snapshot_id": prep["old_snapshot_id"],
        "provider_name": "local", "provider_port": args.port,
    }
    (target / "preparation.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
