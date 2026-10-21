"""Index the S03 Java cache fixture without touching its Git working tree."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ahadiff.growth.code_intel import build_index, snapshot_source_bytes
from ahadiff.growth.git_snapshot import capture_worktree
from ahadiff.growth.local import GrowthLocalRepository

ROOT = Path(__file__).resolve().parents[2]
REL = Path("src/demo/orders")
GATEWAY = """package demo.orders;
public interface PricingGateway {
    int priceCents(String sku);
}
"""
SERVICE_BASE = """package demo.orders;
import java.util.function.LongSupplier;
public final class OrderService {
    private final PricingGateway gateway;
    public OrderService(PricingGateway gateway, LongSupplier clock) {
        this.gateway = gateway;
    }
    public int quote(String sku, int quantity) {
        return gateway.priceCents(sku) * quantity;
    }
}
"""
CONTROLLER = """package demo.orders;
public final class OrderController {
    private final OrderService service;
    public OrderController(OrderService service) {
        this.service = service;
    }
    public int quote(String sku, int quantity) {
        return service.quote(sku, quantity);
    }
}
"""
CACHE = """package demo.orders;
import java.util.HashMap;
import java.util.Map;
import java.util.function.IntSupplier;
import java.util.function.LongSupplier;

public final class PriceCache {
    private record Entry(int price, long expiresAt) {}
    private final Map<String, Entry> entries = new HashMap<>();
    private final LongSupplier clock;
    private final long ttlMillis;
    public PriceCache(LongSupplier clock, long ttlMillis) {
        this.clock = clock;
        this.ttlMillis = ttlMillis;
    }
    public int get(String sku, IntSupplier load) {
        long now = clock.getAsLong();
        Entry old = entries.get(sku);
        if (old != null && now < old.expiresAt()) {
            return old.price();
        }
        int price = load.getAsInt();
        entries.put(sku, new Entry(price, now + ttlMillis));
        return price;
    }
}
"""
SERVICE_CHANGED = """package demo.orders;
import java.util.function.LongSupplier;

