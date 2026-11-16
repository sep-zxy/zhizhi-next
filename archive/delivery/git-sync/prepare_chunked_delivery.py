"""Cloud-only byte-preserving transport packaging; never contacts a remote."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import zlib


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(repo, original, output):
    if output.exists():
        raise ValueError("Use a fresh output directory")
    original_manifest = json.loads((original / "delivery.json").read_text(encoding="utf-8"))
    original_files = {name: (original / name).read_bytes() for name in original_manifest["files"]}
    if any(sha(original_files[name]) != digest for name, digest in original_manifest["files"].items()):
        raise ValueError("Original delivery integrity failed")
    original_files["delivery.json"] = (original / "delivery.json").read_bytes()
    files = {
        ".gitattributes": b"* -text\n",
        ".gitignore": b"/ready/\n/ready.tmp-*/\n",
        "restore_delivery.py": (repo / "deploy/git-sync/restore_delivery.py").read_bytes(),
        "restore.cmd": (repo / "deploy/git-sync/restore.cmd").read_bytes(),
        "README.md": (
            "# 知枝 Windows Git 成品同步\n\n"
            "此分支是云端已验证成品的分块传输表示，不是 Windows 构建项目。\n"
            "Git 检出固定提交后运行 restore.cmd，它仅在本检出目录内的 ready/ "
            "还原原37个成品文件并逐字节校验，不安装、编译、登录或修改应用。\n"
            "再运行 ready/verify.cmd；通过后按照 ready/README.md 的既有授权流程操作。\n"
            "大清单仅采用标准库 zlib 压缩和小块存储；原补丁、回滚文件、许可证、"
            "SHA-256 及完整性验证没有改动。既有205MB运行时不重复提交。\n"
            "若 ready/ 已存在，只允许完整校验成功的同版本复用，不覆盖或修复现场文件。\n"
            "恢复校验通过不是 Windows 模型 smoke 或平台 E2E 通过。\n"
        ).encode(),
    }
    records = {}
    for name, data in sorted(original_files.items()):
        parts = []
        encoding = "zlib" if len(data) > 96_000 else "raw"
        packed = zlib.compress(data, 9) if encoding == "zlib" else data
        for number, offset in enumerate(range(0, len(packed), 96_000)):
            part = packed[offset:offset + 96_000]
            path = f"objects/{sha(part)}"
            files[path] = part
            parts.append(path)
        records[name] = {"sha256": sha(data), "bytes": len(data), "encoding": encoding, "parts": parts}
    plan = {
        "schema_version": 1,
        "target_directory": "ready",
        "original_delivery_sha256": sha(original_files["delivery.json"]),
        "files": records,
        "transport_files": {name: sha(data) for name, data in sorted(files.items())},
    }
    files["transport.json"] = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode()
    tree, requests = [], []
    for name, data in sorted(files.items()):
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        target = output / "checkout" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        payload = {"content": base64.b64encode(data).decode(), "encoding": "base64"}
        if len(json.dumps(payload).encode()) > 131_072:
            raise ValueError("Git blob request exceeds 128 KiB transport budget")
        request = f"requests/{blob}.json"
        dump(output / request, payload)
        if not any(item["expected_sha"] == blob for item in requests):
            requests.append({"path": name, "file": request, "expected_sha": blob, "bytes": len(data)})
        tree.append({"path": name, "mode": "100644", "type": "blob", "sha": blob})
    dump(output / "expected-tree.json", {"tree_elements": tree})
    dump(output / "plan.json", {
        "files": len(files), "bytes": sum(map(len, files.values())),
        "requests": requests, "original_files": len(original_files),
        "original_delivery_sha256": plan["original_delivery_sha256"],
        "remote_writes": False,
    })
    return {"files": len(files), "bytes": sum(map(len, files.values())), "original_files": len(original_files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.repo, args.original, args.output)))
