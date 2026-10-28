"""Explicitly approved Docker executor; never a fallback or daemon configurator.

The host daemon is a root-capable control plane. Payloads are non-root, with no
network, capabilities or credential mounts. Default userns remapping is NOT
assumed. Only the GitHub-hosted workflow may call the control-plane functions.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

IMAGE = "docker.io/library/ubuntu@sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d"
IMAGE_ID = "sha256:d9b8c34d61a9b9a3c44cab883d7d2f2619d674966ab520186f799cf32e3e0377"
MEMORY = 6 * 1024**3
TMPFS = "rw,nosuid,nodev,size=1073741824,mode=1777"
SECURITY = ["no-new-privileges=true", "apparmor=docker-default"]
CACHE_PATHS = ("quartz/.quartz-cache", ".quartz-cache")
DEFAULT_MASKS = frozenset({"/proc/acpi", "/proc/asound", "/proc/interrupts", "/proc/kcore",
    "/proc/keys", "/proc/latency_stats", "/proc/timer_list", "/proc/timer_stats", "/proc/sched_debug",
    "/proc/scsi", "/sys/firmware", "/sys/devices/virtual/powercap"})
DEFAULT_READONLY = frozenset({"/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger"})
REQUIRED_MASKS = frozenset({"/proc/kcore", "/proc/keys", "/proc/timer_list", "/sys/firmware"})
IMPLICIT_MOUNTS = frozenset({"/", "/proc", "/dev", "/dev/pts", "/sys", "/sys/fs/cgroup",
    "/dev/mqueue", "/dev/shm", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf"})


def require(value, reason):
    if not value:
        raise ValueError(reason)


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def checked_directory(path):
    path = Path(path).absolute()
    require(path.is_dir() and path.resolve() == path, "Noncanonical mount directory")
    require(all(not p.is_symlink() for p in (path, *path.parents)), "Mount ancestor symlink")
    return path


def require_hosted():
    require(os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == "sep-zxy/zhizhi-platform"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main", "GitHub-hosted manual main required")


def control(work, args, *, capture=True, timeout=120):
    require_hosted()
    config = checked_directory(work / "docker-config")
    require(not list(config.iterdir()), "Docker config must remain empty")
    # No inherited proxy, context, TLS, login or SSH environment. Public pull only.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(config), "DOCKER_CONFIG": str(config), "LANG": "C.UTF-8"}
    try:
        return subprocess.run(["/usr/bin/docker", "--config", str(config), "--host", "unix:///var/run/docker.sock", *args],
            env=env, stdin=subprocess.DEVNULL, capture_output=capture, text=True, check=True, timeout=timeout)
    except subprocess.CalledProcessError as error:
        stderr = error.stderr or ""
        categories = [item for item in ("permission denied", "operation not permitted", "unknown flag",
            "invalid mount", "no such image", "cannot connect", "not found") if item in stderr.lower()]
        # No raw daemon error/argv/env/body is reprinted as a workflow command.
        print(json.dumps({"event": "docker_control_failed", "operation": args[0], "exit_code": error.returncode,
            "categories": categories, "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest()}), flush=True)
        raise


def check_daemon(work):
    security = json.loads(control(work, ["info", "--format", "{{json .SecurityOptions}}"] ).stdout)
    require("name=apparmor" in security and "name=seccomp,profile=builtin" in security,
            "Default AppArmor and built-in seccomp required")
    return security


def check_image(work):
    info = json.loads(control(work, ["image", "inspect", IMAGE]).stdout)
    require(len(info) == 1 and info[0]["Id"] == IMAGE_ID, "Pinned image ID mismatch")
    require(info[0]["Architecture"] == "amd64" and info[0]["Os"] == "linux", "Image platform mismatch")
    require(not info[0]["Config"].get("Volumes"), "Image-declared volumes forbidden")
    return IMAGE_ID


def prepare_image(work):
    require_hosted()
    checked_directory(work)
    (work / "host-evidence").mkdir(mode=0o700)
    (work / "docker-config").mkdir(mode=0o700)
    security = check_daemon(work)
    control(work, ["pull", "--platform", "linux/amd64", IMAGE], capture=False, timeout=300)
    check_image(work)
    write_json(work / "docker-image.json", {"image": IMAGE, "image_id": IMAGE_ID,
        "daemon_security_options": security, "host_root_control_plane": True,
        "payload_userns_remap_assumed": False})


def mount_plan(stage, work, python_base, node_bin):
    stage, work = checked_directory(stage), checked_directory(work)
    require(stage == work / "source", "Unexpected staged source")
    pairs = [(Path("/usr"), "/usr", False), (python_base, "/runtime/python", False),
             (node_bin, "/runtime/node", False), (stage, "/source", False),
             (work / "result", "/work", True)]
    pairs += [(stage / "projects/selfweb/site" / p, "/source/projects/selfweb/site/" + p, True) for p in CACHE_PATHS]
    return [(str(checked_directory(src)), dst, writable) for src, dst, writable in pairs]


def payload_environment(source, host_namespaces, uid, gid):
    require(re.fullmatch(r"[0-9a-f]{40}", source), "Invalid source")
    return {"PATH": "/runtime/node:/usr/bin:/bin", "HOME": "/tmp/home", "TMPDIR": "/tmp",
        "LANG": "C.UTF-8", "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": "/source/projects/growth/src", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GITHUB_ACTIONS": os.environ["GITHUB_ACTIONS"], "RUNNER_ENVIRONMENT": os.environ["RUNNER_ENVIRONMENT"],
        "C20_SOURCE": source, "C20_HOST_NAMESPACES": json.dumps(host_namespaces, sort_keys=True),
        "C20_RUN_ID": os.environ["GITHUB_RUN_ID"], "C20_RUN_ATTEMPT": os.environ["GITHUB_RUN_ATTEMPT"],
        "C20_UID": str(uid), "C20_GID": str(gid)}


def labels(env):
    return {"c20.source": env["C20_SOURCE"], "c20.run_id": env["C20_RUN_ID"], "c20.run_attempt": env["C20_RUN_ATTEMPT"]}


def create_arguments(plan, env, uid, gid):
    require(uid > 0 and gid > 0, "Non-root numeric user required")
    args = ["create", "--pull", "never", "--platform", "linux/amd64", "--network", "none", "--read-only",
        "--user", f"{uid}:{gid}", "--cap-drop", "ALL", "--ipc", "private", "--cgroupns", "private",
        "--cpus", "2", "--memory", str(MEMORY), "--memory-swap", str(MEMORY), "--pids-limit", "128",
        "--restart", "no", "--no-healthcheck", "--ulimit", "core=0", "--shm-size", "67108864",
        "--tmpfs", "/tmp:" + TMPFS, "--workdir", "/source", "--entrypoint", "/usr/bin/env"]
    for opt in SECURITY:
        args += ["--security-opt", opt]
    for key, value in labels(env).items():
        args += ["--label", f"{key}={value}"]
    for src, dst, writable in plan:
        require("," not in src and "\n" not in src, "Unsafe mount source")
        args += ["--mount", f"type=bind,src={src},dst={dst},bind-propagation=rprivate,bind-recursive=disabled" + ("" if writable else ",readonly")]
    # env -i additionally removes image-defined environment. Values are explicit,
    # not -e NAME pass-through, and do not include any GitHub or Docker token.
    return args + [IMAGE, "-i", *(f"{k}={v}" for k, v in env.items()),
        "/source/projects/growth/.venv/bin/python", "/source/deploy/c20-artifact-only/docker_executor.py", "--inside"]


def validate_inspect(data, plan, uid, gid, env, *, prestart=False):
    h, c = data["HostConfig"], data["Config"]
    require(data["Image"] == IMAGE_ID and c["Image"] == IMAGE, "Container image mismatch")
    all_labels = c.get("Labels", {})
    expected_labels = labels(env)
    image_labels = {"org.opencontainers.image.ref.name": "ubuntu", "org.opencontainers.image.version": "24.04"}
    require(all(all_labels.get(k) == v for k, v in expected_labels.items())
        and set(all_labels) <= set(expected_labels) | set(image_labels)
        and all(all_labels[k] == image_labels[k] for k in set(all_labels) & set(image_labels)), "Container ownership labels mismatch")
    require(c["Entrypoint"] == ["/usr/bin/env"] and c["Cmd"] == ["-i", *(f"{k}={v}" for k, v in env.items()),
        "/source/projects/growth/.venv/bin/python", "/source/deploy/c20-artifact-only/docker_executor.py", "--inside"], "Payload command mismatch")
    require(c["User"] == f"{uid}:{gid}" and h["ReadonlyRootfs"] and not h["Privileged"], "Container privilege mismatch")
    require(h["NetworkMode"] == "none" and not h.get("PortBindings") and not h.get("PublishAllPorts"), "Container network mismatch")
    require(h.get("PidMode", "") == "" and h.get("UTSMode", "") == "" and h["IpcMode"] == "private"
            and h["CgroupnsMode"] == "private" and h.get("UsernsMode", "") == "", "Shared namespace override")
    require(h.get("CapDrop") == ["ALL"] and not h.get("CapAdd") and not h.get("GroupAdd")
            and not h.get("Devices") and not h.get("DeviceRequests") and not h.get("DeviceCgroupRules"), "Extra device/group/capability")
    require(set(h["SecurityOpt"]) == set(SECURITY)
        and data["AppArmorProfile"] in (("", "docker-default") if prestart else ("docker-default",)), "Security policy override")
    require(not h.get("Binds") and not h.get("VolumesFrom") and not h.get("Links") and not h.get("ExtraHosts")
            and not c.get("OpenStdin") and not c.get("Tty"), "Unexpected host integration")
    require(h["Memory"] == MEMORY and h["MemorySwap"] == MEMORY and h["NanoCpus"] == 2_000_000_000
            and h["PidsLimit"] == 128 and h["RestartPolicy"]["Name"] == "no", "Resource policy mismatch")
    require(h.get("Tmpfs") == {"/tmp": TMPFS} and h["ShmSize"] == 67108864, "Unexpected tmpfs")
    require(h.get("Runtime") == "runc" and not h.get("Sysctls") and h.get("CgroupParent", "") == "", "Unexpected runtime or host policy override")
    require(REQUIRED_MASKS <= set(h.get("MaskedPaths", [])) <= DEFAULT_MASKS and set(h.get("ReadonlyPaths", [])) == DEFAULT_READONLY,
            "Unexpected default kernel mounts")
    expected = {dst: (src, writable) for src, dst, writable in plan}
    require(len(h["Mounts"]) == len(expected), "Unexpected requested mounts")
    for m in h["Mounts"]:
        opts = m.get("BindOptions", {})
        require(m["Type"] == "bind" and m["Target"] in expected
            and (m["Source"], not m.get("ReadOnly", False)) == expected[m["Target"]]
            and opts.get("NonRecursive") is True and opts.get("Propagation") == "rprivate",
            "Recursive or mutable source mount request")
    require(len(data["Mounts"]) == len(expected), "Unexpected bind mounts")
    for m in data["Mounts"]:
        require(m["Type"] == "bind" and m["Destination"] in expected, "Nonallowlisted mount")
        require((m["Source"], m["RW"]) == expected[m["Destination"]] and m["Propagation"] == "rprivate", "Mount identity or access mismatch")
    # Audit Docker-managed implicit files as well, not merely caller's binds.
    cid = data["Id"]
    require(re.fullmatch(r"[0-9a-f]{64}", cid), "Invalid container ID")
    implicit = {"ResolvConfPath": "resolv.conf", "HostnamePath": "hostname", "HostsPath": "hosts"}
    for key, leaf in implicit.items():
        # Docker may initialize these during start, not create. Absence is only
        # allowed before start; inside/final verification never skips them.
        require((prestart and data[key] == "") or data[key].endswith(f"/containers/{cid}/{leaf}"),
                "Unexpected managed resolver/hostname path")
    return {"image_id": data["Image"], "user": c["User"], "security_options": h["SecurityOpt"],
        "host_configuration_verified": True, "host_execution_verified": False,
        "network_mode": h["NetworkMode"], "readonly_rootfs": h["ReadonlyRootfs"],
        "mounts": data["Mounts"], "managed_file_targets": ["/etc/resolv.conf", "/etc/hostname", "/etc/hosts"],
        "host_root_control_plane": True, "payload_userns_remap_assumed": False}


def execute(stage, work, source, python_base, node_bin):
    require_hosted()
    require(work.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()), "Work outside runner temp")
    host_evidence = checked_directory(work / "host-evidence")
    state_path = host_evidence / "execution-state.json"
    write_json(state_path, {"source_commit": source, "run_id": os.environ["GITHUB_RUN_ID"],
        "cleanup_verified": False, "status": "execution_requested"})
    check_daemon(work)
    check_image(work)
    plan = mount_plan(stage, work, python_base, node_bin)
    uid, gid = os.getuid(), os.getgid()
    namespaces = {key: os.readlink(f"/proc/self/ns/{key}") for key in ("net", "mnt", "pid", "user")}
    env = payload_environment(source, namespaces, uid, gid)
    cid = None
    try:
        created = control(work, create_arguments(plan, env, uid, gid)).stdout.strip()
        require(re.fullmatch(r"[0-9a-f]{64}", created), "Docker create returned invalid ID")
        cid = created
        write_json(work / "container-id.json", {"id": cid})
        data = json.loads(control(work, ["inspect", cid]).stdout)[0]
        evidence = validate_inspect(data, plan, uid, gid, env, prestart=True)
        evidence.update(source_commit=source, run_id=os.environ["GITHUB_RUN_ID"])
        write_json(host_evidence / "docker-host-inspect.json", evidence)
        control(work, ["start", "--attach", cid], capture=False, timeout=2700)
        final = json.loads(control(work, ["inspect", cid]).stdout)[0]
        validate_inspect(final, plan, uid, gid, env)
        require(final["State"]["ExitCode"] == 0 and not final["State"]["OOMKilled"] and not final["State"]["Running"], "Container did not complete successfully")
        # This directory is never mounted into the payload. Never write a host
        # proof through a path the payload could replace with a link.
        evidence["host_execution_verified"] = True
        evidence["source_commit"] = source
        write_json(host_evidence / "docker-host-inspect.json", evidence)
    finally:
        if cid is not None:
            # Only our freshly returned exact ID, never prune, image deletion,
            # wildcard names, another task's container or automatic rerun.
            cleanup(work, source, os.environ["GITHUB_RUN_ID"])


def cleanup(work, source, run_id):
    """Also called by an always() workflow step after cancellation or timeout."""
    require_hosted()
    require(source == os.environ["GITHUB_SHA"] and run_id == os.environ["GITHUB_RUN_ID"], "Cleanup identity mismatch")
    if not work.exists():
        return  # No preparation or container creation occurred.
    checked_directory(work)
    require(work.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()), "Cleanup outside runner temp")
    state_path = work / "host-evidence/execution-state.json"
    if not state_path.exists():
        return  # execute() records this before making any create request.
    state = json.loads(state_path.read_text())
    if state.get("cleanup_verified") is True:
        return
    cid_path = work / "container-id.json"
    require(cid_path.is_file() and not cid_path.is_symlink(), "Container identity unknown; no broad cleanup")
    cid = json.loads(cid_path.read_text())["id"]
    require(re.fullmatch(r"[0-9a-f]{64}", cid), "Invalid cleanup ID")
    found = control(work, ["ps", "--all", "--no-trunc", "--filter", "id=" + cid, "--format", "{{.ID}}"] ).stdout.splitlines()
    require(found in ([], [cid]), "Ambiguous cleanup identity")
    if found:
        data = json.loads(control(work, ["inspect", cid]).stdout)[0]
        require(data["Id"] == cid and data["Image"] == IMAGE_ID and data["Config"]["Labels"].get("c20.source") == source
            and data["Config"]["Labels"].get("c20.run_id") == run_id
            and data["Config"]["Labels"].get("c20.run_attempt") == os.environ["GITHUB_RUN_ATTEMPT"], "Cleanup labels mismatch")
        control(work, ["rm", "--force", cid])
    write_json(state_path, {"source_commit": source, "run_id": run_id,
        "cleanup_verified": True, "status": "exact_container_absent"})


def validate_limits(limits):
    require(limits.get("memory.max") == str(MEMORY) and limits.get("memory.swap.max") == "0"
        and limits.get("pids.max") == "128", "Kernel memory/swap/pids limit mismatch")
    cpu = limits.get("cpu.max", "").split()
    require(len(cpu) == 2 and all(v.isdigit() for v in cpu)
        and int(cpu[1]) > 0 and int(cpu[0]) == 2 * int(cpu[1]), "Kernel CPU limit mismatch")


def inside():
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    caps = {k: status[k].strip() for k in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")}
    require(os.getuid() == int(os.environ["C20_UID"]) > 0 and os.getgid() == int(os.environ["C20_GID"]) > 0, "Payload user mismatch")
    require(all(int(v, 16) == 0 for v in caps.values()), "Payload has capabilities")
    require(status["NoNewPrivs"].strip() == "1" and status["Seccomp"].strip() == "2", "Payload security state mismatch")
    label = Path("/proc/self/attr/current").read_text().strip()
    require(label == "docker-default (enforce)", "AppArmor not enforced")
    limits = {name: (Path("/sys/fs/cgroup") / name).read_text().strip()
              for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")}
    validate_limits(limits)
    host = json.loads(os.environ["C20_HOST_NAMESPACES"])
    namespaces = {k: os.readlink(f"/proc/self/ns/{k}") for k in host}
    require(all(namespaces[k] != host[k] for k in ("net", "mnt", "pid")), "Host namespace shared")
    interfaces = sorted(line.split(":", 1)[0].strip() for line in Path("/proc/net/dev").read_text().splitlines()[2:])
    routes = Path("/proc/net/route").read_text().splitlines()[1:]
    ipv6 = Path("/proc/net/ipv6_route").read_text().splitlines()
    require(interfaces == ["lo"] and not routes and all(line.split()[-1] == "lo" for line in ipv6), "Payload has external network")
    mounts = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        mounts[fields[4]] = fields[5].split(",")
    explicit = {"/usr", "/runtime/python", "/runtime/node", "/source", "/work", "/tmp",
        *("/source/projects/selfweb/site/" + p for p in CACHE_PATHS)}
    require(set(mounts) <= IMPLICIT_MOUNTS | DEFAULT_MASKS | DEFAULT_READONLY | explicit, "Unreviewed payload mount")
    require(explicit <= set(mounts), "Missing payload mount")
    for target in ("/", "/usr", "/runtime/python", "/runtime/node", "/source", "/sys", "/sys/fs/cgroup",
                   "/etc/hosts", "/etc/hostname", "/etc/resolv.conf"):
        require("ro" in mounts[target], "Expected readonly mount is writable")
    require(not Path("/source/.git").exists() and not Path("/var/run/docker.sock").exists(), "Credential/control mount found")
    Path("/tmp/home").mkdir()
    write_json(Path("/work/isolation.json"), {"source_commit": os.environ["C20_SOURCE"],
        "isolation_verified": True, "network_none": True, "read_only_root": True,
        "nonroot": True, "capabilities_dropped": True,
        "executor": "docker-network-none", "namespaces": namespaces, "host_namespaces": host,
        "user_namespace_separate": namespaces["user"] != host["user"], "uid": os.getuid(), "gid": os.getgid(),
        "capabilities": caps, "no_new_privileges": True, "seccomp_filter_enabled": True,
        "apparmor": label, "interfaces": interfaces, "routes": routes, "ipv6_routes": ipv6,
        "kernel_resource_limits": limits,
        "mount_options": mounts, "environment_names": sorted(os.environ), "no_deployment": True})
    subprocess.run([sys.executable, "/source/deploy/c20-artifact-only/acceptance.py", "--work", "/work/acceptance",
        "--source", os.environ["C20_SOURCE"]], stdin=subprocess.DEVNULL, check=True, timeout=2500)


if __name__ == "__main__":
    if sys.argv[1:] == ["--inside"]:
        inside()
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--cleanup", action="store_true", required=True)
        parser.add_argument("--work", type=Path, required=True)
        parser.add_argument("--source", required=True)
        parser.add_argument("--run-id", required=True)
        args = parser.parse_args()
        cleanup(args.work.absolute(), args.source, args.run_id)
