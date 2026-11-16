"""Synthetic Windows stat metadata; no reparse point or OS permission is changed."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from ahadiff.knowledge_platform.owner_identity import OwnerIdentityError, _safe


@pytest.mark.parametrize("level", ["file", "parent", "root", "ancestor"])
def test_unknown_reparse_tag_is_rejected_on_every_ancestor(tmp_path, monkeypatch, level):
    root = tmp_path / "portable"
    path = root / "data" / "identity.json"
    path.parent.mkdir(parents=True)
    path.write_text("SYNTHETIC NON-CREDENTIAL", encoding="utf-8")
    target = {"file": path, "parent": path.parent, "root": root, "ancestor": tmp_path}[level]
    real = Path.lstat

    def inspected(candidate, *args, **kwargs):
        if candidate == target:
            return SimpleNamespace(st_file_attributes=0x400, st_reparse_tag=0xDEADBEEF)
        return real(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inspected)
    with pytest.raises(OwnerIdentityError, match="OWNER_PATH_REDIRECTED"):
        _safe(root, path)


def test_cannot_inspect_parent_is_not_permission_to_create_identity(tmp_path, monkeypatch):
    real = Path.lstat

    def inspected(candidate, *args, **kwargs):
        if candidate == tmp_path:
            raise PermissionError("synthetic read denial")
        return real(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inspected)
    with pytest.raises(PermissionError):
        _safe(tmp_path, tmp_path / "future" / "identity.json")


def test_missing_descendant_still_checks_existing_ancestors(tmp_path):
    path = tmp_path / "future" / "identity.json"
    assert _safe(tmp_path, path) == path
    assert not path.parent.exists()
