"""Read only four fixed evidence JSON files; never print fixture/file contents."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat


MAX_BYTES = 2 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
OUTCOMES = frozenset({"success", "failure", "cancelled", "skipped"})
IMAGE_ID = "sha256:d9b8c34d61a9b9a3c44cab883d7d2f2619d674966ab520186f799cf32e3e0377"


def read_record(work: Path, relative: str) -> dict | None:
    """Descriptor-relative reads: no symlink ancestors, hardlinks or FIFOs.

    Payload directories are untrusted. Never resolve a payload-supplied path;
    callers supply one fixed relative name, and every open rejects links.
    """
    path = work.absolute() / relative
    descriptors = []
    try:
        if ".." in path.parts or not path.is_absolute():
            return None
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        for part in path.parts[1:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        file = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(file)
        before = os.fstat(file)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
            return None
        chunks = []
        size = 0
        while chunk := os.read(file, min(65536, MAX_BYTES + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                return None
        after = os.fstat(file)
        if (after.st_nlink != 1 or before.st_size != after.st_size or size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            return None
        data = json.loads(b"".join(chunks).decode("utf-8"))
        return data if type(data) is dict else None
    except (OSError, ValueError, UnicodeError):
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def valid_receipt(value: object) -> bool:
    return (type(value) is dict
            and value.get("status") == "verified_site_build"
            and value.get("site_build_executed") is True
            and value.get("deploy_executed") is False
            and value.get("isolation_attested") is False
            and isinstance(value.get("artifact_hash"), str)
            and HASH.fullmatch(value["artifact_hash"]) is not None)


def acceptance_valid(record: dict | None, source: str | None) -> bool:
    if not record or not source or record.get("source_commit") != source:
        return False
    if record.get("status") != "synthetic_real_build_verified":
        return False
    gates = ("double_build_identical", "cache_reset_before_each_successful_publication_build",
             "leak_scan_negative_gate", "tamper_negative_gate", "revocation_negative_gate")
    if not all(record.get(key) is True for key in gates):
        return False
    if record.get("deploy_executed") is not False:
        return False
    if any(record.get(key) != "not_run" for key in ("live_model", "live_user_data", "windows_e2e")):
        return False
    receipts, controls = record.get("product_receipts"), record.get("positive_control_receipts")
    if not (type(receipts) is list and type(controls) is list
            and len(receipts) == len(controls) == 2
            and all(valid_receipt(value) for value in receipts + controls)):
        return False
    if len({value["artifact_hash"] for value in receipts + controls}) != 1:
        return False
    files = record.get("artifact_files")
    return (type(files) is dict and bool(files)
            and all(isinstance(v, str) and HASH.fullmatch(v) for v in files.values()))


def isolation_valid(record: dict | None, host: dict | None, source: str | None, run_id: str | None) -> bool:
    # The executor's independently checked receipt is separate from product
    # isolation_attested=false. This is a strict schema, not existence checking.
    return bool(record and host and source and record.get("source_commit") == source
                and record.get("executor") == "docker-network-none"
                and all(record.get(key) is True for key in (
                    "isolation_verified", "network_none", "read_only_root", "nonroot",
                    "capabilities_dropped", "no_new_privileges"))
                and host.get("source_commit") == source
                and host.get("run_id") == run_id
                and host.get("host_configuration_verified") is True
                and host.get("host_execution_verified") is True
                and host.get("image_id") == IMAGE_ID)


def summarize(work: Path, artifact_outcome: str, execution_outcome: str,
              source: str, run_id: str, cleanup_outcome: str, export_outcome: str) -> tuple[str, bool]:
    scope = read_record(work, "result/scope.json")
    isolation = read_record(work, "result/isolation.json")
    host = read_record(work, "host-evidence/docker-host-inspect.json")
    acceptance = read_record(work, "result/acceptance/acceptance-receipt.json")
    if not isinstance(source, str) or SHA.fullmatch(source) is None:
        source = None
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9]{1,24}", run_id) is None:
        run_id = None
    provenance = bool(source and run_id and scope and scope.get("source_commit") == source
                      and scope.get("run_id") == run_id and scope.get("github_hosted") is True
                      and all(scope.get(key) == "not_run" for key in
                              ("live_model", "live_channels", "windows_e2e", "deployment")))
    isolated, accepted = isolation_valid(isolation, host, source, run_id), acceptance_valid(acceptance, source)
    outcome = artifact_outcome if artifact_outcome in OUTCOMES else "unknown"
    execution = execution_outcome if execution_outcome in OUTCOMES else "unknown"
    cleanup = cleanup_outcome if cleanup_outcome in OUTCOMES else "unknown"
    exported = export_outcome if export_outcome in OUTCOMES else "unknown"
    verified = provenance and isolated and accepted and outcome == execution == cleanup == exported == "success"
    lines = ["## C20 bounded synthetic verification", "",
             "**VERIFIED (bounded synthetic scope only)**" if verified else "**NOT VERIFIED**",
             "", f"- Source commit: `{source or 'unavailable'}`",
             f"- GitHub run: `{run_id or 'unavailable'}`",
             f"- Source/run provenance: {'verified' if provenance else 'missing or invalid'}",
             f"- Executor isolation evidence: {'verified' if isolated else 'missing or invalid'}",
             f"- Real Quartz, double-build and negative gates: {'verified' if accepted else 'missing or invalid'}",
             f"- Isolated execution step outcome: **{execution}**",
             f"- Exact-container cleanup outcome: **{cleanup}**",
             f"- Safe evidence export outcome: **{exported}**",
             f"- Artifact upload outcome: **{outcome}**", "",
             "Scope: synthetic canonical Git/SQLite with real Quartz only. No user-data, model, Windows or deployment E2E claim.",
             "No fixture content, arbitrary JSON strings, or source text is included in this summary.", ""]
    return "\n".join(lines), verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--artifact-outcome", required=True)
    parser.add_argument("--execution-outcome", required=True)
    parser.add_argument("--cleanup-outcome", required=True)
    parser.add_argument("--export-outcome", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--step-summary", required=True, type=Path)
    args = parser.parse_args()
    summary, verified = summarize(args.work, args.artifact_outcome, args.execution_outcome,
                                  args.source, args.run_id, args.cleanup_outcome, args.export_outcome)
    with args.step_summary.open("a", encoding="utf-8") as stream:
        stream.write(summary)
    print(summary)
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
