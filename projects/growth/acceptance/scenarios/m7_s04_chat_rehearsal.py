"""Clone an audited S04 old-topic workspace for installed chat rehearsal."""

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
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--source-local-run", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s04-chat-rehearsal-"):
        raise ValueError("运行 ID 必须标识为 S04 chat rehearsal")
    target = (RUNS / args.run_id).resolve()
    source = (RUNS / args.source_run).resolve()
    local_run = (RUNS / args.source_local_run).resolve()
    if any(item.parent != RUNS.resolve() for item in (target, source, local_run)):
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    local_audit = json.loads((local_run / "result.json").read_text(encoding="utf-8"))
    local_ui = json.loads((local_run / "electron-s04.json").read_text(encoding="utf-8"))
    if local_audit["status"] != "PASS" or local_audit["source_run_id"] != args.source_run:
        raise ValueError("来源旧主题安装版前置未通过审计")
    installer = args.installer.resolve()
    if not installer.is_file() or installer.parent != (ROOT / "dist" / "desktop").resolve():
        raise ValueError("安装包必须位于当前项目 dist/desktop")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if digest != local_audit["candidate_sha256"]:
        raise ValueError("安装包与 S04 来源候选不一致")
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
    if config.exists():
        raise ValueError("S04 来源不应已有模型配置")
    config.write_text(
        "[providers.local]\n"
        'provider_class = "ollama"\n'
        'model_name = "acceptance-replay-model"\n'
        f'base_url = "http://127.0.0.1:{args.port}"\n'
        'api_key_env = ""\n',
        encoding="utf-8",
    )
    prepared = {
        "mode": "replay_not_live", "status": "prepared", "run_id": args.run_id,
        "source_run_id": args.source_run, "source_local_run_id": args.source_local_run,
        "candidate_sha256": digest, "workspace": str(target_repo),
        "account_id": local_ui["account_id"], "cloud_origin": local_ui["cloud_url"],
        "old_topic_id": local_ui["topic_id"], "old_note_id": local_ui["note_id"],
        "provider_name": "local", "provider_port": args.port,
    }
    (target / "preparation.json").write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(prepared, ensure_ascii=False))


if __name__ == "__main__":
    main()
