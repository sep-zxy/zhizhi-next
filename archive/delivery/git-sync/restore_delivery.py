"""Restore exact finalized bytes from bounded Git transport chunks; no build or login."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zlib
from pathlib import Path, PurePosixPath


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe(root, relative):
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative or str(path) != relative:
        raise ValueError("Unsafe relative path")
    target = root.joinpath(*path.parts)
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ValueError("Links/junctions are forbidden")
        if ancestor == root:
            break
    return target


def verify_files(root, expected):
    observed = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if observed != set(expected):
        raise ValueError("Unexpected restored file set")
    for name, record in expected.items():
        path = safe(root, name)
        if not path.is_file() or sha(path.read_bytes()) != record["sha256"]:
            raise ValueError("Restored checksum mismatch: " + name)


def restore(root):
    root = Path(root).absolute()
    plan = json.loads(safe(root, "transport.json").read_text(encoding="utf-8"))
    if plan["schema_version"] != 1 or plan["target_directory"] != "ready":
        raise ValueError("Unsupported transport schema")
    storage = {}
    for name, digest in plan["transport_files"].items():
        data = safe(root, name).read_bytes()
        if sha(data) != digest:
            raise ValueError("Transport checksum mismatch: " + name)
        storage[name] = data
    outputs = {}
    for name, record in plan["files"].items():
        safe(root, name)
        data = b"".join(storage[part] for part in record["parts"])
        size = record["bytes"]
        if not isinstance(size, int) or size < 0 or size > 16_000_000:
            raise ValueError("Unsupported output size")
        if record["encoding"] == "zlib":
            inflater = zlib.decompressobj()
            data = inflater.decompress(data, size + 1)
            if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                raise ValueError("Invalid compressed payload")
        elif record["encoding"] != "raw":
            raise ValueError("Unknown encoding")
        if len(data) != size or sha(data) != record["sha256"]:
            raise ValueError("Final artifact checksum mismatch: " + name)
        outputs[name] = data
    original = json.loads(outputs["delivery.json"])
    if set(outputs) != set(original["files"]) | {"delivery.json"}:
        raise ValueError("Original delivery file set mismatch")
    for name, digest in original["files"].items():
        if sha(outputs[name]) != digest:
            raise ValueError("Original delivery checksum mismatch")
    destination = safe(root, "ready")
    if destination.exists():
        verify_files(destination, plan["files"])
        return {"status": "delivery_already_ready", "files": len(outputs)}
    temporary = Path(tempfile.mkdtemp(prefix="ready.tmp-", dir=root))
    try:
        for name, data in outputs.items():
            target = safe(temporary, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        verify_files(temporary, plan["files"])
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"status": "delivery_restored_and_verified", "files": len(outputs)}


if __name__ == "__main__":
    print(json.dumps(restore(Path(__file__).parent), ensure_ascii=False))
