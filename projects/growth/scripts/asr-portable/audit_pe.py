"""Static PE import audit on the cloud host; does not execute PE files."""

import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--scratch", type=Path, required=True)
args = parser.parse_args()
root = args.root
output = []
for wheel in sorted((root / "wheels").glob("*.whl")):
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.namelist():
            if member.lower().endswith((".dll", ".pyd")):
                dest = args.scratch / wheel.stem / member
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(archive.read(member))
                inspected = subprocess.run(
                    ["objdump", "-p", str(dest)], capture_output=True, text=True, check=True
                ).stdout
                deps = re.findall(r"DLL Name: (.+)", inspected)
                output.append({"wheel": wheel.name, "binary": member, "imports": deps})
(root / "PE_IMPORT_AUDIT.json").write_text(json.dumps(output, indent=2))
print("Binaries:", len(output))
print("\n".join(sorted({dll for item in output for dll in item["imports"]})))
