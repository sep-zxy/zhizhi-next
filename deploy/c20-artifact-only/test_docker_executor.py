"""Configuration-only regressions; no Docker, namespace, network or build execution."""
import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("c20_docker", Path(__file__).with_name("docker_executor.py"))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def inspection():
    plan = [("/run-fixture/source", "/source", False), ("/run-fixture/result", "/work", True)]
    env = {"PATH": "/runtime/node:/usr/bin:/bin", "C20_SOURCE": "a" * 40, "C20_RUN_ID": "12", "C20_RUN_ATTEMPT": "1"}
    h = {"ReadonlyRootfs": True, "Privileged": False, "NetworkMode": "none", "PidMode": "", "UTSMode": "",
        "IpcMode": "private", "CgroupnsMode": "private", "UsernsMode": "", "CapDrop": ["ALL"],
        "SecurityOpt": d.SECURITY[:], "Memory": d.MEMORY, "MemorySwap": d.MEMORY, "NanoCpus": 2_000_000_000,
        "PidsLimit": 128, "RestartPolicy": {"Name": "no"}, "Tmpfs": {"/tmp": d.TMPFS}, "ShmSize": 67108864,
        "MaskedPaths": sorted(d.DEFAULT_MASKS), "ReadonlyPaths": sorted(d.DEFAULT_READONLY), "Runtime": "runc",
        "Mounts": [{"Type": "bind", "Source": src, "Target": dst, "ReadOnly": not rw,
                    "BindOptions": {"NonRecursive": True, "Propagation": "rprivate"}} for src, dst, rw in plan]}
    cid = "c" * 64
    data = {"Id": cid, "Image": d.IMAGE_ID, "AppArmorProfile": "docker-default", "HostConfig": h,
        "Config": {"Labels": {**d.labels(env), "org.opencontainers.image.version": "24.04", "org.opencontainers.image.ref.name": "ubuntu"}, "Image": d.IMAGE, "User": "1001:1001", "Entrypoint": ["/usr/bin/env"],
            "Cmd": ["-i", *(f"{k}={v}" for k,v in env.items()), "/source/projects/growth/.venv/bin/python",
                    "/source/deploy/c20-artifact-only/docker_executor.py", "--inside"]},
        "Mounts": [{"Type": "bind", "Source": src, "Destination": dst, "RW": rw, "Propagation": "rprivate"}
                    for src, dst, rw in plan]}
    for k, leaf in {"ResolvConfPath": "resolv.conf", "HostnamePath": "hostname", "HostsPath": "hosts"}.items():
        data[k] = f"/var/lib/docker/containers/{cid}/{leaf}"
    return data, plan, env


