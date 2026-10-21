"""Verify or restore approved public-build fonts without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path


def prepare(site: Path, manifest_path: Path, archive: Path | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported font manifest")
    fonts = manifest["fonts"]
    for font in fonts:
        if not re.fullmatch(r"[A-Za-z0-9+-]+", font["cache_name"]):
            raise ValueError("Unsafe font cache name")
        if font["archive_path"] != "fonts/" + font["cache_name"] + ".ttf":
            raise ValueError("Unsafe font archive path")
    license_path = manifest_path.parent / "NotoSansSC-OFL.txt"
    if hashlib.sha256(license_path.read_bytes()).hexdigest() != manifest["license"]["sha256"]:
        raise ValueError("Font license differs from approved source")
    cache = site / "quartz/.quartz-cache/fonts"
    if any(directory.is_symlink() for directory in [site / "quartz", site / "quartz/.quartz-cache", cache]):
        raise ValueError("Font cache directory symlinks are forbidden")
    if archive:
        expected = {font["archive_path"] for font in fonts} | {"OFL.txt", "manifest.json"}
        with zipfile.ZipFile(archive) as bundle:
            if len(bundle.namelist()) != len(expected) or set(bundle.namelist()) != expected:
                raise ValueError("Font archive contains missing, duplicate or extra paths")
            for info in bundle.infolist():
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("Font archive symlinks are forbidden")
            if bundle.getinfo("manifest.json").file_size > 65536:
                raise ValueError("Oversized font manifest")
            if json.loads(bundle.read("manifest.json")) != manifest:
                raise ValueError("Font archive manifest differs from approved source")
            if bundle.getinfo("OFL.txt").file_size != manifest["license"]["bytes"]:
                raise ValueError("Font license size differs")
            if bundle.read("OFL.txt") != license_path.read_bytes():
                raise ValueError("Font archive license differs")
            contents = []
            for font in fonts:
                if bundle.getinfo(font["archive_path"]).file_size != font["bytes"]:
                    raise ValueError("Font size differs from approved source")
                data = bundle.read(font["archive_path"])
                if hashlib.sha256(data).hexdigest() != font["sha256"]:
                    raise ValueError("Font hash differs from approved source")
                contents.append((font, data))
        cache.mkdir(parents=True, exist_ok=True)
        for font, data in contents:
            target = cache / font["cache_name"]
            if target.is_symlink():
                raise ValueError("Font cache symlinks are forbidden")
            target.write_bytes(data)
    for font in fonts:
        target = cache / font["cache_name"]
        if target.is_symlink() or not target.is_file():
            raise ValueError("Approved font cache missing; restore the licensed offline asset bundle")
        if target.stat().st_size != font["bytes"] or hashlib.sha256(target.read_bytes()).hexdigest() != font["sha256"]:
            raise ValueError("Font cache differs from approved source")
    return {"fonts_verified": len(fonts), "network_used": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path(__file__).resolve().parent.parent / "site")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parent.parent / "docs/build-assets/fonts-manifest.json")
    parser.add_argument("--archive", type=Path, help="Explicitly restore a previously saved and validated offline asset bundle")
    args = parser.parse_args()
    print(json.dumps(prepare(args.site.resolve(), args.manifest.resolve(), args.archive), sort_keys=True))
