"""Index the S03 installed-app snapshot against the same live worktree."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ahadiff.growth.code_intel import build_index
from ahadiff.growth.git_snapshot import capture_worktree


def main() -> None:
    repo, base_sha, snapshot_id, output = (
        Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
    )
    cache = "src/demo/orders/PriceCache.java"
    selected_untracked = {cache}
    captured = capture_worktree(repo, base_sha, selected_untracked)
    index = build_index(
        repo, base_ref=base_sha, selected_untracked=selected_untracked,
        snapshot_id=snapshot_id, expected=captured,
        index_root=output.parent.parent / "index-copy-s03",
    )
    flow = index.call_path(
        ["OrderController.quote", "OrderService.quote", "PriceCache.get"],
        snapshot_id=snapshot_id,
        effective_tree_hash=captured.effective_tree_hash,
    )
    evidence = {
        "snapshot_id": snapshot_id,
        "base_sha": base_sha,
        "effective_tree_hash": captured.effective_tree_hash,
        "index_revision": index.index_revision,
        "codegraph_version": index.codegraph_version,
        "indexed_files": index.indexed_files,
        "status": index.status,
        "flow": flow,
    }
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
