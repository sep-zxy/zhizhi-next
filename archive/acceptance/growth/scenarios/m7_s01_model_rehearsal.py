"""Clone a completed S01 snapshot into an isolated replay-only model workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "acceptance" / "runs"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s01-model-rehearsal-"):
        raise ValueError("隔离运行 ID 必须标识为 model rehearsal")
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8-sig"))
    if manifest["case_id"] != "S01" or manifest["mode"] != "live":
        raise ValueError("来源必须是 S01 正式快照运行")
    installer = ROOT / "dist" / "desktop" / "工程成长伴侣 Setup 0.1.0.exe"
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if digest != manifest["candidate_sha256"]:
        raise ValueError("安装包与来源快照候选版本不一致")
    source_repo = source / "fixture" / "repo"
    source_db = source_repo / ".ahadiff" / "growth.sqlite"
    if not (source_repo / ".git").exists() or not source_db.exists():
        raise ValueError("来源仓库或业务账本缺失")
    target_repo = target / "fixture" / "repo"
    target_repo.parent.mkdir(parents=True)
    shutil.copytree(source_repo, target_repo)
    db_path = target_repo / ".ahadiff" / "growth.sqlite"
    with sqlite3.connect(source_db) as origin, sqlite3.connect(db_path) as cloned:
        origin.backup(cloned)
        binding_count = cloned.execute("SELECT COUNT(*) FROM bindings").fetchone()[0]
        if binding_count != 1:
            raise ValueError("隔离 S01 应只有一个仓库绑定")
        cloned.execute("UPDATE bindings SET canonical_local_path=?", (str(target_repo),))
        cloned.commit()
    config = target_repo / ".ahadiff" / "config.toml"
    if config.exists():
        raise ValueError("预演不应覆盖现有 provider 配置")
    config.write_text(
        "[providers.local]\n"
        'provider_class = "ollama"\n'
        'model_name = "acceptance-replay-model"\n'
        f'base_url = "http://127.0.0.1:{args.port}"\n'
        'api_key_env = ""\n',
        encoding="utf-8",
    )
    result = {
        "mode": "replay_not_live",
        "status": "prepared",
        "source_run_id": args.source_run,
        "run_id": args.run_id,
        "candidate_sha256": digest,
        "workspace": str(target_repo),
        "provider_name": "local",
        "provider_port": args.port,
    }
    (target / "preparation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
