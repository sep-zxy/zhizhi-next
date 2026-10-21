"""Drive the S01 local learning data path with explicit replay model outputs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ahadiff.growth.learning import FeedbackDraft, GrowthLearningService, OpportunityBatch
from ahadiff.growth.local import GrowthLocalRepository

ROOT = Path(__file__).resolve().parents[2]
BASE_SOURCE = """package demo.route;
import java.util.concurrent.Executor;
import java.util.function.Function;
public final class RoutePlanner {
    public String plan(Function<String, String> query, Executor executor) {
        String a = query.apply("A");
        String b = query.apply("B");
        return a + "|" + b;
    }
}
"""
CHANGED_SOURCE = """package demo.route;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.function.Function;
public final class RoutePlanner {
    public String plan(Function<String, String> query, Executor executor) {
        CompletableFuture<String> a = CompletableFuture.supplyAsync(
            () -> query.apply("A"), executor);
        CompletableFuture<String> b = CompletableFuture.supplyAsync(
            () -> query.apply("B"), executor);
        return a.thenCombine(b, (left, right) -> left + "|" + right).join();
    }
}
"""
PROBE_SOURCE = """import demo.route.RoutePlanner;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Function;
public final class RouteProbe {
    public static void main(String[] args) {
        AtomicInteger next = new AtomicInteger();
        ExecutorService executor = Executors.newFixedThreadPool(2, job -> {
            Thread thread = new Thread(job);
            thread.setName("route-worker-" + next.incrementAndGet());
            return thread;
        });
        try {
            Function<String, String> query = value -> {
                System.out.println("QUERY|" + value + "|" + Thread.currentThread().getName());
                return value;
            };
            System.out.println("RESULT=" + new RoutePlanner().plan(query, executor));
        } finally {
            executor.shutdown();
        }
    }
}
"""
A1 = (
    "CompletableFuture 每调用一次 supplyAsync 就新建一个线程，"
    "join 只是把两个结果放到一起，不会等待。"
)
A2 = (
    "两个查询任务交给传入的 executor，线程是不是新建由执行器决定。"
    "thenCombine 组合结果，最后 join 会等待组合结果，调用 plan 的线程仍可能被阻塞。"
)
N1 = (
    "我以前把 CompletableFuture 当成“每个任务自动新建线程”。"
    "这次代码把两个查询交给同一个 executor，实际线程由执行器安排。"
    "thenCombine 组合结果，join 会等待，所以这段代码并不意味着调用方从头到尾都不阻塞。"
    "以后排查并发问题，我要先找执行器在哪里配置。"
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def save(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.encode("utf-8"))


def dump(path: Path, value: Any) -> None:
    save(path, json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def probe(repo: Path) -> str:
    classes = repo / ".classes"
    classes.mkdir(exist_ok=True)
    subprocess.run(
        [
            "javac",
            "-encoding",
            "UTF-8",
            "-d",
            str(classes),
            str(repo / "src" / "demo" / "route" / "RoutePlanner.java"),
            str(repo / "RouteProbe.java"),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return subprocess.run(
        ["java", "-cp", str(classes), "RouteProbe"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def main() -> None:
    run_id = f"m2-replay-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    repo = run_dir / "fixture" / "route-demo"
    repo.mkdir(parents=True)
    started_at = now()
    steps: list[dict[str, Any]] = []
    try:
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "情境验证")
        git(repo, "config", "user.email", "scenario@example.invalid")
        git(repo, "config", "core.autocrlf", "false")
        source = repo / "src" / "demo" / "route" / "RoutePlanner.java"
        save(source, BASE_SOURCE)
        save(repo / "RouteProbe.java", PROBE_SOURCE)
        save(repo / ".gitignore", ".ahadiff/\n.classes/\n.codegraph/\n")
        git(repo, "add", "src", "RouteProbe.java", ".gitignore")
        git(repo, "commit", "-m", "建立路线查询模拟基线")
        base_sha = git(repo, "rev-parse", "HEAD")
        baseline_output = probe(repo)
        steps.append(
            {"step": "S01-01", "time": now(), "git_sha": base_sha, "probe": baseline_output}
        )
        git(repo, "switch", "-c", "feature/parallel-route")
        save(source, CHANGED_SOURCE)
        git(repo, "add", "src/demo/route/RoutePlanner.java")
        git(repo, "commit", "-m", "将路线查询改为显式执行器并发调用")
        head_sha = git(repo, "rev-parse", "HEAD")
        changed_output = probe(repo)
        original_status = git(repo, "status", "--porcelain", "--untracked-files=all")
        steps.append(
            {"step": "S01-02", "time": now(), "git_sha": head_sha, "probe": changed_output}
        )

        trace_id = str(uuid.uuid4())
        with GrowthLocalRepository(run_dir / "local.sqlite") as ledger:
            project_id = ledger.create_project("route-demo", trace_id=trace_id)
            binding_id = ledger.bind_repo(project_id, repo, trace_id=trace_id)
            feature_id = ledger.start_feature(
                project_id, binding_id, "parallel-route", "main", trace_id=trace_id
            )
            snapshot_id = ledger.capture_snapshot(feature_id, binding_id, trace_id=trace_id)
            analysis_id = ledger.analyze_snapshot(
                snapshot_id, binding_id, "s01-analysis", trace_id=trace_id
            )
            source_ref = ledger.connection.execute(
                "SELECT source_ref_id, blob_hash FROM source_refs WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            assert source_ref is not None
            source_ref_id = str(source_ref["source_ref_id"])
            steps.append(
                {
                    "step": "S01-03",
                    "time": now(),
                    "snapshot_id": snapshot_id,
                    "analysis_id": analysis_id,
                }
            )
            learning = GrowthLearningService(ledger)
            opportunities = OpportunityBatch.model_validate(
                {
                    "opportunities": [
                        {
                            "title": "显式执行器与 join 等待",
                            "reason": "新增 supplyAsync(..., executor)、thenCombine 和 join",
                            "learning_goal": "解释任务由哪个执行器执行，join 是否阻塞调用者",
                            "source_refs": [source_ref_id],
                            "estimated_minutes": 5,
                            "topic_suggestions": ["CompletableFuture 并发等待"],
                            "uncertainties": ["实际任务线程由传入的 executor 决定"],
                        }
                    ]
                }
            )
            opportunity_ids = learning.save_opportunities(
                analysis_id, opportunities, origin="replay", trace_id=trace_id
            )
            repeated_opportunity_ids = learning.save_opportunities(
                analysis_id, opportunities, origin="replay", trace_id=trace_id
            )
            proposal = ledger.connection.execute(
                "SELECT proposal_id FROM topic_proposals WHERE opportunity_id=?",
                (opportunity_ids[0],),
            ).fetchone()
            assert proposal is not None
            topic_before = ledger.connection.execute(
                "SELECT COUNT(*) FROM growth_topics"
            ).fetchone()[0]
            topic_id = learning.decide_topic(
                str(proposal["proposal_id"]), "confirm", trace_id=trace_id
            )
            assert topic_id is not None
            repeated_topic_id = learning.decide_topic(
                str(proposal["proposal_id"]), "confirm", trace_id=trace_id
            )
            task_id = learning.create_task(
                opportunity_ids[0],
                topic_id,
                "这段代码的两个查询任务由哪个执行器运行？join 对调用线程有什么影响？",
                trace_id=trace_id,
            )
            repeated_task_id = learning.create_task(
                opportunity_ids[0],
                topic_id,
                "这段代码的两个查询任务由哪个执行器运行？join 对调用线程有什么影响？",
                trace_id=trace_id,
            )
            steps.append(
                {
                    "step": "S01-04/05",
                    "time": now(),
                    "opportunity_id": opportunity_ids[0],
                    "proposal_id": proposal["proposal_id"],
                    "topic_id": topic_id,
                    "task_id": task_id,
                    "model_mode": "replay",
                }
            )
            attempt1 = learning.submit_answer(
                task_id, A1, trace_id=trace_id, actor_origin="simulated_user"
            )
            learning.mark_feedback_pending(attempt1, trace_id=trace_id)
            learning.feedback_failed(attempt1, "注入一次反馈服务中断", trace_id=trace_id)
            preserved = ledger.connection.execute(
                "SELECT answer_text, status FROM learning_attempts WHERE attempt_id=?", (attempt1,)
            ).fetchone()
            feedback1 = FeedbackDraft.model_validate(
                {
                    "summary": "任务交给传入的 executor；join 等待组合结果，可能阻塞调用者。",
                    "user_claims": ["每次 supplyAsync 新建线程", "join 不会等待"],
                    "code_facts": ["两个 supplyAsync 都显式传入 executor", "结果调用 join"],
                    "general_principles": ["线程创建由执行器策略决定"],
                    "inferences": ["调用 plan 的线程可能等待"],
                    "corrections": ["不能从该代码推断每个任务新建线程", "join 会等待结果"],
                    "source_refs": [source_ref_id],
                }
            )
            learning.mark_feedback_pending(attempt1, trace_id=trace_id)
            learning.save_feedback(attempt1, feedback1, origin="replay", trace_id=trace_id)
            steps.append(
                {"step": "S01-06", "time": now(), "attempt_id": attempt1, "feedback_mode": "replay"}
            )
            attempt2 = learning.submit_answer(
                task_id,
                A2,
                trace_id=trace_id,
                actor_origin="simulated_user",
                parent_attempt_id=attempt1,
            )
            feedback2 = FeedbackDraft.model_validate(
                {
                    "summary": "修正说明了显式执行器与 join 可能阻塞。",
                    "user_claims": ["任务交给传入的 executor", "join 可能阻塞"],
                    "code_facts": ["两个 supplyAsync 都显式传入 executor", "最后调用 join"],
                    "general_principles": ["线程是否新建由 executor 决定"],
                    "inferences": ["调用者仍可能等待"],
                    "corrections": [],
                    "source_refs": [source_ref_id],
                }
            )
            learning.mark_feedback_pending(attempt2, trace_id=trace_id)
            learning.save_feedback(attempt2, feedback2, origin="replay", trace_id=trace_id)
            learning.set_task_progress(task_id, "completed", trace_id=trace_id)
            steps.append(
                {"step": "S01-07", "time": now(), "attempt_id": attempt2, "feedback_mode": "replay"}
            )
            notes_before = ledger.connection.execute(
                "SELECT COUNT(*) FROM engineering_notes"
            ).fetchone()[0]
            note_id = learning.save_user_note(task_id, N1, trace_id=trace_id)
            steps.append(
                {
                    "step": "S01-08",
                    "time": now(),
                    "note_id": note_id,
                    "content_hash": hashlib.sha256(N1.encode()).hexdigest(),
                }
            )
            local_chain = ledger.export_chain(feature_id)

        with GrowthLocalRepository(run_dir / "local.sqlite") as reopened:
            task_chain = GrowthLearningService(reopened).export_task_chain(task_id)
            schema_version = reopened.connection.execute("PRAGMA user_version").fetchone()[0]
            event_rows = reopened.connection.execute(
                "SELECT * FROM growth_events ORDER BY created_at, rowid"
            ).fetchall()
            events = [dict(row) for row in event_rows]
            source_bytes = reopened.connection.execute(
                "SELECT content FROM source_refs WHERE source_ref_id=?", (source_ref_id,)
            ).fetchone()[0]

        # Recreate the prior v2 shape from this real M1 chain, then upgrade it.
        legacy_db = run_dir / "upgrade-v2.sqlite"
        with (
            sqlite3.connect(run_dir / "local.sqlite") as source_db,
            sqlite3.connect(legacy_db) as target_db,
        ):
            source_db.backup(target_db)
            target_db.executescript(
                "DROP TABLE IF EXISTS local_chat_model_requests; "
                "DROP TABLE IF EXISTS local_development_event_captures; "
                "DROP TABLE growth_task_hints; "
                "DROP TABLE feedback_model_responses; "
                "DROP TABLE feedback_model_attempts; "
                "DROP TABLE feedback_model_requests; "
                "DROP TABLE analysis_model_responses; "
                "DROP TABLE analysis_model_requests; "
                "DROP TABLE snapshot_bindings; "
                "DROP TABLE sync_pending_attempts; DROP TABLE sync_task_drafts; "
                "DROP TABLE sync_note_drafts; DROP TABLE sync_outbox; "
                "DROP TABLE sync_entities; DROP TABLE sync_accounts; "
                "DROP TABLE sync_publication_links; DROP TABLE project_policies; "
                "DROP TABLE growth_timeline; DROP TABLE engineering_notes; "
                "DROP TABLE learning_attempts; DROP TABLE growth_tasks; "
                "DROP TABLE topic_proposals; DROP TABLE growth_topics; "
                "DROP TABLE opportunities; DROP TABLE opportunity_batches; "
                "PRAGMA user_version=2;"
            )
        with GrowthLocalRepository(legacy_db) as upgraded:
            upgraded_version = upgraded.connection.execute("PRAGMA user_version").fetchone()[0]
            upgraded_ids = {
                "feature": upgraded.connection.execute(
                    "SELECT feature_id FROM features WHERE feature_id=?", (feature_id,)
                ).fetchone()[0],
                "snapshot": upgraded.connection.execute(
                    "SELECT snapshot_id FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
                ).fetchone()[0],
                "analysis": upgraded.connection.execute(
                    "SELECT analysis_id FROM analysis_runs WHERE analysis_id=?", (analysis_id,)
                ).fetchone()[0],
            }
            upgraded_opportunity_count = upgraded.connection.execute(
                "SELECT COUNT(*) FROM opportunities"
            ).fetchone()[0]

        checks = {
            "java_baseline_result": "RESULT=A|B" in baseline_output,
            "java_changed_result": "RESULT=A|B" in changed_output,
            "java_changed_uses_executor": "route-worker-" in changed_output
            and "route-worker-" not in baseline_output,
            "real_git_diff": base_sha != head_sha
            and source_ref_id in [r["source_ref_id"] for r in local_chain["source_refs"]],
            "source_snapshot_bytes": source_bytes == CHANGED_SOURCE.encode(),
            "source_hash_matches": source_ref["blob_hash"]
            == hashlib.sha256(source_bytes).hexdigest(),
            "repo_status_unchanged": git(repo, "status", "--porcelain", "--untracked-files=all")
            == original_status,
            "opportunity_idempotent": opportunity_ids == repeated_opportunity_ids,
            "no_topic_before_confirmation": topic_before == 0,
            "topic_decision_idempotent": topic_id == repeated_topic_id,
            "task_idempotent": task_id == repeated_task_id,
            "answer_survives_feedback_failure": preserved["answer_text"] == A1
            and preserved["status"] == "feedback_failed",
            "a1_a2_original_preserved": [r["answer_text"] for r in task_chain["attempts"]]
            == [A1, A2],
            "a2_parent_links_a1": task_chain["attempts"][1]["parent_attempt_id"] == attempt1,
            "replay_feedback_kept_separate": all(
                r["feedback_origin"] == "replay" for r in task_chain["attempts"]
            ),
            "note_empty_before_user_input": notes_before == 0,
            "user_note_exact": len(task_chain["notes"]) == 1
            and task_chain["notes"][0]["content_text"] == N1,
            "note_is_user_only": task_chain["notes"][0]["author"] == "user",
            "no_false_mastery": {r["evidence_label"] for r in task_chain["timeline"]}
            <= {"practicing", "encountered"},
            "task_completed_without_mastery": task_chain["task"]["progress"] == "completed",
            "schema_survives_restart": schema_version == 14,
            "v2_to_v14_preserves_m1_ids": upgraded_version == 14
            and upgraded_ids == {
                "feature": feature_id,
                "snapshot": snapshot_id,
                "analysis": analysis_id,
            }
            and upgraded_opportunity_count == 0,
            "trace_links_events": all(row["trace_id"] == trace_id for row in events),
        }
        artifacts = run_dir / "artifacts"
        artifacts.mkdir()
        save(artifacts / "RoutePlanner.java", CHANGED_SOURCE)
        save(artifacts / "base-probe.txt", baseline_output)
        save(artifacts / "changed-probe.txt", changed_output)
        dump(artifacts / "replay-opportunities.json", opportunities.model_dump(mode="json"))
        dump(artifacts / "replay-feedback-a1.json", feedback1.model_dump(mode="json"))
        dump(artifacts / "replay-feedback-a2.json", feedback2.model_dump(mode="json"))
        git(repo, "bundle", "create", str(run_dir / "fixture" / "fixture.bundle"), "--all")
        dump(run_dir / "chain.json", {"local": local_chain, "learning": task_chain})
        save(
            run_dir / "events.jsonl",
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in events),
        )
        save(
            run_dir / "steps.jsonl",
            "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in steps),
        )
        status = "PASS" if all(checks.values()) else "FAIL"
        dump(run_dir / "result.json", {"status": status, "checks": checks})
        dump(
            run_dir / "manifest.json",
            {
                "run_id": run_id,
                "stage": "M2",
                "case_id": "S01-local-replay",
                "mode": "replay",
                "started_at": started_at,
                "finished_at": now(),
                "base_sha": base_sha,
                "head_sha": head_sha,
                "trace_id": trace_id,
                "check_status": status,
                "stage_status": "BLOCKED",
                "stage_blockers": [
                    "外部真实模型凭据未配置",
                    "桌面完整链路尚未由真实 DOM 情境驱动，云同步未贯通",
                    "M0 双平台底座与后续完整 ReMe 故事尚未验收",
                ],
                "artifact_sha256": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in artifacts.iterdir()
                    if path.is_file()
                },
            },
        )
        save(
            run_dir / "report.md",
            "# M2 S01 本地回放链路\n\n"
            f"真实 Java Git 基线 `{base_sha}` 到并发改动 `{head_sha}`；两个 Probe 都返回 A|B，"
            "改动版任务在传入执行器的 route-worker 线程上运行。"
            f"快照 `{snapshot_id}`、机会 `{opportunity_ids[0]}`、任务 `{task_id}`、"
            f"两次独立回答 `{attempt1}`/`{attempt2}`、亲写笔记 `{note_id}` 已持久化并重开核验。"
            "A1 的反馈故障未删答案，A2 关联 A1；笔记正文只在模拟用户输入 N1 后出现。"
            "机会与反馈使用显式注入的 replay 输出，未调用真实模型或桌面 UI，"
            "所以此记录不能计入 S01 live 验收。\n\n"
            f"本地检查：**{status}**；M2 阶段：**BLOCKED**。\n",
        )
        print(run_dir)
        if status != "PASS":
            raise AssertionError(f"M2 checks failed: {checks}")
    except Exception as exc:
        if steps and not (run_dir / "steps.jsonl").exists():
            save(
                run_dir / "steps.jsonl",
                "".join(json.dumps(step, ensure_ascii=False, default=str) + "\n" for step in steps),
            )
        if not (run_dir / "result.json").exists():
            dump(
                run_dir / "result.json", {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
            )
            dump(
                run_dir / "manifest.json",
                {
                    "run_id": run_id,
                    "stage": "M2",
                    "mode": "replay",
                    "check_status": "FAIL",
                    "stage_status": "BLOCKED",
                    "error": str(exc),
                },
            )
            save(
                run_dir / "report.md",
                f"# M2 回放失败\n\n`{type(exc).__name__}: {exc}`。"
                "原运行保留，修复后新建 run_id。\n",
            )
        print(run_dir)
        raise


if __name__ == "__main__":
    main()
