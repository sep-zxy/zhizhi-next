"""Clone S03 feedback and perform the scenario's real feature/main/rebase sequence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

from m7_s01_model_rehearsal import RUNS


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("m7-s03-rebase-rehearsal-"):
        raise ValueError("目标运行 ID 必须标识为 S03 rebase rehearsal")
    source = (RUNS / args.source_run).resolve()
    target = (RUNS / args.run_id).resolve()
    if source.parent != RUNS.resolve() or target.parent != RUNS.resolve():
        raise ValueError("运行目录必须位于 acceptance/runs 下")
    if target.exists():
        raise FileExistsError(target)
    audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    source_prep = json.loads((source / "preparation.json").read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or audit["mode"] != "replay_not_live":
        raise ValueError("来源必须是已审计的 S03 双回答反馈预演")
    installer = args.installer.resolve()
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    source_repo = source / "fixture" / "repo"
    target_repo = target / "fixture" / "repo"
    target_repo.parent.mkdir(parents=True)
    shutil.copytree(source_repo, target_repo)
    with sqlite3.connect(source_repo / ".ahadiff" / "growth.sqlite") as origin, sqlite3.connect(
        target_repo / ".ahadiff" / "growth.sqlite"
    ) as cloned:
        origin.backup(cloned)
        cloned.row_factory = sqlite3.Row
        cloned.execute("UPDATE bindings SET canonical_local_path=?", (str(target_repo),))
        old_ref = cloned.execute(
            "SELECT source_ref_id, blob_hash, content FROM source_refs "
            "WHERE relative_path='src/demo/orders/PriceCache.java'",
        ).fetchone()
        if (old_ref is None
                or hashlib.sha256(old_ref["content"]).hexdigest() != old_ref["blob_hash"]):
            raise ValueError("旧 PriceCache 来源快照不存在或哈希不一致")
        old_source_hash = old_ref["blob_hash"]
        cloned.commit()
    if git(target_repo, "branch", "--show-current") != "feature/price-cache":
        raise ValueError("来源仓库必须在 feature/price-cache")
    before = git(target_repo, "status", "--porcelain")
    if "OrderService.java" not in before or "PriceCache.java" not in before:
        raise ValueError("S03 未提交缓存变更前置缺失")
    git(target_repo, "add", "--", "src/demo/orders/OrderService.java",
        "src/demo/orders/PriceCache.java")
    git(target_repo, "commit", "-m", "增加惰性价格缓存")
    feature_before_rebase = git(target_repo, "rev-parse", "HEAD")
    git(target_repo, "switch", "main")
    controller = target_repo / "src" / "demo" / "orders" / "OrderController.java"
    old_text = controller.read_text(encoding="utf-8")
    marker = "    public int quote"
    if marker not in old_text:
        raise ValueError("Controller 插入注释位置缺失")
    controller.write_text(
        old_text.replace(marker, "    // 订单接口保留原语义。\n" + marker, 1),
        encoding="utf-8",
    )
    git(target_repo, "add", "--", "src/demo/orders/OrderController.java")
    git(target_repo, "commit", "-m", "补充订单控制器说明")
    main_head = git(target_repo, "rev-parse", "HEAD")
    git(target_repo, "switch", "feature/price-cache")
    git(target_repo, "rebase", "main")
    feature_after_rebase = git(target_repo, "rev-parse", "HEAD")
    with sqlite3.connect(target_repo / ".ahadiff" / "growth.sqlite") as db:
        row = db.execute(
            "SELECT blob_hash, content FROM source_refs WHERE source_ref_id=?",
            (old_ref["source_ref_id"],),
        ).fetchone()
        if row is None or hashlib.sha256(row[1]).hexdigest() != old_source_hash:
            raise ValueError("rebase 后旧源码引用发生变化")
    if feature_before_rebase == feature_after_rebase or main_head == feature_after_rebase:
        raise ValueError("rebase 未改变 feature HEAD")
    if git(target_repo, "status", "--porcelain"):
        raise ValueError("rebase 后工作树必须干净")
    prepared = {
        "mode": "replay_not_live", "status": "prepared", "run_id": args.run_id,
        "source_run_id": args.source_run,
        "source_candidate_sha256": source_prep["candidate_sha256"],
        "candidate_sha256": digest,
        "cross_candidate": digest != source_prep["candidate_sha256"],
        "workspace": str(target_repo), "account_id": source_prep["account_id"],
        "cloud_origin": source_prep["cloud_origin"],
        "task_id": source_prep["task_id"], "module_id": source_prep["module_id"],
        "module_index_revision": source_prep["module_index_revision"],
        "old_source_ref_id": old_ref["source_ref_id"],
        "old_source_hash": old_source_hash,
        "feature_before_rebase": feature_before_rebase,
        "main_head": main_head,
        "feature_after_rebase": feature_after_rebase,
        "git_graph": git(target_repo, "log", "--all", "--format=%H %P", "-5"),
    }
    (target / "preparation.json").write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(prepared, ensure_ascii=False))


if __name__ == "__main__":
    main()
