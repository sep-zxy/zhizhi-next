"""Prepare audited Git trees locally. This program never contacts a remote."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_PARTS = {
    ".git", ".ssh", ".aws", ".codex", ".claude", ".ahadiff", ".local",
    ".runtime", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    "raw", "vault", "private-knowledge", "reports", "screenshots",
    "test-results", "playwright-report",
}
FORBIDDEN_NAMES = {"auth.json", "credentials.json", "cookies.json", "id_rsa", "id_ed25519"}
FORBIDDEN_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".log", ".zip", ".bundle", ".pem", ".key"}
SECRET_RULES = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "github-token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,})\b",
    "aws-access-key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "provider-key": rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}\b",
    "slack-token": rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
    "url-userinfo": rb"""https?://[^\s/:<>"']{2,}:[^\s/@<>"']{6,}@""",
}
PATCH_SHA256 = "6263a182c2805098ea700482c4214058aecb4bb680b01504a622de338a0bbd99"
SMOKE_SHA256 = "2f8245f84fc1eb4a240dfd0644df58480a44ca2e8c43176efde5f1104ecbdba6"
BASE_SHA256 = "f326f38b3c5ef8df821fa3b8d8f8c48d8bfa8bc18fbb12d1e74931ef91a473d0"
VERSION = "2026-10-20-codex-cli.1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=repo)


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name or path.is_absolute() or ".." in path.parts
        or "\\" in name or ":" in name or str(path) != name
    ):
        raise ValueError("Unsafe relative path")
    return name


def source_path_allowed(name: str) -> bool:
    safe_relative(name)
    path = PurePosixPath(name)
    if any(part.lower() in FORBIDDEN_PARTS for part in path.parts):
        return False
    if path.name.lower() in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    if name.startswith(("projects/selfweb/site/content/", "projects/selfweb/site/public/")):
        return False
    if "runtime" in path.parts and name not in {
        "deploy/windows-portable/runtime/launcher.py",
        "deploy/windows-portable/runtime/windows_job.py",
        "deploy/windows-portable/runtime/pi_once.py",
    }:
        return False
    return True


def scan(name: str, data: bytes, reviewed: dict) -> list[str]:
    matches = sorted(rule for rule, pattern in SECRET_RULES.items() if re.search(pattern, data))
    if not matches:
        return []
    approved = reviewed.get(name, {})
    if approved.get("sha256") == digest(data) and matches == sorted(approved.get("rules", [])):
        return []
    return matches


def is_binary(data: bytes) -> bool:
    try:
        return "\0" in data.decode("utf-8")
    except UnicodeDecodeError:
        return True


def export_requests(files: dict[str, tuple[str, bytes]], output: Path) -> dict:
    """Write content-addressed manifests and bounded Git Data API request fragments.

    No repository, branch, credentials or remote mutation is embedded. Large/binary
    files require create_blob first; returned SHA must equal the local Git blob ID.
    Remaining text entries may be batched with create_tree, carrying its base tree.
    """
    if output.exists():
        raise ValueError("Use a fresh output directory")
    output.mkdir(parents=True)
    entries, batch, batch_bytes, batch_number, blobs = [], [], 0, 0, []
    file_manifest = {}
    for name, (mode, data) in sorted(files.items()):
        safe_relative(name)
        sha1 = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        file_manifest[name] = {"sha256": digest(data), "git_blob": sha1, "bytes": len(data), "mode": mode}
        entry = {"path": name, "mode": mode, "type": "blob"}
        try:
            text = data.decode("utf-8")
            binary = "\0" in text
        except UnicodeDecodeError:
            text, binary = "", True
        if binary or len(data) > 512_000:
            request_name = f"blobs/{sha1}.json"
            if sha1 not in {item["expected_sha"] for item in blobs}:
                dump(output / request_name, {
                    "content": base64.b64encode(data).decode("ascii") if binary else text,
                    "encoding": "base64" if binary else "utf-8",
                })
                blobs.append({"file": request_name, "expected_sha": sha1, "bytes": len(data)})
            entry["sha"] = sha1
        else:
            entry["content"] = text
        estimated = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        if batch and batch_bytes + estimated > 1_000_000:
            dump(output / f"trees/{batch_number:03d}.json", {"tree_elements": batch})
            batch_number += 1
            batch, batch_bytes = [], 0
        batch.append(entry)
        batch_bytes += estimated
        entries.append({key: value for key, value in entry.items() if key != "content"} | {"sha": sha1})
    if batch:
        dump(output / f"trees/{batch_number:03d}.json", {"tree_elements": batch})
        batch_number += 1
    summary = {
        "schema_version": 1,
        "files": len(files),
        "bytes": sum(len(data) for _, data in files.values()),
        "blob_requests": blobs,
        "tree_batches": batch_number,
        "contains_git_history": False,
        "remote_mutations_performed": False,
    }
    dump(output / "manifest.json", {"summary": summary, "files": file_manifest})
    dump(output / "expected-tree.json", {"tree_elements": entries})
    return summary


