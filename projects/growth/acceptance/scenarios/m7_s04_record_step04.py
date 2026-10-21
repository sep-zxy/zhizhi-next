"""Record S04's real Git owner check and before/after behavior probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from m7_s04_record_step02 import artifact, read, write


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read(run / "manifest.json")
    chain = read(run / "chain.json")
    code_path = run / "artifacts" / "code-result.json"
    baseline_path = run / "artifacts" / "code-probe-baseline.json"
    target_path = run / "artifacts" / "code-probe-target.json"
    patch_path = run / "artifacts" / "code-feature.diff"
    result = read(code_path)
    baseline, target = read(baseline_path), read(target_path)
    steps = [json.loads(line) for line in (run / "steps.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    digest = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    if manifest["case_id"] != "S04" or manifest["candidate_sha256"] != digest:
        raise AssertionError("安装包与 S04 候选不一致")
    if [item["step_id"] for item in steps] != ["S04-01", "S04-02", "S04-03"] \
            or any(item["status"] != "PASS" for item in steps):
        raise AssertionError("S04-01/02/03 必须先连续通过")
    repo = run / "fixture" / "repo"
    if result["mode"] != "live_formal_windows" or result["status"] != "PASS" \
            or result["source_run_id"] != run.name or result["chat_run_id"] != run.name:
        raise AssertionError("代码 Probe 不是同一正式 S04 运行")
    checks = {
        "same_candidate_and_repository": result["candidate_sha256"] == digest
        and Path(result["workspace"]).resolve() == repo.resolve()
        and Path(git(repo, "rev-parse", "--show-toplevel")).resolve() == repo.resolve(),
        "real_git_commit": result["base_sha"] == chain["base_sha"]
        == manifest["fixture"]["base_sha"]
        and result["head_sha"] == git(repo, "rev-parse", "HEAD")
        and result["base_sha"] != result["head_sha"]
        and not git(repo, "status", "--porcelain"),
        "source_matches_scenario": all(
            hashlib.sha256((repo / relative).read_bytes()).hexdigest()
            == manifest["fixture"]["next_file_sha256"][relative]
            for relative in ("src/access.ts", "src/document.ts")),
        "baseline_bob_can_edit": baseline["bob"]["status"] == "OK"
        and baseline["bob"]["content"] == "new",
        "target_bob_forbidden": target["bob"]["status"] == "FORBIDDEN",
        "alice_and_anonymous_stable": baseline["alice"]["status"] == "OK"
        and target["alice"]["status"] == "OK"
        and baseline["anonymous"]["status"] == "UNAUTHENTICATED"
        and target["anonymous"]["status"] == "UNAUTHENTICATED",
        "patch_contains_owner_check": "canEdit" in patch_path.read_text(encoding="utf-8")
        and "ownerId" in patch_path.read_text(encoding="utf-8"),
    }
    if not all(checks.values()):
        raise AssertionError(checks)
    started = baseline_path.stat().st_ctime
    finished = code_path.stat().st_mtime
    record = {
        "schema_version": 1, "run_id": manifest["run_id"], "case_id": "S04",
        "step_id": "S04-04", "status": "PASS",
        "started_at": datetime.fromtimestamp(started, UTC).isoformat(),
        "finished_at": datetime.fromtimestamp(finished, UTC).isoformat(),
        "device_id": steps[0]["device_id"],
        "trace_id": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   f"growth-git-observation:{result['head_sha']}")),
        "action": "在同一 Git 仓库新增所有者检查并修改调用，提交后运行前后 Probe",
        "expected": "Bob 从可修改 Alice 文档变为 FORBIDDEN；Alice 与匿名行为不退化",
        "actual": {"base_sha": result["base_sha"], "head_sha": result["head_sha"],
                   "checks": checks},
        "artifacts": [artifact(run, path) for path in
                      (code_path, baseline_path, target_path, patch_path)],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    chain["head_sha"] = result["head_sha"]
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\n\n## S04-04 同仓库权限修复\n\n"
                     "实际 Git 提交增加资源所有者检查；基线 Bob 可修改 Alice 文档，"
                     "改动后 Bob 得到 FORBIDDEN，Alice 和匿名行为保持原预期。"
                     "提交、源码哈希、diff 与两次 Probe 见 `artifacts/code-*`。\n")
    print(f"S04-04_PASS {result['head_sha']}")


if __name__ == "__main__":
    main()
