"""Hosted-only dependency preparation followed by a real Linux sandbox.

No deploy, model, real user inputs, Git write or Windows delivery operation.
This driver never changes product isolation_attested receipts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys


SOURCE_PREFIXES = (
    "projects/growth/src/", "projects/selfweb/site/",
    "projects/selfweb/scripts/", "projects/selfweb/docs/build-assets/",
    "deploy/c20-artifact-only/",
)


def run(argv: list[str], cwd: Path) -> None:
    subprocess.run(argv, cwd=cwd, check=True)


def load_font_preparer(repo: Path):
    """Resolve the reviewed helper's sibling imports, without lasting sys.path changes."""
    helper = repo / "deploy/windows-portable/build_github_product.py"
    original_path = sys.path[:]
    try:
        sys.path.insert(0, str(helper.parent))
        return runpy.run_path(str(helper))["fonts"]
    finally:
        sys.path[:] = original_path


def copy_tracked(repo: Path, destination: Path) -> None:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo).decode().split("\0")
    for name in names:
        if not name.startswith(SOURCE_PREFIXES):
            continue
        source = repo / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("Source snapshot contains an unsupported file")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def sandbox_command(stage: Path, work: Path, source: str, python_base: Path,
                    node_bin: Path, host_net: str) -> list[str]:
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
               "--cap-drop", "ALL"]
    for directory in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(directory).exists():
            command += ["--ro-bind", directory, directory]
    command += ["--dir", "/etc"]
    for name in ("/etc/ld.so.cache", "/etc/localtime"):
        if Path(name).is_file():
            command += ["--ro-bind", name, name]
    # Only the selected interpreter runtime and Node executable directory, never
    # the user's home, the toolcache root, credential store or Docker socket.
    command += ["--ro-bind", str(python_base), "/runtime/python",
                "--ro-bind", str(node_bin), "/runtime/node"]
    command += ["--ro-bind", str(stage), "/source", "--bind", str(work / "result"), "/work",
                "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev"]
    for relative in ("quartz/.quartz-cache", ".quartz-cache"):
        command += ["--bind", str(stage / "projects/selfweb/site" / relative),
                    "/source/projects/selfweb/site/" + relative]
    environment = {
        "PATH": "/runtime/node:/usr/bin:/bin", "HOME": "/tmp/home", "TMPDIR": "/tmp",
        "LANG": "C.UTF-8", "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": "/source/projects/growth/src", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        # These are the actual hosted runner values, checked before preparation.
        "GITHUB_ACTIONS": os.environ["GITHUB_ACTIONS"],
        "RUNNER_ENVIRONMENT": os.environ["RUNNER_ENVIRONMENT"],
        "C20_HOST_NET_NAMESPACE": host_net,
    }
    for key, value in environment.items():
        command += ["--setenv", key, value]
    return command + ["--chdir", "/source", "/source/projects/growth/.venv/bin/python",
                      "/source/deploy/c20-artifact-only/prepare_and_run.py", "--inside",
                      "--work", "/work", "--source", source]


def inside(work: Path, source: str) -> None:
    """Fail closed if kernel network isolation or mount hygiene is absent."""
    interfaces = sorted(line.split(":", 1)[0].strip() for line in
                        Path("/proc/net/dev").read_text().splitlines()[2:])
    route_lines = Path("/proc/net/route").read_text().splitlines()[1:]
    ipv6_routes = Path("/proc/net/ipv6_route").read_text().splitlines()
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                  if ":" in line)
    capabilities = {key: status[key].strip() for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")}
    namespace = os.readlink("/proc/self/ns/net")
    if namespace == os.environ["C20_HOST_NET_NAMESPACE"] or interfaces != ["lo"] or route_lines:
        raise ValueError("Kernel network isolation was not established")
    if any(not line.split() or line.split()[-1] != "lo" for line in ipv6_routes):
        raise ValueError("Unexpected IPv6 network route")
    if any(int(capabilities[key], 16) for key in ("CapInh", "CapPrm", "CapEff", "CapAmb")):
        raise ValueError("Unexpected sandbox process capabilities")
    for prohibited in ("/home", "/root", "/run/docker.sock", "/source/.git"):
        if Path(prohibited).exists():
            raise ValueError("Unexpected host or credential mount")
    Path("/tmp/home").mkdir()
    evidence = {
        "scope": "synthetic canonical Git/SQLite and real Quartz; not user or Windows E2E",
        "source_commit": source, "network_namespace": namespace,
        "host_network_namespace": os.environ["C20_HOST_NET_NAMESPACE"],
        "interfaces": interfaces, "routes": route_lines, "ipv6_routes": ipv6_routes,
        "uid": os.getuid(), "gid": os.getgid(), "capabilities": capabilities,
        "environment_names": sorted(os.environ),
        "mountinfo": Path("/proc/self/mountinfo").read_text(),
        "no_deployment": True,
    }
    (work / "isolation.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    run([sys.executable, "/source/deploy/c20-artifact-only/acceptance.py",
         "--work", str(work / "acceptance"), "--source", source], Path("/source"))