class DockerPolicyTests(unittest.TestCase):
    def test_kernel_resource_limits_must_match_configuration(self):
        good = {"memory.max": str(d.MEMORY), "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}
        d.validate_limits(good)
        for key, value in (("memory.max", "max"), ("memory.swap.max", "max"), ("pids.max", "max"),
                           ("cpu.max", "max 100000"), ("cpu.max", "400000 100000"), ("cpu.max", "0 0")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                d.validate_limits({**good, key: value})

    def test_deferred_runtime_paths_only_allowed_before_start(self):
        data, plan, env = inspection()
        for key in ("ResolvConfPath", "HostnamePath", "HostsPath", "AppArmorProfile"):
            data[key] = ""
        d.validate_inspect(data, plan, 1001, 1001, env, prestart=True)
        with self.assertRaises(ValueError): d.validate_inspect(data, plan, 1001, 1001, env)
        data["HostsPath"] = "/etc/hosts"
        with self.assertRaises(ValueError): d.validate_inspect(data, plan, 1001, 1001, env, prestart=True)

    def test_expected_inspect_is_only_configuration_not_workload_success(self):
        data, plan, env = inspection()
        result = d.validate_inspect(data, plan, 1001, 1001, env)
        self.assertTrue(result["host_configuration_verified"])
        self.assertFalse(result["host_execution_verified"])

    def test_privilege_network_resources_policy_mutations_fail_closed(self):
        changes = [("Privileged", True), ("ReadonlyRootfs", False), ("NetworkMode", "host"), ("PidMode", "host"),
            ("UTSMode", "host"), ("IpcMode", "host"), ("UsernsMode", "host"), ("CapAdd", ["SYS_ADMIN"]),
            ("CapDrop", []), ("GroupAdd", ["docker"]), ("Devices", [{"PathOnHost": "/dev/sda"}]),
            ("SecurityOpt", ["seccomp=unconfined"]), ("Memory", 0), ("MemorySwap", -1), ("NanoCpus", 0),
            ("PidsLimit", -1), ("PortBindings", {"80/tcp": []}), ("PublishAllPorts", True),
            ("Sysctls", {"net.ipv4.ip_forward": "1"}), ("Runtime", "unreviewed"), ("ExtraHosts", ["x:1.1.1.1"]), ("MaskedPaths", [])]
        for key, value in changes:
            with self.subTest(key=key):
                data, plan, env = inspection(); data["HostConfig"][key] = value
                with self.assertRaises(ValueError): d.validate_inspect(data, plan, 1001, 1001, env)

    def test_recursive_or_writable_source_mount_is_rejected(self):
        for kind in ("recursive", "writable", "propagation", "extra"):
            with self.subTest(kind=kind):
                data, plan, env = inspection()
                if kind == "recursive": data["HostConfig"]["Mounts"][0]["BindOptions"]["NonRecursive"] = False
                if kind == "writable": data["Mounts"][0]["RW"] = True
                if kind == "propagation": data["Mounts"][0]["Propagation"] = "rshared"
                if kind == "extra": data["Mounts"].append({"Type": "bind", "Destination": "/var/run/docker.sock"})
                with self.assertRaises(ValueError): d.validate_inspect(data, plan, 1001, 1001, env)

    def test_wrong_image_user_entrypoint_and_managed_hosts_are_rejected(self):
        for kind in ("image", "user", "command", "hosts", "extra-label", "wrong-image-label"):
            with self.subTest(kind=kind):
                data, plan, env = inspection()
                if kind == "image": data["Image"] = "sha256:" + "0" * 64
                if kind == "user": data["Config"]["User"] = "0:0"
                if kind == "command": data["Config"]["Cmd"] = ["shell"]
                if kind == "hosts": data["HostsPath"] = "/etc/hosts"
                if kind == "extra-label": data["Config"]["Labels"]["unreviewed"] = "yes"
                if kind == "wrong-image-label": data["Config"]["Labels"]["org.opencontainers.image.version"] = "latest"
                with self.assertRaises(ValueError): d.validate_inspect(data, plan, 1001, 1001, env)

    def test_create_argv_pins_network_image_and_clean_environment(self):
        args = d.create_arguments([("/fixture", "/source", False)], inspection()[2], 1001, 1001)
        self.assertIn(d.IMAGE, args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertEqual(args[args.index("--pull") + 1], "never")
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop", args)
        self.assertEqual(args[args.index(d.IMAGE) + 1], "-i")
        for forbidden in ("--privileged", "--cap-add", "--device", "--publish", "--group-add", "--userns"):
            self.assertNotIn(forbidden, args)

    def test_root_or_unsafe_mount_argument_is_rejected(self):
        for uid, src in ((0, "/fixture"), (1001, "/fixture,other")):
            with self.assertRaises(ValueError): d.create_arguments([(src, "/source", False)], inspection()[2], uid, 1001)

    def test_mount_plan_rejects_symlink_before_any_control_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "target").mkdir(); (root / "link").symlink_to(root / "target")
            with self.assertRaises(ValueError): d.checked_directory(root / "link")

    def test_local_execution_guard_without_impersonating_ci(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(d.subprocess, "run") as run:
            with self.assertRaises(ValueError): d.prepare_image(Path("/missing"))
            run.assert_not_called()

    def test_control_env_does_not_copy_caller_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp); (work / "docker-config").mkdir()
            with patch.object(d, "require_hosted"), patch.object(d.subprocess, "run") as run:
                d.control(work, ["version"])
            args, kwargs = run.call_args
            self.assertEqual(set(kwargs["env"]), {"PATH", "HOME", "DOCKER_CONFIG", "LANG"})
            self.assertEqual(args[0][3:5], ["--host", "unix:///var/run/docker.sock"])

    def test_cleanup_only_removes_saved_id_with_matching_ownership(self):
        for matches in (True, False):
            with self.subTest(matches=matches), tempfile.TemporaryDirectory() as tmp:
                import json
                from types import SimpleNamespace
                work = Path(tmp); (work / "host-evidence").mkdir()
                (work / "host-evidence/execution-state.json").write_text('{"cleanup_verified":false}')
                data, _, _ = inspection()
                (work / "container-id.json").write_text(json.dumps({"id": data["Id"]}))
                if not matches: data["Config"]["Labels"]["c20.run_id"] = "other"
                calls = []
                def control(work, args, **kwargs):
                    calls.append(args)
                    return SimpleNamespace(stdout=data["Id"] + "\n" if args[0] == "ps" else json.dumps([data]))
                # No runner impersonation: authorization check is stubbed for
                # this configuration-only fixture, all Docker I/O is mocked.
                with patch.object(d, "require_hosted"), patch.object(d, "control", control), patch.dict(os.environ,
                        {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1", "RUNNER_TEMP": str(work.parent)}):
                    if matches:
                        d.cleanup(work, "a" * 40, "12")
                        self.assertEqual(calls[-1], ["rm", "--force", data["Id"]])
                    else:
                        with self.assertRaises(ValueError): d.cleanup(work, "a" * 40, "12")
                        self.assertFalse(any(c[0] == "rm" for c in calls))

    def test_cleanup_unknown_identity_never_broadly_searches_or_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp); (work / "host-evidence").mkdir()
            (work / "host-evidence/execution-state.json").write_text('{"cleanup_verified":false}')
            with patch.object(d, "require_hosted"), patch.object(d, "control") as control, patch.dict(os.environ,
                    {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "12", "RUNNER_TEMP": str(work.parent)}):
                with self.assertRaises(ValueError): d.cleanup(work, "a" * 40, "12")
                control.assert_not_called()


if __name__ == "__main__":
    unittest.main()
