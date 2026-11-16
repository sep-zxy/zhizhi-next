"""Exercise the pinned AhaDiff Git capture on a disposable, real repository."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ahadiff.core.orchestrator import LearnRequest, run_learn_pipeline


ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "acceptance" / "runs"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def main() -> None:
    started_at = utc_now()
    run_id = f"m0-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = RUNS / run_id
    repo = run_dir / "fixture" / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "情境验证")
    git(repo, "config", "user.email", "scenario@example.invalid")
    source = repo / "route.py"
    write_text(source, "def route_count(routes):\n    return len(routes)\n")
    git(repo, "add", "route.py")
    git(repo, "commit", "-m", "建立路线查询基线")
    base_sha = git(repo, "rev-parse", "HEAD")
    write_text(
        source,
        "from concurrent.futures import ThreadPoolExecutor\n\n"
        "def route_count(routes):\n"
        "    with ThreadPoolExecutor(max_workers=4) as pool:\n"
        "        return sum(pool.map(lambda route: len(route), routes))\n",
    )
    git(repo, "add", "route.py")
    git(repo, "commit", "-m", "增加并发路线统计")
    head_sha = git(repo, "rev-parse", "HEAD")
    before_source_hash = sha256(source)

    result = run_learn_pipeline(LearnRequest(workspace_root=repo, last=True, dry_run=True))
    artifacts = repo / ".ahadiff" / "runs" / result.run_id
    metadata = json.loads((artifacts / "metadata.json").read_text(encoding="utf-8"))
    after = json.loads((artifacts / "after_text_by_path.json").read_text(encoding="utf-8"))
    checks = {
        "dry_run_status": result.status == "dry_run",
        "source_ref_is_head": metadata.get("source_ref") == head_sha,
        "changed_file_captured": "ThreadPoolExecutor" in json.dumps(after),
        "source_unchanged": sha256(source) == before_source_hash,
        "git_worktree_unchanged": git(repo, "status", "--porcelain", "--untracked-files=no") == "",
    }
    # The bundle preserves actual commits without nesting a Git repository in the product repo.
    bundle = run_dir / "fixture" / "fixture.bundle"
    git(repo, "bundle", "create", str(bundle), "--all")
    evidence_dir = run_dir / "artifacts" / "ahadiff"
    evidence_dir.mkdir(parents=True)
    files = {}
    for name in (
        "metadata.json",
        "artifact_set.json",
        "before_text_by_path.json",
        "after_text_by_path.json",
        "evidence_anchors.json",
        "patch.diff",
    ):
        target = evidence_dir / name
        shutil.copy2(artifacts / name, target)
        files[name] = sha256(target)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "stage": "M0",
        "mode": "dry_run",
        "started_at": started_at,
        "finished_at": utc_now(),
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
        },
        "fixture": {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "bundle_sha256": sha256(bundle),
        },
        "ahadiff": {"run_id": result.run_id, "status": result.status},
        "checks": checks,
        "artifact_sha256": files,
        "check_status": "PASS" if all(checks.values()) else "FAIL",
        "stage_status": "BLOCKED",
        "stage_blockers": ["macOS 实机与安装验证", "live 模型生成", "双平台 Python sidecar"],
    }
    write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write_text(
        run_dir / "report.md",
        f"# M0 Windows Git 捕获情境\n\n"
        f"- 运行 ID：`{run_id}`\n"
        f"- Git 基线：`{base_sha}`；变更：`{head_sha}`\n"
        f"- AhaDiff run：`{result.run_id}`，模式：`dry_run`\n"
        f"- 检查：`{manifest['check_status']}`；M0 阶段：`BLOCKED`\n\n"
        "此运行验证真实 Git 提交可生成差异、源码版本和来源锚点；"
        "没有调用模型，也没有证明桌面双平台或完整学习产物。\n",
    )
    print(run_dir)
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
