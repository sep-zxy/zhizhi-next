"""Audited source transport; restore on GitHub, never on the user's Windows host.

Preparation reads a fixed Git commit, not the working tree or its history. The
one-time publisher can only fast-forward this repository's main with unchanged
workflow blobs, using the job-scoped GITHUB_TOKEN. No credentials are persisted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile

REPOSITORY = "sep-zxy/zhizhi-platform"
CHUNK_BYTES = 96_000
REQUEST_LIMIT = 131_072
MAX_UNPACKED = 256_000_000
MAX_PACKED = 128_000_000
EXCLUDED_PREFIXES = ("design-output/",)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def load_json(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    return result


def exact_hex(value: str, length: int) -> str:
    if not re.fullmatch("[0-9a-f]{" + str(length) + "}", value):
        raise ValueError("Expected a complete lowercase content hash")
    return value


def relative(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or str(path) != name or ".." in path.parts
            or "\\" in name or ":" in name or any(ord(c) < 32 for c in name)
            or any(part.casefold() == ".git" for part in path.parts)):
        raise ValueError("Unsafe source path")
    return name


def safe(root: Path, name: str) -> Path:
    target = root.joinpath(*PurePosixPath(relative(name)).parts)
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink() or (
                hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ValueError("Links and junctions are forbidden")
        if ancestor == root:
            break
    return target


def git(repo: Path, *args: str, data: bytes | None = None, env=None) -> bytes:
    return subprocess.check_output(
        ["git", *args], cwd=repo, input=data, env=env, stderr=subprocess.PIPE
    )


def tree_digest(files: dict[str, tuple[str, bytes]]) -> str:
    """Compute a Git tree without changing any source repository or its index."""
    root = {}
    for name, (mode, data) in files.items():
        parts = PurePosixPath(relative(name)).parts
        directory = root
        for part in parts[:-1]:
            directory = directory.setdefault(part, {})
            if not isinstance(directory, dict):
                raise ValueError("File/directory collision")
        if parts[-1] in directory:
            raise ValueError("Duplicate source path")
        if mode not in {"100644", "100755"}:
            raise ValueError("Unsupported source mode")
        directory[parts[-1]] = (mode, git_blob(data))

    def encode(directory):
        content = bytearray()
        for name, value in sorted(
                directory.items(), key=lambda item: item[0].encode() +
                (b"/" if isinstance(item[1], dict) else b"\0")):
            mode, digest = ("40000", encode(value)) if isinstance(value, dict) else value
            content.extend(mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(digest))
        return hashlib.sha1(b"tree " + str(len(content)).encode() + b"\0" + content).hexdigest()
    return encode(root)


def audit_snapshot(repo: Path, ref: str) -> tuple[dict, dict]:
    exact_hex(ref, 40)
    # The scanner is local reviewed tooling; fixture/asset exceptions come from
    # the same immutable commit as the files, never dirty working-tree copies.
    spec = importlib.util.spec_from_file_location(
        "delivery_audit", Path(__file__).with_name("prepare_git_delivery.py"))
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    fixtures = load_json(git(repo, "show", ref + ":deploy/git-sync/reviewed-fixtures.json"))
    assets = load_json(git(repo, "show", ref + ":deploy/git-sync/reviewed-source-assets.json"))
    files, excluded, blocked = {}, [], []
    for row in git(repo, "ls-tree", "-rz", ref).split(b"\0"):
        if not row:
            continue
        meta, path = row.split(b"\t", 1)
        mode, kind, blob = meta.decode().split()
        name = relative(path.decode())
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Unsupported source entry: " + name)
        if name.startswith(EXCLUDED_PREFIXES):
            excluded.append(name)
            continue
        data = git(repo, "cat-file", "blob", blob)
        if not audit.source_path_allowed(name):
            blocked.append({"path": name, "reason": "forbidden-path"})
        elif audit.is_binary(data) and assets.get(name, {}).get("sha256") != sha256(data):
            blocked.append({"path": name, "reason": "unreviewed-binary"})
        elif audit.scan(name, data, fixtures):
            blocked.append({"path": name, "reason": "unreviewed-secret-pattern"})
        else:
            files[name] = (mode, data)
    if blocked:
        raise ValueError(json.dumps({"blocked": blocked}, ensure_ascii=False))
    original = git(repo, "rev-parse", ref + "^{tree}").decode().strip()
    projected = tree_digest(files)
    if not excluded and projected != original:
        raise ValueError("Source Git tree reconstruction mismatch")
    return files, {
        "source_commit": ref, "original_git_tree": original,
        "restored_git_tree": projected, "excluded_nonproduct_paths": excluded,
        "history_included": False,
    }


def prepare_files(files: dict, provenance: dict, output: Path) -> dict:
    if output.exists():
        raise ValueError("Use a fresh output directory")
    records, body = {}, bytearray()
    for name, (mode, data) in sorted(files.items()):
        relative(name)
        records[name] = {
            "mode": mode, "bytes": len(data), "sha256": sha256(data), "git_blob": git_blob(data),
        }
        body.extend(data)
    manifest = {"schema_version": 1, **provenance, "files": records}
    manifest_data = json_bytes(manifest)
    unpacked = len(manifest_data).to_bytes(8, "big") + manifest_data + body
    if len(unpacked) > MAX_UNPACKED:
        raise ValueError("Source exceeds reviewed size boundary")
    packed = lzma.compress(unpacked, preset=6)
    if len(packed) > MAX_PACKED:
        raise ValueError("Transport exceeds reviewed size boundary")
    payloads, parts = {".gitattributes": b"* -text\n"}, []
    for offset in range(0, len(packed), CHUNK_BYTES):
        data = packed[offset:offset + CHUNK_BYTES]
        name = "objects/" + sha256(data)
        payloads[name] = data
        parts.append({"path": name, "sha256": sha256(data), "bytes": len(data)})
    transport = {
        "schema_version": 1, "format": "zhizhi-source-xz-v1",
        "packed_sha256": sha256(packed), "packed_bytes": len(packed),
        "unpacked_bytes": len(unpacked), "manifest_sha256": sha256(manifest_data),
        "source_commit": manifest["source_commit"],
        "restored_git_tree": manifest["restored_git_tree"], "parts": parts,
    }
    payloads["transport.json"] = json_bytes(transport)
    requests, tree = [], []
    output.mkdir(parents=True)
    for name, data in sorted(payloads.items()):
        request = {"content": base64.b64encode(data).decode(), "encoding": "base64"}
        request_data = json_bytes(request)
        if len(request_data) > REQUEST_LIMIT:
            raise ValueError("Git Data API request exceeds 128 KiB")
        blob = git_blob(data)
        target = output / "checkout" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        request_path = output / "requests" / (blob + ".json")
        request_path.parent.mkdir(exist_ok=True)
        request_path.write_bytes(request_data)
        requests.append({"path": name, "file": "requests/" + blob + ".json",
                         "expected_sha": blob, "bytes": len(data),
                         "request_bytes": len(request_data)})
        tree.append({"path": name, "mode": "100644", "type": "blob", "sha": blob})
    plan = {
        **provenance, "source_files": len(files), "source_bytes": len(body),
        "packed_bytes": len(packed), "transport_files": len(payloads),
        "transport_sha256": sha256(payloads["transport.json"]),
        "maximum_request_bytes": max(item["request_bytes"] for item in requests),
        "requests": requests, "remote_mutations_performed": False,
    }
    (output / "plan.json").write_bytes(json_bytes(plan))
    (output / "source-manifest.json").write_bytes(manifest_data)
    (output / "expected-tree.json").write_bytes(json_bytes({"tree_elements": tree}))
    return plan


def restore(transport_root: Path, expected_transport: str, output: Path) -> dict:
    exact_hex(expected_transport, 64)
    if output.exists():
        raise ValueError("Refusing to overwrite restored source")
    header = safe(transport_root, "transport.json").read_bytes()
    if sha256(header) != expected_transport:
        raise ValueError("Unapproved transport manifest")
    plan = load_json(header)
    if plan.get("schema_version") != 1 or plan.get("format") != "zhizhi-source-xz-v1":
        raise ValueError("Unsupported source transport")
    if (type(plan["unpacked_bytes"]) is not int or not 8 < plan["unpacked_bytes"] <= MAX_UNPACKED
            or type(plan["packed_bytes"]) is not int or not 0 < plan["packed_bytes"] <= MAX_PACKED
            or not 1 <= len(plan["parts"]) <= 2048):
        raise ValueError("Unbounded transport size")
    packed = bytearray()
    for part in plan["parts"]:
        if part["path"] != "objects/" + exact_hex(part["sha256"], 64):
            raise ValueError("Non-content-addressed source chunk")
        data = safe(transport_root, part["path"]).read_bytes()
        if not 0 < len(data) <= CHUNK_BYTES or len(data) != part["bytes"] or sha256(data) != part["sha256"]:
            raise ValueError("Source chunk integrity failed")
        packed.extend(data)
        if len(packed) > plan["packed_bytes"]:
            raise ValueError("Excess compressed source data")
    if len(packed) != plan["packed_bytes"] or sha256(packed) != plan["packed_sha256"]:
        raise ValueError("Packed source integrity failed")
    inflater = lzma.LZMADecompressor(memlimit=128_000_000)
    unpacked = inflater.decompress(packed, max_length=plan["unpacked_bytes"] + 1)
    if len(unpacked) != plan["unpacked_bytes"] or not inflater.eof or inflater.unused_data:
        raise ValueError("Invalid or oversized compressed source")
    length = int.from_bytes(unpacked[:8], "big")
    if not 0 < length <= 8_000_000 or length + 8 > len(unpacked):
        raise ValueError("Invalid source manifest length")
    manifest_data = unpacked[8:8 + length]
    if sha256(manifest_data) != plan["manifest_sha256"]:
        raise ValueError("Source manifest checksum mismatch")
    manifest = load_json(manifest_data)
    if (manifest.get("schema_version") != 1
            or manifest["source_commit"] != plan["source_commit"]
            or manifest["restored_git_tree"] != plan["restored_git_tree"]
            or manifest.get("history_included") is not False):
        raise ValueError("Source provenance mismatch")
    offset, files, case_names = 8 + length, {}, set()
    for name, record in sorted(manifest["files"].items()):
        relative(name)
        if name.casefold() in case_names:
            raise ValueError("Case-insensitive source collision")
        case_names.add(name.casefold())
        size = record["bytes"]
        if type(size) is not int or size < 0 or offset + size > len(unpacked):
            raise ValueError("Invalid source file size")
        data = unpacked[offset:offset + size]
        offset += size
        if sha256(data) != record["sha256"] or git_blob(data) != record["git_blob"]:
            raise ValueError("Source file checksum mismatch: " + name)
        files[name] = (record["mode"], data)
    if offset != len(unpacked) or tree_digest(files) != manifest["restored_git_tree"]:
        raise ValueError("Restored source tree mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="source-verified-", dir=output.parent))
    try:
        for name, (mode, data) in files.items():
            target = safe(temporary, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if mode == "100755" else 0o644)
        temporary.rename(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest


def workflow_blobs(repo: Path, ref: str) -> dict:
    result = {}
    for row in git(repo, "ls-tree", "-rz", ref, "--", ".github/workflows").split(b"\0"):
        if row:
            meta, name = row.split(b"\t", 1)
            mode, kind, digest = meta.decode().split()
            result[name.decode()] = (mode, kind, digest)
    return result


def commit_restored(repo: Path, restored: Path, manifest: dict, expected_main: str) -> str:
    """Write local objects only; reject workflow edits before creating a commit."""
    exact_hex(expected_main, 40)
    if git(repo, "rev-parse", "HEAD").decode().strip() != expected_main:
        raise ValueError("Controller checkout is not the approved main commit")
    desired = {name: (record["mode"], "blob", record["git_blob"])
               for name, record in manifest["files"].items()
               if name.startswith(".github/workflows/")}
    if desired != workflow_blobs(repo, expected_main):
        raise ValueError("Seed exact workflow files through the approved connector first")
    with tempfile.TemporaryDirectory(prefix="source-index-") as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(repo, "read-tree", "--empty", env=env)
        records = bytearray()
        for name, record in sorted(manifest["files"].items()):
            data = safe(restored, name).read_bytes()
            if sha256(data) != record["sha256"]:
                raise ValueError("Restored source changed before commit")
            blob = git(repo, "hash-object", "-w", "--stdin", data=data).decode().strip()
            records.extend(f'{record["mode"]} {blob}\t{name}'.encode() + b"\0")
        git(repo, "update-index", "-z", "--index-info", data=records, env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
    if tree != manifest["restored_git_tree"]:
        raise ValueError("Git index tree differs from audited source")
    env = {
        **os.environ, "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    }
    message = ("恢复经审计的完整源码树\n\nSource-Commit: " + manifest["source_commit"]
               + "\nSource-Tree: " + tree + "\nNo private history imported.\n")
    return git(repo, "commit-tree", tree, "-p", expected_main, data=message.encode(), env=env).decode().strip()


def publish(repo: Path, restored: Path, manifest: dict, expected_main: str) -> dict:
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_SERVER_URL") != "https://github.com"):
        raise ValueError("Publishing is limited to this repository's manual GitHub main workflow")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("Job-scoped GITHUB_TOKEN is required")
    # The secret lives only in this child process environment, not a URL,
    # command argument, .git/config, credential helper, file, or receipt.
    auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
    env = {**os.environ, "GIT_CONFIG_COUNT": "2",
           "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
           "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + auth,
           "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": "",
           "GIT_TERMINAL_PROMPT": "0"}
    remote = "https://github.com/" + REPOSITORY + ".git"
    observed = git(repo, "ls-remote", remote, "refs/heads/main", env=env).decode().split()
    if observed != [exact_hex(expected_main, 40), "refs/heads/main"]:
        raise ValueError("main moved; stop and prepare a new approved dispatch")
    commit = commit_restored(repo, restored, manifest, expected_main)
    # Parent is expected_main. A concurrent update therefore rejects the normal
    # fast-forward push; never force or change a delivery ref in this operation.
    git(repo, "push", "--porcelain", remote, commit + ":refs/heads/main", env=env)
    observed = git(repo, "ls-remote", remote, "refs/heads/main", env=env).decode().split()
    if observed != [commit, "refs/heads/main"]:
        raise ValueError("Push outcome uncertain; inspect remote before retrying")
    return {"status": "source_published", "commit": commit, "previous_main": expected_main,
            "tree": manifest["restored_git_tree"], "source_commit": manifest["source_commit"],
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "build_status": "not_run", "windows_e2e": "not_run"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--repo", type=Path, required=True)
    prepare.add_argument("--ref", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    unpack = sub.add_parser("restore")
    unpack.add_argument("--transport", type=Path, required=True)
    unpack.add_argument("--transport-sha256", required=True)
    unpack.add_argument("--output", type=Path, required=True)
    unpack.add_argument("--publish-repo", type=Path)
    unpack.add_argument("--expected-main")
    unpack.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        files, provenance = audit_snapshot(args.repo, args.ref)
        result = prepare_files(files, provenance, args.output)
        result = {key: value for key, value in result.items() if key != "requests"}
    else:
        manifest = restore(args.transport, args.transport_sha256, args.output)
        result = {"status": "source_restored_and_verified", "files": len(manifest["files"]),
                  "tree": manifest["restored_git_tree"], "source_commit": manifest["source_commit"]}
        if args.publish_repo:
            if not args.expected_main:
                raise ValueError("Expected main SHA is required")
            result = publish(args.publish_repo, args.output, manifest, args.expected_main)
        if args.receipt:
            args.receipt.write_bytes(json_bytes(result))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
