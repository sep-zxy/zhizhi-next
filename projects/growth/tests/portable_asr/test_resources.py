import hashlib
import importlib.util
import sys
import tomllib
from pathlib import Path

SCRIPTS = Path(__file__).parents[2] / "scripts/asr-portable"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lock_has_complete_windows_asr_wheel_closure():
    resources = load("prepare_resources")
    packages = resources.selected_packages(
        tomllib.loads((SCRIPTS.parents[1] / "uv.lock").read_text())
    )
    assert packages["av"]["version"] == "19.0.1"
    assert packages["ctranslate2"]["version"] == "4.8.3"
    for package in packages.values():
        wheel = resources.choose_wheel(package)
        assert wheel["hash"].startswith("sha256:")
        assert "win_amd64" in wheel["url"] or "-any.whl" in wheel["url"]


def test_healthcheck_refuses_linux(tmp_path, monkeypatch):
    health = load("healthcheck")
    monkeypatch.setattr(sys, "platform", "linux")
    result = health.probe(tmp_path, tmp_path / "absent.json")
    assert result["status"] == "BLOCKED"
    assert result["code"] == "WINDOWS_X64_REQUIRED"


def test_model_integrity(tmp_path):
    health = load("healthcheck")
    path = tmp_path / "data/models/test.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"valid")
    records = [{"path": "data/models/test.bin", "sha256": hashlib.sha256(b"valid").hexdigest()}]
    assert health.verify_files(tmp_path, records) == []
    path.write_bytes(b"tampered")
    assert health.verify_files(tmp_path, records) == ["data/models/test.bin"]


def test_missing_vc_runtime_is_blocked_without_install(tmp_path, monkeypatch):
    import json

    health = load("healthcheck")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(health.platform, "machine", lambda: "AMD64")

    def missing(name):
        raise OSError("missing")

    monkeypatch.setattr(health.ctypes, "WinDLL", missing, raising=False)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": []}))
    result = health.probe(tmp_path, manifest)
    assert result["code"] == "VC_RUNTIME_MISSING"
    assert len(result["missing_dlls"]) == 4
    assert result["status"] == "BLOCKED"
