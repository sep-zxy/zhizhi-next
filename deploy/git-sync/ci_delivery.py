"""Stage and atomically publish GitHub-built Windows candidates, never final E2E claims."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from source_bootstrap import REPOSITORY, exact_hex, git, json_bytes, push_exact_parent, safe
from verify_current import verify

MAX_FILE_BYTES = 95_000_000
MAX_PRODUCT_BYTES = 2_000_000_000
STAGE_ROOT_FILES = {
    ".gitattributes", ".gitignore", "current.json", "verify_current.py", "verify.cmd", "README.md",
}
PRIVATE_DIRECTORIES = ("data", "temp", "cache", "reports", "browser-profile")
REQUIRED_PHASES = {
    "backend-isolated-fixtures", "graphify-performance-gate",
    "delivery-unit-gates", "portable-assembly-unit-gates",
    "viewer-typecheck", "viewer-tests", "viewer-production-build",
    "private-wiki-locked-install", "private-wiki-types", "private-wiki-tests",
    "private-wiki-domain-capture", "private-wiki-domain-contract",
    "private-wiki-production-build",
    "private-wiki-browser-install", "private-wiki-browser-fixtures",
    "pi-types", "pi-tests", "pi-runtime-build", "pi-portable-tests", "licensed-fonts-exact-hash",
    "wiki-types", "wiki-core-tests", "wiki-independent-template-capture",
    "wiki-runtime-compile", "wiki-runtime-package", "wiki-dynamic-fixture-gates",
    "wiki-windows-native-exact-lock", "windows-locked-wheels",
    "windows-official-runtimes", "assemble-windows-product",
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_bundle(bundle: Path, receipt: dict, expected_source: str) -> dict:
    exact_hex(expected_source, 40)
    if (receipt.get("schema_version") != 1 or receipt.get("status") != "ci_candidate_built"
            or receipt.get("runner") != "github-hosted"
            or receipt.get("repository") != REPOSITORY
            or receipt.get("event") != "workflow_dispatch"
            or receipt.get("source_commit") != expected_source
            or receipt.get("final_delivery") is not False
            or receipt.get("windows_e2e") != "not_run"):
        raise ValueError("Not an attributable GitHub build candidate")
    phases = receipt["phases"]
    names = [phase["phase"] for phase in phases]
    if (len(names) != len(set(names)) or not REQUIRED_PHASES <= set(names)
            or any(phase["status"] != "passed" for phase in phases)):
        raise ValueError("Required CI build or test gate did not pass")
    embedded = json.loads(safe(bundle, "provenance/github-build.json").read_text())
    for key in ("repository", "source_commit", "run_id", "run_attempt", "workflow",
                "runner", "event", "status", "phases", "final_delivery", "windows_e2e"):
        if embedded.get(key) != receipt.get(key):
            raise ValueError("Build receipt differs from embedded provenance")
    distribution_path = safe(bundle, "provenance/distribution.json")
    if sha(distribution_path) != receipt["distribution_sha256"]:
        raise ValueError("Distribution seal differs from build receipt")
    distribution = json.loads(distribution_path.read_text())
    if distribution.get("schema_version") != 1 or distribution.get("target") != "windows-x64":
        raise ValueError("Wrong distribution target")
    assembly = json.loads(safe(bundle, "provenance/assembly.json").read_text())
    if (assembly.get("assembly_source_commit") != expected_source
            or assembly.get("backend_source_commit") != expected_source
            or not {"pi", "wiki", "wiki-private"} <= set(assembly.get("components", {}))):
        raise ValueError("Missing components or inconsistent source commit")
    for component in ("pi", "wiki", "wiki-private"):
        if assembly["components"][component].get("source_commit") != expected_source:
            raise ValueError("A component came from a different source commit")
    config = json.loads(safe(bundle, "config/launch.json").read_text())
    if (any(config.get(key) is not None for key in
            ("owner", "identities_file", "model_config", "desktop_config", "pi_environment_file"))
            or config.get("providers") != [] or config.get("run_worker") is not False
            or config.get("auto_commit") is not False):
        raise ValueError("Only credential-free inactive default configuration may ship")
    expected = distribution["files"]
    actual, total = {}, 0
    for path in sorted(bundle.rglob("*")):
        name = path.relative_to(bundle).as_posix()
        safe(bundle, name)
        if not path.is_file():
            continue
        if name.startswith(("data/", "temp/", "cache/", "reports/", "browser-profile/")):
            raise ValueError("Private or runtime state in candidate")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("File exceeds approved direct Git delivery size: " + name)
        total += path.stat().st_size
        if total > MAX_PRODUCT_BYTES:
            raise ValueError("Candidate exceeds reviewed total size boundary")
        actual[name] = sha(path)
    if set(actual) != set(expected) | {"config/launch.json", "provenance/distribution.json"}:
        raise ValueError("Unexpected or missing product files")
    if any(actual[name] != value for name, value in expected.items()):
        raise ValueError("Product checksum mismatch")
    required = {"start.cmd", "stop.cmd", "diagnostics.cmd", "app/launcher.py",
                "app/viewer/index.html", "runtime/python/python.exe", "runtime/node/node.exe",
                "runtime/git/cmd/git.exe", "app/backend/LICENSE",
                "app/pi/component.json", "app/wiki/component.json", "app/wiki-private/index.html",
                "codex-diagnostic.cmd", "app/backend/ahadiff/knowledge_platform/model/diagnostic_probe.py",
                "local-test-initialize.cmd", "local-test-pair.cmd", "local-test-start.cmd",
                "app/backend/ahadiff/knowledge_platform/local_test_identity.py",
                "app/backend/ahadiff/knowledge_platform/browser_sessions.py"}
    if not required <= set(actual):
        raise ValueError("Candidate lacks required runnable components")
    return {"files": actual, "bytes": total}


def stage(bundle: Path, receipt: dict, expected_source: str, output: Path,
          maximum_product_bytes: int = MAX_PRODUCT_BYTES) -> dict:
    verified = validate_bundle(bundle, receipt, expected_source)
    if (type(maximum_product_bytes) is not int
            or not 1 <= maximum_product_bytes <= MAX_PRODUCT_BYTES
            or verified["bytes"] > maximum_product_bytes):
        raise ValueError("Product exceeds the dispatch-approved Git publication limit")
    if output.exists():
        raise ValueError("Refusing to overwrite candidate stage")
    run_id, attempt = str(receipt["run_id"]), str(receipt["run_attempt"])
    if not re.fullmatch(r"[0-9]+", run_id) or not re.fullmatch(r"[0-9]+", attempt):
        raise ValueError("Invalid GitHub run identity")
    release_id = f"{expected_source[:12]}-{run_id}-{attempt}"
    output.mkdir(parents=True)
    release = output / "releases" / release_id
    shutil.copytree(bundle, release)
    manifest = {
        "schema_version": 1, "release_id": release_id, "source_commit": expected_source,
        "github_run_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
        "github_run_id": run_id, "github_run_attempt": attempt,
        "distribution_sha256": receipt["distribution_sha256"],
        "status": "candidate_for_windows_acceptance", "final_delivery": False,
        "windows_e2e": "not_run", "live_channels": "not_run", "live_model": "not_run",
        "files": verified["files"],
    }
    (release / "release.json").write_bytes(json_bytes(manifest))
    pointer = {"schema_version": 1, "release_id": release_id, "source_commit": expected_source,
               "release_manifest_sha256": sha(release / "release.json"),
               "status": "candidate_for_windows_acceptance", "final_delivery": False}
    (output / "current.json").write_bytes(json_bytes(pointer))
    (output / ".gitattributes").write_bytes(b"* -text\n")
    (output / ".gitignore").write_text(
        "# Local runtime state and test identities never belong in delivery Git.\n"
        + "".join(f"/{name}/\n/releases/*/{name}/\n" for name in PRIVATE_DIRECTORIES),
        encoding="ascii",
    )
    shutil.copyfile(Path(__file__).with_name("verify_current.py"), output / "verify_current.py")
    (output / "verify.cmd").write_bytes(
        b'@echo off\r\nsetlocal\r\n'
        b'"C:\\Code\\zhizhi-e2e-20261020\\runtime\\python\\python.exe" -B -s -X utf8 '
        b'"%~dp0verify_current.py" --root "%~dp0." %*\r\nexit /b %ERRORLEVEL%\r\n')
    (output / "README.md").write_text(
        "# GitHub 构建的 Windows 验收候选\n\n"
        "固定本交付分支的完整 commit 后，运行 verify.cmd --expected-commit <完整commit>。"
        "它使用既有验收包的 Python，只读校验，不构建、安装、登录或迁移配置。\n\n"
        f"当前版本为 releases/{release_id}。完整性通过后，在本轮明确授权的验收范围内"
        "运行该版本目录的 diagnostics.cmd / start.cmd。首次默认为 waiting_auth，"
        "不得复制凭据或自行改写配置来绕过授权。应用状态只写入该版本自己的目录；"
        "不会覆盖旧包数据。实际身份/状态迁移需单独协调。\n\n"
        "这个分支是测试用候选，不代表最终交付。CI、fixture、真实 Windows、"
        "模型、渠道、ASR 和动态 Wiki 验收分别记录。未通过全部必需真实 E2E 前不得宣布交付。\n\n"
        "历史版本和 Git 父提交保留；停止新版本后可检出旧固定 commit 运行旧版本。"
        "不在运行中的目录切换 Git。候选运行后会生成状态文件，因此首次 pristine 校验"
        "应在运行前完成；日后使用应用 diagnostics 检查 immutable 文件。\n",
        encoding="utf-8")
    verify(output)
    return {"release_id": release_id, "product_files": len(verified["files"]),
            "product_bytes": verified["bytes"], "source_commit": expected_source,
            "status": "candidate_staged_not_published", "final_delivery": False}


def validate_stage_files(staged: Path, release_id: str) -> None:
    """No surrounding files may hitchhike on a valid, sealed release."""
    release = safe(staged, "releases/" + release_id)
    manifest = json.loads(safe(release, "release.json").read_text())
    expected = STAGE_ROOT_FILES | {
        "releases/" + release_id + "/" + name
        for name in set(manifest["files"]) | {"release.json"}
    }
    observed = set()
    for path in staged.rglob("*"):
        name = path.relative_to(staged).as_posix()
        safe(staged, name)
        if path.is_file():
            observed.add(name)
    if observed != expected:
        raise ValueError("Unexpected or missing file outside the sealed candidate")


def publish(staged: Path, expected_delivery: str, expected_source: str) -> dict:
    exact_hex(expected_delivery, 40)
    exact_hex(expected_source, 40)
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_SHA") != expected_source):
        raise ValueError("Only this source's manual GitHub-hosted workflow may publish")
    result = verify(staged)
    validate_stage_files(staged, result["release_id"])
    pointer = json.loads((staged / "current.json").read_text())
    release = json.loads((staged / "releases" / result["release_id"] / "release.json").read_text())
    if (pointer["source_commit"] != expected_source
            or release["github_run_id"] != os.environ.get("GITHUB_RUN_ID")
            or release["github_run_attempt"] != os.environ.get("GITHUB_RUN_ATTEMPT")):
        raise ValueError("Candidate does not belong to this workflow run")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("Ephemeral job token required")
    auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
    env = {
        **os.environ, "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + auth,
        "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": "",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    }
    remote = f"https://github.com/{REPOSITORY}.git"
    target = "refs/heads/windows-delivery"
    with tempfile.TemporaryDirectory(prefix="github-delivery-") as directory:
        repo = Path(directory)
        git(repo, "init", "--bare")
        observed = git(repo, "ls-remote", remote, target, env=env).decode().split()
        if observed != [expected_delivery, target]:
            raise ValueError("Delivery branch moved; no files or refs published")
        git(repo, "fetch", "--no-tags", "--filter=blob:none", remote, expected_delivery, env=env)
        index_env = {**env, "GIT_INDEX_FILE": str(repo / "delivery.index")}
        git(repo, "read-tree", expected_delivery, env=index_env)
        old = git(repo, "ls-tree", "-r", expected_delivery, "--", "releases/" + result["release_id"]).strip()
        if old:
            raise ValueError("Immutable release identifier already exists")
        entries = bytearray()
        for path in sorted(staged.rglob("*")):
            if path.is_file():
                name = path.relative_to(staged).as_posix()
                safe(staged, name)
                blob = git(repo, "hash-object", "-w", "--stdin", data=path.read_bytes()).decode().strip()
                entries.extend(f"100644 {blob}\t{name}".encode() + b"\0")
        git(repo, "update-index", "-z", "--index-info", data=entries, env=index_env)
        tree = git(repo, "write-tree", env=index_env).decode().strip()
        message = (
            "发布GitHub构建的Windows验收候选\n\n"
            f"Source-Commit: {expected_source}\nGitHub-Run: {release['github_run_url']}\n"
            f"Release: {result['release_id']}\nPrevious-Delivery: {expected_delivery}\n"
            "Final-Delivery: false\nWindows-E2E: not_run\n"
        )
        commit = git(repo, "commit-tree", tree, "-p", expected_delivery,
                     data=message.encode(), env=env).decode().strip()
        # Direct-parent verification plus an exact lease prevents all concurrent
        # ref movements, without authorizing history rewrites or old-file deletion.
        push_exact_parent(repo, remote, target, commit, expected_delivery, env)
        observed = git(repo, "ls-remote", remote, target, env=env).decode().split()
        if observed != [commit, target]:
            raise ValueError("Delivery push uncertain; inspect remote before retry")
    return {**result, "status": "candidate_git_published", "delivery_commit": commit,
            "previous_delivery_commit": expected_delivery, "tree": tree,
            "windows_e2e": "not_run", "final_delivery": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--build-receipt", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--publish-expected-delivery")
    parser.add_argument("--maximum-product-bytes", type=int, default=MAX_PRODUCT_BYTES)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    build_receipt = json.loads(args.build_receipt.read_text())
    result = stage(args.bundle, build_receipt, args.expected_source, args.stage,
                   args.maximum_product_bytes)
    if args.publish_expected_delivery:
        result = publish(args.stage, args.publish_expected_delivery, args.expected_source)
    args.receipt.write_bytes(json_bytes(result))
    print(json.dumps(result, ensure_ascii=False))
