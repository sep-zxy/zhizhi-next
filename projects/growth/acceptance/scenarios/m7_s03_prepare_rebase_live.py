"""Perform S03's real fixture Git commits and rebase in the formal run."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo), *args], check=True,
                               capture_output=True, text=True, encoding="utf-8")
    return completed.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    repo = run / "fixture/repo"
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    chain = json.loads((run / "chain.json").read_text(encoding="utf-8"))
    source = json.loads((run / "artifacts/s03-06-preparation.json").read_text(encoding="utf-8"))
    target = run / "artifacts/s03-07-git-preparation.json"
    if target.exists() or not result["checks"]["S03-06"]:
        raise AssertionError("S03-06 未通过或 Git rebase 已准备")
    digest = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    if digest != manifest["candidate_sha256"] or source["candidate_sha256"] != digest:
        raise AssertionError("S03 安装包候选不一致")
    if git(repo, "branch", "--show-current") != "feature/price-cache":
        raise AssertionError("fixture 不在 feature/price-cache")
    before = git(repo, "status", "--porcelain")
    if "OrderService.java" not in before or "PriceCache.java" not in before:
        raise AssertionError("未提交缓存变更缺失")
    with sqlite3.connect(repo / ".ahadiff/growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        old_ref = db.execute("SELECT source_ref_id,blob_hash,content FROM source_refs "
                             "WHERE snapshot_id=? AND relative_path='src/demo/orders/PriceCache.java'",
                             (chain["snapshot_id"],)).fetchone()
        if old_ref is None or hashlib.sha256(old_ref["content"]).hexdigest() != old_ref["blob_hash"]:
            raise AssertionError("旧 PriceCache 来源不完整")
        old_id, old_hash = old_ref["source_ref_id"], old_ref["blob_hash"]
    git(repo, "add", "--", "src/demo/orders/OrderService.java", "src/demo/orders/PriceCache.java")
    git(repo, "commit", "-m", "增加惰性价格缓存")
    feature_before = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "main")
    controller = repo / "src/demo/orders/OrderController.java"
    original = controller.read_text(encoding="utf-8")
    marker = "    public int quote"
    if marker not in original or "订单接口保留原语义" in original:
        raise AssertionError("Controller 注释插入位置异常")
    controller.write_text(original.replace(marker, "    // 订单接口保留原语义。\n" + marker, 1),
                          encoding="utf-8")
    git(repo, "add", "--", "src/demo/orders/OrderController.java")
    git(repo, "commit", "-m", "补充订单控制器说明")
    main_head = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "feature/price-cache")
    git(repo, "rebase", "main")
    feature_after = git(repo, "rev-parse", "HEAD")
    if feature_after in (feature_before, main_head) or git(repo, "status", "--porcelain"):
        raise AssertionError("rebase 没有产生新 HEAD 或工作树不干净")
    with sqlite3.connect(repo / ".ahadiff/growth.sqlite") as db:
        old = db.execute("SELECT blob_hash,content FROM source_refs WHERE source_ref_id=?", (old_id,)).fetchone()
        if old is None or old[0] != old_hash or hashlib.sha256(old[1]).hexdigest() != old_hash:
            raise AssertionError("rebase 后旧来源引用发生变化")
    prepared = {"mode": "live_observation", "status": "prepared", "run_id": run.name,
                "candidate_sha256": digest, "workspace": str(repo),
                "account_id": source["account_id"], "cloud_origin": source["cloud_origin"],
                "task_id": chain["task_id"], "module_id": chain["module_id"],
                "module_index_revision": source["module_index_revision"],
                "old_source_ref_id": old_id, "old_source_hash": old_hash,
                "feature_before_rebase": feature_before, "main_head": main_head,
                "feature_after_rebase": feature_after,
                "git_graph": git(repo, "log", "--all", "--format=%H %P", "-5")}
    target.write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"S03_REBASE_PREPARED {feature_before[:12]} {main_head[:12]} {feature_after[:12]}")


if __name__ == "__main__":
    main()
