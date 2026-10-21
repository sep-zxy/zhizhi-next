"""Clone audited S03 module state for an isolated installed card rehearsal."""

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
    parser.add_argument("--module-run", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s03-module-card-rehearsal-"):
        raise ValueError("运行 ID 必须标识为 S03 module card rehearsal")
    source = (RUNS / args.source_run).resolve()
    module_run = (RUNS / args.module_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if any(item.parent != RUNS.resolve() for item in (source, module_run, target)):
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    source_result = json.loads((source / "result.json").read_text(encoding="utf-8"))
    module_result = json.loads((module_run / "result.json").read_text(encoding="utf-8"))
    module_ui = json.loads((module_run / "electron-cloud.json").read_text(encoding="utf-8"))
    if manifest["case_id"] != "S03" or source_result["status"] != "BLOCKED":
        raise ValueError("来源必须是部分通过的 S03 前段")
    if not all(source_result["checks"][f"S03-{step:02d}"] for step in range(1, 5)):
        raise ValueError("S03-01 至 S03-04 未全部通过")
    if module_result["status"] != "PASS" or module_result["source_run_id"] != args.source_run:
        raise ValueError("模块锁定来源未通过独立审计")
    installer = args.installer.resolve()
    if not installer.is_file() or installer.parent != (ROOT / "dist" / "desktop").resolve():
        raise ValueError("安装包必须位于当前项目 dist/desktop")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if digest != manifest["candidate_sha256"]:
        raise ValueError("安装包与来源候选版本不一致")
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
        raise ValueError("S03 来源不应已有模型配置")
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
        "source_run_id": args.source_run, "module_run_id": args.module_run,
        "candidate_sha256": digest, "workspace": str(target_repo),
        "account_id": module_ui["account_id"], "cloud_origin": module_ui["cloud_url"],
        "project_id": module_ui["project_id"], "snapshot_id": module_ui["snapshot_id"],
        "module_id": module_ui["module_id"],
        "module_index_revision": module_ui["index_flow"]["index_revision"],
        "provider_name": "local", "provider_port": args.port,
    }
    (target / "preparation.json").write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(prepared, ensure_ascii=False))


if __name__ == "__main__":
    main()
