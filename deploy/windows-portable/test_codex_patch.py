"""Cloud-only patch safety tests; no Windows execution or real account calls."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "portable_codex_patch", Path(__file__).parent / "patch/apply_patch.py"
)
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def make_patch(tmp_path):
    root, patch = tmp_path / "product", tmp_path / "patch"
    before = {
        "app/backend/program.py": "old code",
        "runtime/unchanged.exe": "original runtime",
    }
    after = {
        **before,
        "app/backend/program.py": "new code",
        "app/backend/new.py": "added code",
    }
    for name, value in before.items():
        write(root / name, value)
    write(root / "config/launch.json", '{"unchanged":"user config"}')
    write(root / "data/private.txt", "user state")
    before_dist = json.dumps(
        {
            "files": {
                k: hashlib.sha256(v.encode()).hexdigest() for k, v in before.items()
            }
        }
    )
    after_dist = json.dumps(
        {"files": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in after.items()}}
    )
    write(root / PATCH.MANIFEST, before_dist)
    before[PATCH.MANIFEST], after[PATCH.MANIFEST] = before_dist, after_dist
    changes = ["app/backend/program.py", "app/backend/new.py", PATCH.MANIFEST]
    plan = {
        "schema_version": 1,
        "target": "windows-x64",
        "source_commit": "fixture-source",
        "base_zip_sha256": "fixture-archive",
        "changes": changes,
        "before": {},
        "after": {},
    }
    for direction, files in (("before", before), ("after", after)):
        for name in changes:
            if name in files:
                write(patch / direction / name, files[name])
                plan[direction][name] = PATCH.sha(patch / direction / name)
    write(patch / "patch.json", json.dumps(plan))
    return root, patch


def test_apply_verify_rollback_preserves_config_state(tmp_path):
    root, patch = make_patch(tmp_path)
    assert PATCH.apply(root, patch, "check")["status"] == "base_verified"
    assert PATCH.apply(root, patch, "apply")["status"] == "applied"
    assert (root / "app/backend/program.py").read_text() == "new code"
    assert PATCH.apply(root, patch, "rollback")["status"] == "rolled_back"
    assert (root / "app/backend/program.py").read_text() == "old code"
    assert not (root / "app/backend/new.py").exists()
    assert (root / "config/launch.json").read_text() == '{"unchanged":"user config"}'
    assert (root / "data/private.txt").read_text() == "user state"


@pytest.mark.parametrize("target", ["app/backend/program.py", "runtime/unchanged.exe"])
def test_preflight_detects_original_immutable_tampering(tmp_path, target):
    root, patch = make_patch(tmp_path)
    write(root / target, "tampered")
    with pytest.raises(ValueError):
        PATCH.apply(root, patch, "apply")
    assert not (root / "app/backend/new.py").exists()


def test_payload_tampering_stops_before_update(tmp_path):
    root, patch = make_patch(tmp_path)
    write(patch / "after/app/backend/program.py", "tampered")
    with pytest.raises(ValueError):
        PATCH.apply(root, patch, "apply")
    assert (root / "app/backend/program.py").read_text() == "old code"


def test_new_file_conflict_stops_before_update(tmp_path):
    root, patch = make_patch(tmp_path)
    write(root / "app/backend/new.py", "user's other code")
    with pytest.raises(ValueError):
        PATCH.apply(root, patch, "apply")
    assert (root / "app/backend/program.py").read_text() == "old code"


def test_partial_application_can_roll_back(tmp_path):
    root, patch = make_patch(tmp_path)
    write(root / "app/backend/program.py", "new code")
    assert PATCH.apply(root, patch, "rollback")["status"] == "rolled_back"
    assert (root / "app/backend/program.py").read_text() == "old code"


def test_rollback_refuses_unexpected_edits(tmp_path):
    root, patch = make_patch(tmp_path)
    PATCH.apply(root, patch, "apply")
    write(root / "app/backend/program.py", "later edits")
    with pytest.raises(ValueError):
        PATCH.apply(root, patch, "rollback")
    assert (root / "app/backend/program.py").read_text() == "later edits"


def test_patch_cannot_target_configuration(tmp_path):
    root, patch = make_patch(tmp_path)
    plan = json.loads((patch / "patch.json").read_text())
    plan["changes"].append("config/launch.json")
    plan["after"]["config/launch.json"] = "invalid"
    write(patch / "patch.json", json.dumps(plan))
    with pytest.raises(ValueError, match="state/config/runtime"):
        PATCH.apply(root, patch, "apply")


def test_missing_launcher_lock_fails_closed_without_creating_state(tmp_path):
    root, _ = make_patch(tmp_path)
    with pytest.raises(ValueError, match="Launcher lock missing"):
        PATCH.acquire_launcher_lock(root)
    assert not (root / "data/launcher.lock").exists()


def test_launcher_lock_is_at_byte_zero_without_changing_content(tmp_path, monkeypatch):
    root, _ = make_patch(tmp_path)
    write(root / "data/launcher.lock", "unchanged-lock")
    calls = []
    monkeypatch.setitem(
        sys.modules, "msvcrt",
        SimpleNamespace(LK_NBLCK=1, locking=lambda fd, mode, size: calls.append((mode, size))),
    )
    lock = PATCH.acquire_launcher_lock(root)
    assert lock.tell() == 0 and calls == [(1, 1)]
    lock.close()
    assert (root / "data/launcher.lock").read_text() == "unchanged-lock"
