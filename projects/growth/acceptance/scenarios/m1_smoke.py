"""Exercise stable growth IDs with staged, unstaged and selected new source."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch as mock_patch

from ahadiff.growth.local import GrowthLocalRepository

ROOT = Path(__file__).resolve().parents[2]


def now() -> str:
    return datetime.now(UTC).isoformat()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def save(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def main() -> None:
    run_id = f"m1-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    repo = run_dir / "fixture" / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "情境验证")
    git(repo, "config", "user.email", "scenario@example.invalid")
    # Windows checkout may rewrite unchanged text to CRLF. It must not become
    # a phantom learning source.
    git(repo, "config", "core.autocrlf", "true")
    source = repo / "module.py"
    save(source, "def total(values):\n    return sum(values)\n")
    save(repo / ".gitignore", ".ahadiff/\n.codegraph/\n")
    git(repo, "add", "module.py", ".gitignore")
    git(repo, "commit", "-m", "建立求和基线")
    base_sha = git(repo, "rev-parse", "HEAD")
    other_worktree = run_dir / "fixture" / "worktree-other"
    git(repo, "worktree", "add", "-b", "feature/other", str(other_worktree), "main")
    git(repo, "switch", "-c", "feature/parallel-total")
    save(
        source,
        "from concurrent.futures import ThreadPoolExecutor\n\n"
        "def total(values):\n"
        "    with ThreadPoolExecutor(max_workers=2) as pool:\n"
        "        return sum(pool.map(int, values))\n",
    )
    git(repo, "add", "module.py")
    save(
        source,
        "from concurrent.futures import ThreadPoolExecutor\n\n"
        "def total(values):\n"
        "    with ThreadPoolExecutor(max_workers=4) as pool:\n"
        "        return sum(pool.map(int, values))\n",
    )
    save(repo / "helper.py", "def normalize(value):\n    return int(value)\n")
    original_status = git(repo, "status", "--porcelain", "--untracked-files=all")
    original_source = hashlib.sha256(source.read_bytes()).hexdigest()
    started_at = now()
    db_path = run_dir / "local.sqlite"
    trace_id = str(uuid.uuid4())
    with GrowthLocalRepository(db_path) as ledger:
        project_id = ledger.create_project("并发求和", trace_id=trace_id)
        binding_id = ledger.bind_repo(project_id, repo, trace_id=trace_id)
        other_binding_id = ledger.bind_repo(project_id, other_worktree, trace_id=trace_id)
        worktree_ids = [
            row[0]
            for row in ledger.connection.execute(
                "SELECT worktree_id FROM bindings WHERE project_id=? ORDER BY binding_id",
                (project_id,),
            )
        ]
        feature_id = ledger.start_feature(
            project_id, binding_id, "并发求和", "main", trace_id=trace_id
        )
        snapshot_id = ledger.capture_snapshot(
            feature_id, binding_id, trace_id=trace_id, selected_untracked={"helper.py"}
        )
        repeated_snapshot_id = ledger.capture_snapshot(
            feature_id, binding_id, trace_id=trace_id, selected_untracked={"helper.py"}
        )
        try:
            analysis_id = ledger.analyze_snapshot(
                snapshot_id, binding_id, "first-capture", trace_id=trace_id
            )
        except Exception as exc:
            failed_chain = ledger.export_chain(feature_id)
            save(
                run_dir / "chain.json",
                json.dumps(failed_chain, ensure_ascii=False, indent=2) + "\n",
            )
            save(
                run_dir / "events.jsonl",
                "".join(
                    json.dumps(event, ensure_ascii=False) + "\n" for event in failed_chain["events"]
                ),
            )
            failure = {
                "run_id": run_id,
                "stage": "M1",
                "mode": "dry_run",
                "started_at": started_at,
                "finished_at": now(),
                "feature_id": feature_id,
                "snapshot_id": snapshot_id,
                "check_status": "FAIL",
                "stage_status": "BLOCKED",
                "error": str(exc),
            }
            save(
                run_dir / "manifest.json", json.dumps(failure, ensure_ascii=False, indent=2) + "\n"
            )
            save(
                run_dir / "report.md",
                f"# M1 本地链路失败\n\n运行 `{run_id}` 在 AhaDiff 分析阶段失败："
                f"`{exc}`。实际事件和快照保存在 chain.json/events.jsonl；"
                "此运行保持 FAIL，修复后使用新 run_id。\n",
            )
            print(run_dir)
            raise
        repeated_analysis_id = ledger.analyze_snapshot(
            snapshot_id, binding_id, "first-capture", trace_id=trace_id
        )
        with mock_patch(
            "ahadiff.growth.local.run_learn_pipeline",
            side_effect=RuntimeError("注入一次产物发布失败"),
        ):
            try:
                ledger.analyze_snapshot(
                    snapshot_id, binding_id, "retry-capture",
                    trace_id=trace_id, reanalysis=True,
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError("故障注入没有使分析失败")
        failed_analysis_id = ledger.connection.execute(
            "SELECT analysis_id FROM analysis_runs WHERE snapshot_id=? AND request_id=?",
            (snapshot_id, "retry-capture"),
        ).fetchone()[0]
        recovered_analysis_id = ledger.retry_analysis(
            failed_analysis_id, binding_id, trace_id=trace_id
        )
        repeated_recovered_id = ledger.analyze_snapshot(
            snapshot_id, binding_id, "retry-capture", trace_id=trace_id
        )
        first_chain = ledger.export_chain(feature_id)

    with GrowthLocalRepository(db_path) as reopened:
        chain = reopened.export_chain(feature_id)
        source_rows = reopened.connection.execute(
            "SELECT relative_path, content, blob_hash FROM source_refs WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchall()
        patch = reopened.connection.execute(
            "SELECT patch_text FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()[0]
    checks = {
        "feature_base_is_real_commit": chain["feature"]["start_base_sha"] == base_sha,
        "linked_worktree_ids_distinct": len(set(worktree_ids)) == 2
        and other_binding_id != binding_id,
        "snapshot_id_reused": repeated_snapshot_id == snapshot_id,
        "analysis_id_reused": repeated_analysis_id == analysis_id,
        "ids_survive_restart": first_chain["snapshot_ids"] == chain["snapshot_ids"],
        "staged_and_unstaged_not_doubled": patch.count("max_workers=4") == 1,
        "staged_intermediate_excluded": "max_workers=2" not in patch,
        "selected_untracked_included": {row[0] for row in source_rows}
        == {"module.py", "helper.py"},
        "source_bytes_preserved": next(row[1] for row in source_rows if row[0] == "module.py")
        == source.read_bytes(),
        "source_hashes_match": all(
            hashlib.sha256(row[1]).hexdigest() == row[2] for row in source_rows
        ),
        "git_status_unchanged": git(repo, "status", "--porcelain", "--untracked-files=all")
        == original_status,
        "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == original_source,
        "upstream_dry_run_linked": all(
            row["upstream_run_id"] is not None for row in chain["analysis_runs"]
        ),
        "retry_keeps_analysis_id": failed_analysis_id
        == recovered_analysis_id
        == repeated_recovered_id,
        "failed_attempt_preserved": sorted(
            (row["attempt_no"], row["status"])
            for row in chain["analysis_attempts"]
            if row["analysis_id"] == failed_analysis_id
        )
        == [(1, "failed"), (2, "succeeded")],
        "events_linked": len(chain["events"]) >= 6,
    }
    artifact_dir = run_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    # Preserve AhaDiff's exact patch bytes; text-mode newline conversion on
    # Windows would turn CRLF source lines into CRCRLF in the evidence copy.
    (artifact_dir / "source.diff").write_bytes(patch.encode("utf-8"))
    (artifact_dir / "module.py").write_bytes(source.read_bytes())
    (artifact_dir / "helper.py").write_bytes((repo / "helper.py").read_bytes())
    artifact_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in artifact_dir.iterdir()
        if path.is_file()
    }
    git(repo, "bundle", "create", str(run_dir / "fixture" / "fixture.bundle"), "--all")
    save(run_dir / "chain.json", json.dumps(chain, ensure_ascii=False, indent=2) + "\n")
    save(
        run_dir / "events.jsonl",
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in chain["events"]),
    )
    manifest = {
        "run_id": run_id,
        "stage": "M1",
        "mode": "dry_run",
        "started_at": started_at,
        "finished_at": now(),
        "base_sha": base_sha,
        "project_id": project_id,
        "feature_id": feature_id,
        "snapshot_id": snapshot_id,
        "analysis_id": analysis_id,
        "recovered_analysis_id": recovered_analysis_id,
        "artifact_sha256": artifact_hashes,
        "checks": checks,
        "check_status": "PASS" if all(checks.values()) else "FAIL",
        "stage_status": "BLOCKED",
        "stage_blockers": ["M0 双平台和 live 模型未完成", "云主库与同步未配置"],
    }
    save(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    save(
        run_dir / "report.md",
        f"# M1 Windows 本地成长链路\n\n"
        f"真实基线 `{base_sha}`；feature `{feature_id}`；快照 `{snapshot_id}`；"
        f"分析 `{analysis_id}`。\n\n"
        "两个真实 linked worktree 绑定同一逻辑项目且本机 worktree_id 不同。"
        "暂存版使用 2 个 worker，未暂存修改改为 4 个；"
        "有效快照只包含最终工作树内容，用户明确选择的新文件也被捕获。"
        "再次捕获、同请求分析及 SQLite 重开均保留原 ID。"
        "注入一次产物发布失败后，以同一分析 ID 显式重试成功，失败 attempt 仍保留。"
        "实际差异和最终源码见 `artifacts/`，其 SHA-256 见 manifest。"
        "AhaDiff 仅运行 dry_run，未调用 live 模型。\n\n"
        f"本地检查：**{manifest['check_status']}**；完整 M1 退出：**BLOCKED**。\n",
    )
    print(run_dir)
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
