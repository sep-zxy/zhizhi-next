"""Clone an audited S03 module card for isolated two-answer feedback rehearsal."""

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
    parser.add_argument("--provider-port", type=int, required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s03-feedback-rehearsal-"):
        raise ValueError("目标运行 ID 必须标识为 S03 feedback rehearsal")
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    card = json.loads((source / "artifacts" / "card-ui.json").read_text(encoding="utf-8"))
    source_prep = json.loads((source / "preparation.json").read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or audit["mode"] != "replay_not_live":
        raise ValueError("来源必须是已审计的 S03 模块卡片预演")
    if card["task_id"] != audit["task_id"] or card["module_id"] != source_prep["module_id"]:
        raise ValueError("来源模块卡片身份不一致")
    source_repo = source / "fixture" / "repo"
    target_repo = target / "fixture" / "repo"
    target_repo.parent.mkdir(parents=True)
    shutil.copytree(source_repo, target_repo)
    with sqlite3.connect(source_repo / ".ahadiff" / "growth.sqlite") as origin, sqlite3.connect(
        target_repo / ".ahadiff" / "growth.sqlite"
    ) as cloned:
        origin.backup(cloned)
        cloned.execute("UPDATE bindings SET canonical_local_path=?", (str(target_repo),))
        bindings = cloned.execute("SELECT binding_id FROM bindings").fetchall()
        task = cloned.execute(
            "SELECT source_refs_json, module_id, module_index_revision "
            "FROM growth_tasks WHERE task_id=?", (card["task_id"],)
        ).fetchone()
        attempts = cloned.execute(
            "SELECT COUNT(*) FROM learning_attempts WHERE task_id=?", (card["task_id"],)
        ).fetchone()[0]
        if len(bindings) != 1 or task is None or attempts != 0:
            raise ValueError("隔离模块卡片必须只有一个仓库绑定且尚无回答")
        if (task[1], task[2]) != (
            source_prep["module_id"], source_prep["module_index_revision"]
        ):
            raise ValueError("模块索引修订不一致")
        cloned.commit()
    config = target_repo / ".ahadiff" / "config.toml"
    config.write_text(
        "[providers.local]\n"
        'provider_class = "ollama"\n'
        'model_name = "acceptance-replay-model"\n'
        f'base_url = "http://127.0.0.1:{args.provider_port}"\n'
        'api_key_env = ""\n', encoding="utf-8",
    )
    prepared = {
        "mode": "replay_not_live", "status": "prepared", "run_id": args.run_id,
        "source_run_id": args.source_run,
        "candidate_sha256": audit["candidate_sha256"],
        "workspace": str(target_repo), "task_id": card["task_id"],
        "binding_id": bindings[0][0],
        "module_id": source_prep["module_id"],
        "module_index_revision": source_prep["module_index_revision"],
        "source_ref_ids": json.loads(task[0]),
        "account_id": source_prep["account_id"],
        "cloud_origin": source_prep["cloud_origin"],
        "provider_port": args.provider_port,
    }
    (target / "preparation.json").write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(prepared, ensure_ascii=False))


if __name__ == "__main__":
    main()
