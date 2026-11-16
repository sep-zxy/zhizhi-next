"""Copy bounded evidence to an unmounted export tree; never follow payload links.

An unsuccessful or uncertain cleanup exports host diagnostics only. This is not
an acceptance verdict. The workflow still preserves its original failed outcome.
"""
import argparse
import json
import os
from pathlib import Path
import stat

MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 10000


def require(value, message):
    if not value:
        raise ValueError(message)


def copy_tree(source, destination, budget):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(source, flags)
    try:
        copy_directory(fd, destination, budget)
    finally:
        os.close(fd)


def copy_directory(fd, destination, budget):
    destination.mkdir()
    with os.scandir(fd) as entries:
        for entry in entries:
            name = entry.name
            budget[0] += 1
            require(budget[0] <= MAX_FILES, "Evidence export entry budget exceeded")
            require(name not in (".", "..") and "/" not in name, "Unsafe entry")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    copy_directory(child, destination / name, budget)
                finally:
                    os.close(child)
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Nonregular or hardlinked evidence")
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    actual = os.fstat(child)
                    require(stat.S_ISREG(actual.st_mode) and actual.st_nlink == 1
                        and (actual.st_dev, actual.st_ino) == (info.st_dev, info.st_ino), "Evidence changed before copy")
                    budget[1] += actual.st_size
                    require(budget[1] <= MAX_BYTES, "Evidence export budget exceeded")
                    with os.fdopen(child, "rb", closefd=False) as stream, (destination / name).open("xb") as output:
                        remaining = actual.st_size
                        while remaining:
                            data = stream.read(min(remaining, 1024 * 1024))
                            require(data, "Evidence truncated")
                            output.write(data); remaining -= len(data)
                        require(not stream.read(1), "Evidence grew")
                finally:
                    os.close(child)


def export(work):
    require(work.is_dir() and work.resolve() == work and all(not p.is_symlink() for p in (work, *work.parents)), "Unsafe work root")
    destination = work / "evidence-export"
    require(not destination.exists() and not destination.is_symlink(), "Fresh export required")
    destination.mkdir(mode=0o700)
    budget = [0, 0]
    host = work / "host-evidence"
    if host.exists():
        copy_tree(host, destination / "host-evidence", budget)
    # Read only the safely copied host file, never a payload path.
    state = destination / "host-evidence/execution-state.json"
    cleanup = json.loads(state.read_text()) if state.exists() else None
    payload_exported = cleanup is None or cleanup.get("cleanup_verified") is True
    if payload_exported and (work / "result").exists():
        copy_tree(work / "result", destination / "result", budget)
    (destination / "export-status.json").write_text(json.dumps({"payload_result_exported": payload_exported,
        "entries": budget[0], "bytes": budget[1], "acceptance_verified": False}), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    export(parser.parse_args().work.absolute())
