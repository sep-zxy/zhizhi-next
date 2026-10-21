"""Read-only evidence for an installed Electron capture against a Git fixture."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def main() -> None:
    repo, snapshot_id, output = (Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
    database = (repo / ".ahadiff" / "growth.sqlite").resolve()
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        snapshot = conn.execute(
            "SELECT snapshots.*, features.project_id, features.base_ref, "
            "features.start_base_sha, bindings.binding_id, "
            "bindings.canonical_local_path FROM snapshots "
            "JOIN features ON features.feature_id=snapshots.feature_id "
            "JOIN snapshot_bindings ON snapshot_bindings.snapshot_id=snapshots.snapshot_id "
            "JOIN bindings ON bindings.binding_id=snapshot_bindings.binding_id "
            "WHERE snapshots.snapshot_id=?", (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            raise ValueError("界面快照 ID 不存在于本机业务账本")
        sources = conn.execute(
            "SELECT source_ref_id, relative_path, blob_hash, content, deleted "
            "FROM source_refs WHERE snapshot_id=? ORDER BY relative_path",
            (snapshot_id,),
        ).fetchall()
        event = conn.execute(
            "SELECT event_id, trace_id, event_type, payload_json, created_at "
            "FROM growth_events WHERE entity_type='snapshot' AND entity_id=? "
            "ORDER BY created_at DESC LIMIT 1", (snapshot_id,),
        ).fetchone()
        analyses = conn.execute(
            "SELECT analysis_id, mode, status, upstream_run_id, error, created_at "
            "FROM analysis_runs WHERE snapshot_id=? ORDER BY created_at",
            (snapshot_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT event_id, trace_id, parent_event_id, entity_type, entity_id, "
            "event_type, payload_json, created_at FROM growth_events "
            "WHERE entity_id IN (?, ?, ?, ?) ORDER BY created_at, event_id",
            (snapshot["project_id"], snapshot["binding_id"],
             snapshot["feature_id"], snapshot_id),
        ).fetchall()
    evidence = {
        "snapshot_id": snapshot_id,
        "project_id": snapshot["project_id"],
        "feature_id": snapshot["feature_id"],
        "binding_id": snapshot["binding_id"],
        "canonical_local_path": snapshot["canonical_local_path"],
        "base_ref": snapshot["base_ref"],
        "start_base_sha": snapshot["start_base_sha"],
        "resolved_base_sha": snapshot["resolved_base_sha"],
        "head_sha": snapshot["head_sha"],
        "effective_tree_hash": snapshot["effective_tree_hash"],
        "diff_hash": snapshot["diff_hash"],
        "patch_sha256": hashlib.sha256(snapshot["patch_text"].encode()).hexdigest(),
        "source_refs": [
            {
                "source_ref_id": row["source_ref_id"],
                "relative_path": row["relative_path"],
                "blob_hash": row["blob_hash"],
                "stored_content_sha256": hashlib.sha256(row["content"]).hexdigest(),
                "deleted": bool(row["deleted"]),
            }
            for row in sources
        ],
        "capture_event": None if event is None else {
            "event_id": event["event_id"], "trace_id": event["trace_id"],
            "event_type": event["event_type"],
            "payload": json.loads(event["payload_json"]),
            "created_at": event["created_at"],
        },
        "analysis_runs": [dict(row) for row in analyses],
        "local_events": [
            {**{key: row[key] for key in row.keys()  # noqa: SIM118 - sqlite3.Row iterates values.
                if key != "payload_json"},
             "payload": json.loads(row["payload_json"])}
            for row in events
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