public final class OrderService {
    private final PricingGateway gateway;
    private final PriceCache cache;
    public OrderService(PricingGateway gateway, LongSupplier clock) {
        this.gateway = gateway;
        this.cache = new PriceCache(clock, 1000);
    }
    public int quote(String sku, int quantity) {
        int price = cache.get(sku, () -> gateway.priceCents(sku));
        return price * quantity;
    }
}
"""
PROBE = """package demo.orders;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
public final class Probe {
    public static void main(String[] args) {
        AtomicInteger calls = new AtomicInteger();
        AtomicLong time = new AtomicLong();
        PricingGateway gateway = sku -> { calls.incrementAndGet(); return 1299; };
        OrderController controller = new OrderController(new OrderService(gateway, time::get));
        int first = controller.quote("SKU-1", 2);
        time.set(500);
        int second = controller.quote("SKU-1", 2);
        time.set(1000);
        int third = controller.quote("SKU-1", 2);
        System.out.println(first + "," + second + "," + third + "|" + calls.get());
    }
}
"""


def _run(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    return result.stdout.strip()


def _save(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _probe(repo: Path, output: Path) -> str:
    output.mkdir(parents=True)
    probe_file = output / "Probe.java"
    _save(probe_file, PROBE)
    sources = sorted(str(path) for path in (repo / REL).glob("*.java"))
    _run(["javac", "-encoding", "UTF-8", "-d", str(output), *sources,
          str(probe_file)], cwd=repo)
    return _run(["java", "-cp", str(output), "demo.orders.Probe"], cwd=repo)


def main() -> None:
    run_id = f"m5-codegraph-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    repo = run_dir / "fixture" / "order-demo"
    repo.mkdir(parents=True)
    checks: dict[str, bool] = {}
    steps: list[dict[str, Any]] = []

    def step(label: str, actual: Any, passed: bool) -> None:
        item = {"step": label, "time": datetime.now(UTC).isoformat(),
                "actual": actual, "verdict": "PASS" if passed else "FAIL"}
        steps.append(item)
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")

    try:
        _run(["git", "init", "-b", "main"], cwd=repo)
        _run(["git", "config", "user.name", "Growth Fixture"], cwd=repo)
        _run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo)
        _save(repo / REL / "PricingGateway.java", GATEWAY)
        _save(repo / REL / "OrderService.java", SERVICE_BASE)
        _save(repo / REL / "OrderController.java", CONTROLLER)
        _run(["git", "add", "."], cwd=repo)
        _run(["git", "commit", "-m", "建立订单计价模块模拟基线"], cwd=repo)
        base_sha = _run(["git", "rev-parse", "HEAD"], cwd=repo)
        baseline_output = _probe(repo, run_dir / "probe-baseline")
        checks["baseline_no_cache"] = baseline_output == "2598,2598,2598|3"
        step("S03-01", baseline_output, checks["baseline_no_cache"])
        _run(["git", "switch", "-c", "feature/price-cache"], cwd=repo)
        _save(repo / REL / "OrderService.java", SERVICE_CHANGED)
        _save(repo / REL / "PriceCache.java", CACHE)
        original_status = _run(["git", "status", "--porcelain"], cwd=repo)
        changed_output = _probe(repo, run_dir / "probe-changed")
        checks["lazy_ttl_behavior"] = changed_output == "2598,2598,2598|2"
        step("S03-02", {"output": changed_output, "git_status": original_status},
             checks["lazy_ttl_behavior"])
        cache_name = (REL / "PriceCache.java").as_posix()
        trace_id = str(uuid.uuid4())
        with GrowthLocalRepository(run_dir / "local.sqlite") as ledger:
            project_id = ledger.create_project("订单计价", trace_id=trace_id)
            binding_id = ledger.bind_repo(project_id, repo, trace_id=trace_id)
            feature_id = ledger.start_feature(
                project_id, binding_id, "价格缓存", "main", trace_id=trace_id
            )
            snapshot_id = ledger.capture_snapshot(
                feature_id, binding_id, trace_id=trace_id,
                selected_untracked={cache_name},
            )
            captured = capture_worktree(repo, "main", {cache_name})
            chain = ledger.export_chain(feature_id)
            cache_ref = next(
                ref for ref in chain["source_refs"] if ref["relative_path"] == cache_name
            )
            first_index = build_index(
                repo, base_ref="main", selected_untracked={cache_name},
                snapshot_id=snapshot_id, expected=captured,
                index_root=run_dir / "index-copy-1",
            )
            queries = {
                "controller_quote": first_index.query("OrderController.quote"),
                "service_quote": first_index.query("OrderService.quote"),
                "cache_get": first_index.query("PriceCache.get"),
            }
            callees = {
                "controller_quote": first_index.callees("OrderController.quote"),
                "service_quote": first_index.callees("OrderService.quote"),
                "cache_get": first_index.callees("PriceCache.get"),
            }
            flow = first_index.call_path(
                ["OrderController.quote", "OrderService.quote", "PriceCache.get"],
                snapshot_id=snapshot_id, effective_tree_hash=captured.effective_tree_hash,
            )
            unresolved_flow = first_index.call_path(
                ["PriceCache.get", "PricingGateway.priceCents"],
                snapshot_id=snapshot_id, effective_tree_hash=captured.effective_tree_hash,
            )
            gateway_flow = first_index.call_path(
                ["OrderService.quote", "PricingGateway.priceCents"],
                snapshot_id=snapshot_id, effective_tree_hash=captured.effective_tree_hash,
            )
            full_flow = first_index.call_path(
                ["OrderController.quote", "OrderService.quote", "PriceCache.get",
                 "PricingGateway.priceCents"],
                snapshot_id=snapshot_id, effective_tree_hash=captured.effective_tree_hash,
            )
            checks["indexed_controller_service_cache_flow"] = (
                [edge["status"] for edge in flow["edges"]] == ["indexed", "indexed"]
            )
            checks["unresolved_dynamic_edge_not_claimed"] = (
                unresolved_flow["edges"][0]["status"] == "unknown"
            )
            checks["lambda_gateway_indexed"] = (
                gateway_flow["edges"][0]["status"] == "indexed"
            )
            full_edges = {
                (edge["from"], edge["to"]): edge["status"]
                for edge in full_flow["edges"]
            }
            checks["complete_module_flow_keeps_lambda_and_unknown"] = (
                full_edges == {
                    ("OrderController.quote", "OrderService.quote"): "indexed",
                    ("OrderService.quote", "PriceCache.get"): "indexed",
                    ("OrderService.quote", "PricingGateway.priceCents"): "indexed",
                    ("PriceCache.get", "PricingGateway.priceCents"): "unknown",
                }
            )
            step("S03-04", {"flow": full_flow},
                 checks["indexed_controller_service_cache_flow"]
                 and checks["unresolved_dynamic_edge_not_claimed"]
                 and checks["lambda_gateway_indexed"]
                 and checks["complete_module_flow_keeps_lambda_and_unknown"])
            checks["untracked_cache_in_snapshot_and_index"] = (
                cache_name in first_index.indexed_files
                and cache_ref["blob_hash"] == hashlib.sha256(CACHE.encode()).hexdigest()
                and first_index.effective_tree_hash == captured.effective_tree_hash
            )
            step("S03-03", {"snapshot_id": snapshot_id,
                            "index_revision": first_index.index_revision,
                            "status": first_index.status},
                 checks["untracked_cache_in_snapshot_and_index"])
            _run(["git", "add", "src/demo/orders/OrderService.java", cache_name], cwd=repo)
            _run(["git", "commit", "-m", "增加惰性价格缓存"], cwd=repo)
            _run(["git", "switch", "main"], cwd=repo)
            _save(repo / REL / "OrderController.java", CONTROLLER.replace(
                "    public int quote", "    // 订单接口保留原语义。\n    public int quote"
            ))
            _run(["git", "add", "src/demo/orders/OrderController.java"], cwd=repo)
            _run(["git", "commit", "-m", "补充订单控制器说明"], cwd=repo)
            _run(["git", "switch", "feature/price-cache"], cwd=repo)
            _run(["git", "rebase", "main"], cwd=repo)
            old_cache = snapshot_source_bytes(ledger, cache_ref["source_ref_id"])
            checks["old_snapshot_survives_rebase"] = old_cache == CACHE.encode()
            newer_snapshot_id = ledger.capture_snapshot(
                feature_id, binding_id, trace_id=trace_id
            )
            newer = capture_worktree(repo, "main")
            second_index = build_index(
                repo, base_ref="main", selected_untracked=set(),
                snapshot_id=newer_snapshot_id, expected=newer,
                index_root=run_dir / "index-copy-2",
            )
            second_flow = second_index.call_path(
                ["OrderController.quote", "OrderService.quote", "PriceCache.get",
                 "PricingGateway.priceCents"],
                snapshot_id=newer_snapshot_id,
                effective_tree_hash=newer.effective_tree_hash,
            )
            checks["index_revision_changes_after_rebase"] = (
                first_index.index_revision != second_index.index_revision
                and first_index.effective_tree_hash != second_index.effective_tree_hash
            )
            try:
                first_index.call_path(
                    ["OrderController.quote", "OrderService.quote"],
                    snapshot_id=newer_snapshot_id,
                    effective_tree_hash=newer.effective_tree_hash,
                )
            except ValueError:
                checks["stale_index_rejected"] = True
            else:
                checks["stale_index_rejected"] = False
            checks["original_user_repo_unmodified_by_index"] = (
                ".codegraph" not in _run(["git", "status", "--porcelain",
                                             "--untracked-files=all"], cwd=repo)
                and not (repo / ".codegraph").exists()
            )
            step("S03-07/08", {"new_snapshot_id": newer_snapshot_id,
                                "new_index_revision": second_index.index_revision},
                 checks["old_snapshot_survives_rebase"]
                 and checks["index_revision_changes_after_rebase"])
            (run_dir / "chain.json").write_text(json.dumps({
                "project_id": project_id, "feature_id": feature_id,
                "base_sha": base_sha, "snapshot_id": snapshot_id,
                "newer_snapshot_id": newer_snapshot_id,
                "cache_source_ref_id": cache_ref["source_ref_id"],
                "first_index_revision": first_index.index_revision,
                "second_index_revision": second_index.index_revision,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (run_dir / "events.jsonl").write_text("".join(
                json.dumps(event, ensure_ascii=False) + "\n" for event in chain["events"]
            ), encoding="utf-8")
            artifact = run_dir / "artifacts"
            artifact.mkdir()
            for name, data in {"query.json": queries, "callees.json": callees,
                               "flow.json": full_flow, "flow-after-rebase.json": second_flow,
                               "unresolved-flow.json": unresolved_flow,
                               "status-1.json": first_index.status,
                               "status-2.json": second_index.status}.items():
                (artifact / name).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                )
            for name, content in {
                "PricingGateway.java": GATEWAY, "OrderController.java": CONTROLLER,
                "OrderService-baseline.java": SERVICE_BASE,
                "OrderService-changed.java": SERVICE_CHANGED,
                "PriceCache.java": CACHE, "Probe.java": PROBE,
            }.items():
                _save(artifact / "fixture-source" / name, content)
            _run(["git", "bundle", "create", str(artifact / "fixture.bundle"),
                  "--all"], cwd=repo)
        result = "PASS" if all(checks.values()) else "FAIL"
        (run_dir / "result.json").write_text(
            json.dumps({"status": result, "checks": checks}, ensure_ascii=False,
                       indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": run_id, "stage": "M5", "mode": "replay",
                        "check_status": result, "stage_status": "BLOCKED",
                        "stage_blockers": ["模块云端关系与用户锁定尚未实现",
                                           "真实 UI/模型/ReMe/双平台未验收"]},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / "report.md").write_text(
            "# M5 S03 CodeGraph 局部回放\n\n"
            f"基线 Probe `{baseline_output}`，新增缓存 Probe `{changed_output}`。"
            "未提交的新文件由用户选择纳入快照；CodeGraph 在仓库外的拷贝建立索引，"
            "索引修订绑定完整工作树指纹。feature 经真实提交、main 注释提交与 rebase 后，"
            "旧 SourceRef 仍可按原字节读取，新索引修订与旧版不同。\n\n"
            f"本地检查：**{result}**。模块云端、卡片、ReMe 和桌面 UI 在本地索引回放中尚未验证，"
            "不计入 S03 live 验收。\n", encoding="utf-8"
        )
        if result != "PASS":
            raise AssertionError(f"M5 checks failed: {checks}")
    except Exception as exc:
        if not (run_dir / "result.json").exists():
            (run_dir / "result.json").write_text(
                json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False,
                           indent=2) + "\n", encoding="utf-8"
            )
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": run_id, "stage": "M5", "mode": "replay",
                            "check_status": "FAIL", "stage_status": "BLOCKED",
                            "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (run_dir / "report.md").write_text(
                f"# M5 CodeGraph 回放失败\n\n`{type(exc).__name__}: {exc}`。此运行保持 FAIL。\n",
                encoding="utf-8",
            )
        print(run_dir)
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
