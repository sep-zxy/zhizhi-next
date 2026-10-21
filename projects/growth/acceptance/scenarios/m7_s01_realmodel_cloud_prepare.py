"""Clone a formally reviewed real-model S01 chain for isolated local cloud rehearsal."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "runs"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s01-local-cloud-realmodel-"):
        raise ValueError("目标运行必须标识为本机云预演")
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    manifest = read(source / "manifest.json")
    result = read(source / "result.json")
    model = read(source / "artifacts/s01-04-deepseek-ui.json")
    card = read(source / "artifacts/s01-card-live-ui.json")
    feedback = read(source / "artifacts/s01-feedback-live-ui.json")
    steps = [json.loads(line) for line in (source / "steps.jsonl").read_text(
        encoding="utf-8-sig"
    ).splitlines() if line.strip()]
    if result["status"] != "BLOCKED" or [step["step_id"] for step in steps] != [
        f"S01-{index:02d}" for index in range(1, 9)
    ] or not all(step["status"] == "PASS" for step in steps):
        raise ValueError("来源不是已审阅的正式 S01 前八步")
    if model["mode"] != card["mode"] != feedback["mode"] != "live_observation":
        raise ValueError("来源模型、卡片或反馈不是 live 观察")
    if model["preview"]["model_name"] != "deepseek-flash" or len(
        feedback["turns"]
    ) != 2 or card["task_id"] != feedback["task_id"]:
        raise ValueError("真实模型学习链不完整")
    source_repo = source / "fixture/repo"
    target_repo = target / "fixture/repo"
    target_repo.parent.mkdir(parents=True)
    shutil.copytree(source_repo, target_repo)
    with sqlite3.connect(source_repo / ".ahadiff/growth.sqlite") as origin, sqlite3.connect(
        target_repo / ".ahadiff/growth.sqlite"
    ) as cloned:
        origin.backup(cloned)
        cloned.execute("UPDATE bindings SET canonical_local_path=?", (str(target_repo),))
        cloned.commit()
    preparation = {
        "mode": "replay_not_live",
        "source_model": "real_deepseek",
        "cloud_kind": "isolated_local_postgresql",
        "run_id": args.run_id,
        "source_run_id": args.source_run,
        "candidate_sha256": manifest["candidate_sha256"],
        "executable_sha256": card["executable_sha256"],
        "workspace": str(target_repo),
        "task_id": card["task_id"],
        "opportunity_id": card["opportunity_id"],
        "attempt_ids": [turn["attempt_id"] for turn in feedback["turns"]],
        "note_id": card["note_id"],
    }
    (target / "preparation.json").write_text(
        json.dumps(preparation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"run_id": args.run_id, "status": "prepared"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
