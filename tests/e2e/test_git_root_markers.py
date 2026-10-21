"""A sandbox's empty .git placeholder is not a checkout boundary."""

import pytest

from ahadiff.core.errors import InputError
from ahadiff.core.paths import _valid_git_marker, find_repo_root


def test_empty_marker_does_not_promote_non_git_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    (workspace / ".ahadiff").mkdir()
    assert not _valid_git_marker(workspace / ".git")
    with pytest.raises(InputError, match="not inside a git repository"):
        find_repo_root(workspace)


def test_directory_checkout_and_worktree_markers(tmp_path):
    marker = tmp_path / "normal" / ".git"
    marker.mkdir(parents=True)
    (marker / "HEAD").write_text("ref: refs/heads/main\n")
    (marker / "config").write_text("[core]\nrepositoryformatversion = 0\n")
    assert _valid_git_marker(marker)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: ../normal/.git\n")
    assert find_repo_root(linked) == linked
    (linked / ".git").write_text("gitdir: ../missing\n")
    assert not _valid_git_marker(linked / ".git")
