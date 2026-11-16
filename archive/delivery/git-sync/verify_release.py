"""Read-only integrity check for an unpacked, byte-preserving Git delivery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


def verify(root: Path) -> int:
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ValueError("Links/junctions are forbidden")
    root = root.resolve()
    manifest = json.loads((root / "delivery.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported delivery schema")
    expected = manifest["files"]
    for relative, digest in expected.items():
        path = PurePosixPath(relative)
        if (
            path.is_absolute() or ".." in path.parts or "\\" in relative
            or ":" in relative or str(path) != relative
        ):
            raise ValueError("Unsafe manifest path")
        target = root.joinpath(*path.parts)
        for ancestor in (target, *target.parents):
            if ancestor == root.parent:
                break
            if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
                raise ValueError("Links/junctions are forbidden")
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("Delivery checksum mismatch: " + relative)
    observed = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Links/junctions are forbidden")
        if path.is_file():
            observed.add(relative.as_posix())
    if observed != set(expected) | {"delivery.json"}:
        raise ValueError("Unlisted or missing delivery files")
    return len(expected)


if __name__ == "__main__":
    print(json.dumps({"status": "delivery_integrity_passed", "files": verify(Path(__file__).parent)}))