def require_hosted_source(repo: Path, source: str) -> None:
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("GITHUB_REPOSITORY") != "sep-zxy/zhizhi-platform"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_SHA") != source
            or not re.fullmatch(r"[0-9a-f]{40}", source)):
        raise ValueError("Only the bound manual GitHub-hosted workflow may execute")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() != source:
        raise ValueError("Source mismatch")


def prepare(repo: Path, work: Path, source: str) -> None:
    require_hosted_source(repo, source)
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=repo):
        raise ValueError("Preparation requires an unmodified source checkout")
    if work.exists():
        raise ValueError("A fresh isolated work directory is required")
    work.mkdir(parents=True)
    result = work / "result"
    result.mkdir()
    (result / "scope.json").write_text(json.dumps({
        "source_commit": source, "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"], "github_hosted": True,
        "live_model": "not_run", "live_channels": "not_run", "windows_e2e": "not_run",
        "deployment": "not_run", "status": "preparation_started",
    }, indent=2), encoding="utf-8")
    site = repo / "projects/selfweb/site"
    npm = ["npm", "ci", "--ignore-scripts", "--no-audit", "--fund=false"]
    run(npm, site)
    vendored = json.loads((site / ".quartz/vendored-source-manifest.json").read_text())
    for name in sorted(vendored["plugins"]):
        if not re.fullmatch(r"[a-z0-9-]+", name):
            raise ValueError("Invalid vendored plugin path")
        plugin = site / ".quartz/plugins" / name
        run(npm, plugin)
        run(["npm", "run", "build"], plugin)
    # Existing audited helper downloads exact reviewed font URLs and verifies
    # hashes/licenses. This happens before the network namespace is created.
    load_font_preparer(repo)(repo, work)
    # Dependency preparation must not silently change the reviewed tracked
    # source copied into the isolated executor. Generated ignored outputs are
    # handled separately below, never mistaken for committed source.
    run(["git", "diff", "--exit-code", "HEAD", "--"], repo)
    stage = work / "source"
    stage.mkdir()
    copy_tracked(repo, stage)
    # Copy only installed runtime/dependency trees, no checkout .git/config.
    shutil.copytree(repo / "projects/growth/.venv", stage / "projects/growth/.venv", symlinks=True)
    # Relocate the selected runtime to a neutral mountpoint; uv-managed Python
    # can live under /home on the host, which must never be mounted wholesale.
    venv = stage / "projects/growth/.venv"
    config = venv / "pyvenv.cfg"
    config.write_text(config.read_text(encoding="utf-8").replace(
        str(Path(sys.base_prefix).resolve()), "/runtime/python"), encoding="utf-8")
    for executable in (venv / "bin").glob("python*"):
        if executable.is_symlink():
            executable.unlink()
            executable.symlink_to("/runtime/python/bin/" + Path(sys._base_executable).resolve().name)
    for node_modules in [site / "node_modules", *(site / ".quartz/plugins").glob("*/node_modules")]:
        shutil.copytree(node_modules, stage / node_modules.relative_to(repo), symlinks=True)
    for dist in (site / ".quartz/plugins").glob("*/dist"):
        target = stage / dist.relative_to(repo)
        shutil.copytree(dist, target, dirs_exist_ok=True)
    for relative in ("quartz/.quartz-cache", ".quartz-cache"):
        target = stage / "projects/selfweb/site" / relative
        if (site / relative).exists():
            shutil.copytree(site / relative, target, dirs_exist_ok=True)
        else:
            target.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node")
    if not node:
        raise ValueError("Node missing")
    (work / "prepared.json").write_text(json.dumps({
        "source_commit": source, "python_base": str(Path(sys.base_prefix).resolve()),
        "node_bin": str(Path(node).resolve().parent),
    }), encoding="utf-8")


def execute(repo: Path, work: Path, source: str) -> None:
    require_hosted_source(repo, source)
    prepared = json.loads((work / "prepared.json").read_text(encoding="utf-8"))
    node = shutil.which("node")
    if (prepared["source_commit"] != source
            or prepared["python_base"] != str(Path(sys.base_prefix).resolve())
            or not node or prepared["node_bin"] != str(Path(node).resolve().parent)):
        raise ValueError("Prepared runtime identity changed")
    command = sandbox_command(work / "source", work, source, Path(prepared["python_base"]),
                              Path(prepared["node_bin"]), os.readlink("/proc/self/ns/net"))
    run(command, repo)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--phase", choices=("prepare", "execute"))
    args = parser.parse_args()
    if args.inside:
        inside(args.work, args.source)
    else:
        if not args.repo:
            parser.error("--repo is required for preparation")
        if not args.phase:
            parser.error("--phase is required outside the sandbox")
        operation = prepare if args.phase == "prepare" else execute
        operation(args.repo.resolve(), args.work.resolve(), args.source)