def source(repo: Path, output: Path) -> dict:
    if git(repo, "status", "--porcelain"):
        raise ValueError("Commit source changes before preparing publishing input")
    reviewed = json.loads((repo / "deploy/git-sync/reviewed-fixtures.json").read_text(encoding="utf-8"))
    assets = json.loads((repo / "deploy/git-sync/reviewed-source-assets.json").read_text(encoding="utf-8"))
    files, blocked = {}, []
    for row in git(repo, "ls-tree", "-rz", "HEAD").split(b"\0"):
        if not row:
            continue
        meta, raw_path = row.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        name = raw_path.decode()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Unsupported source entry: {name}")
        if not source_path_allowed(name):
            blocked.append({"path": name, "reason": "forbidden-path"})
            continue
        data = git(repo, "cat-file", "blob", sha)
        if is_binary(data) and assets.get(name, {}).get("sha256") != digest(data):
            blocked.append({"path": name, "reason": "unreviewed-binary"})
            continue
        findings = scan(name, data, reviewed)
        if findings:
            blocked.append({"path": name, "rules": findings})
            continue
        files[name] = (mode, data)
    if blocked:
        # Never emit matching secret values or silently delete files from a release.
        raise ValueError(json.dumps({"blocked": blocked}, ensure_ascii=False))
    summary = export_requests(files, output)
    dump(output / "source.json", {"source_commit": git(repo, "rev-parse", "HEAD").decode().strip(), **summary})
    return summary


def read_archive(path: Path, expected: str, prefix: str) -> dict[str, tuple[str, bytes]]:
    if digest(path.read_bytes()) != expected:
        raise ValueError("Artifact checksum mismatch")
    files = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = safe_relative(member.filename)
            if member.is_dir():
                raise ValueError("Unexpected directory record")
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive links are forbidden")
            target = prefix + "/" + name
            if target in files:
                raise ValueError("Duplicate archive member")
            files[target] = ("100644", archive.read(member))
    return files


def release(repo: Path, patch: Path, smoke: Path, output: Path) -> dict:
    files = read_archive(patch, PATCH_SHA256, "patch")
    files.update(read_archive(smoke, SMOKE_SHA256, "smoke"))
    plan = json.loads(files["patch/patch.json"][1])
    if plan["base_zip_sha256"] != BASE_SHA256:
        raise ValueError("Unexpected base package")
    for direction in ("before", "after"):
        for name, expected in plan[direction].items():
            safe_relative(name)
            if digest(files[f"patch/{direction}/{name}"][1]) != expected:
                raise ValueError("Patch manifest mismatch")
    smoke_manifest = json.loads(files["smoke/manifest.json"][1])
    for name, expected in smoke_manifest["files"].items():
        safe_relative(name)
        if digest(files[f"smoke/{name}"][1]) != expected:
            raise ValueError("Smoke manifest mismatch")
    for relative in ("verify_release.py", "verify.cmd", "RELEASE_README.md"):
        target = "README.md" if relative == "RELEASE_README.md" else relative
        files[target] = ("100644", (repo / "deploy/git-sync" / relative).read_bytes())
    files[".gitattributes"] = ("100644", b"* -text\n")
    files["LICENSE"] = ("100644", (repo / "projects/growth/LICENSE").read_bytes())
    manifest = {
        "schema_version": 1,
        "version": VERSION,
        "delivery": "unpacked finalized cloud artifacts; no Windows build",
        "source_commit": git(repo, "rev-parse", "HEAD").decode().strip(),
        "adapter_source_commit": plan["source_commit"],
        "base_archive_sha256": BASE_SHA256,
        "original_archives": {"patch_sha256": PATCH_SHA256, "smoke_sha256": SMOKE_SHA256},
        "runtime_included": False,
        "actual_windows_test_status": "not established by this packaging operation",
        "files": {name: digest(data) for name, (_, data) in sorted(files.items())},
    }
    files["delivery.json"] = ("100644", (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
    summary = export_requests(files, output)
    tree = output / "checkout"
    for name, (_, data) in files.items():
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["source", "release"])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--smoke", type=Path)
    args = parser.parse_args()
    if args.kind == "source":
        result = source(args.repo.resolve(), args.output.resolve())
    else:
        if not args.patch or not args.smoke:
            parser.error("release requires --patch and --smoke")
        result = release(args.repo.resolve(), args.patch, args.smoke, args.output.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
