"""Reject new strict diagnostics against the immutable pre-migration source.

The full existing debt is reported, never suppressed or rewritten into a mutable
allowlist. Both trees use the same locked pyright, interpreter and strict rules.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "62397c2ffed74d4938149f5d40e4d7a1b88c7c29"


def run_types(project: Path, configuration: dict, output: Path) -> dict:
    config = output.with_suffix(".config.json")
    settings = dict(configuration)
    settings.update({
        "include": [os.path.relpath(project / "src/ahadiff/knowledge_platform", config.parent)],
        "extraPaths": [str(project / "src")],
        "venvPath": str(ROOT / "backend"),
        "venv": ".venv",
    })
    config.write_text(json.dumps(settings), encoding="utf-8")
    result = subprocess.run(
        ["uv", "run", "--project", str(ROOT / "backend"), "--no-sync", "pyright", "--project", str(config), "--outputjson"],
        cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Pyright execution failed")
    report = json.loads(result.stdout)
    expected_files = len(list((project / "src/ahadiff/knowledge_platform").rglob("*.py")))
    if expected_files == 0 or report["summary"]["filesAnalyzed"] < expected_files:
        raise RuntimeError("Pyright scanned an empty or unexpectedly narrowed source set")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def diagnostics(report: dict, project: Path) -> Counter:
    result: Counter = Counter()
    for item in report["generalDiagnostics"]:
        if item["severity"] != "error":
            continue
        filename = Path(item["file"]).resolve().relative_to(project.resolve()).as_posix()
        # Moving source shifts line numbers; multiplicity and diagnostic meaning
        # still have to match within the same owning file.
        result[(filename, item.get("rule", ""), item["message"])] += 1
    return result


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    archive = subprocess.check_output(["git", "archive", BASELINE, "projects/growth/src", "projects/growth/pyproject.toml"], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix="zhizhi-type-baseline-") as temporary:
        baseline_root = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(baseline_root, filter="data")
        original = baseline_root / "projects/growth"
        configuration = tomllib.loads((original / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pyright"]
        if configuration["typeCheckingMode"] != "strict":
            raise RuntimeError("Immutable baseline is not strict")
        current = run_types(ROOT / "backend", configuration, artifacts / "python-types-current.json")
        baseline = run_types(original, configuration, artifacts / "python-types-baseline.json")
        introduced = diagnostics(current, ROOT / "backend") - diagnostics(baseline, original)
        summary = {"baseline_source": BASELINE, "current_errors": current["summary"]["errorCount"], "baseline_errors": baseline["summary"]["errorCount"], "introduced_errors": sum(introduced.values()), "strict_clean": current["summary"]["errorCount"] == 0}
        (artifacts / "python-types-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary))
        for (filename, rule, message), count in introduced.items():
            print(f"{filename}: {rule}: {message} ({count})", file=sys.stderr)
        return int(bool(introduced))


if __name__ == "__main__":
    raise SystemExit(main())
