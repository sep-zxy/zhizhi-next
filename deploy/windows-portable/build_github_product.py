"""GitHub-hosted orchestration of the existing Windows component builders.

This is a candidate build, not live-channel/model/Windows E2E acceptance. It never
publishes a Git ref, invokes a model, or consumes historical cloud-built output.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

from build_bundle import RUNTIMES, download, json_write, sha256

LEARNING_JOURNEYS = {"zhizhi-domain.spec.ts": 16, "growth-topics.spec.ts": 1}


def verify_learning_report(report_path: Path) -> None:
    """Require all selected synthetic browser journeys, never live E2E claims."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    stats = report.get("stats", {})
    if (report.get("errors") != []
            or any(stats.get(key) != 0 for key in ("skipped", "unexpected", "flaky"))):
        raise ValueError("Learning browser fixtures contain missing, skipped or failed evidence")
    counts = {name: 0 for name in LEARNING_JOURNEYS}

    def inspect(suites):
        for suite in suites:
            for spec in suite.get("specs", []):
                name = Path(spec.get("file", "")).name
                tests = spec.get("tests", [])
                if name not in counts or spec.get("ok") is not True or not tests:
                    raise ValueError("Unexpected or unsuccessful learning browser fixture")
                for test in tests:
                    results = test.get("results", [])
                    if (test.get("projectName") != "chromium-desktop"
                            or test.get("expectedStatus") != "passed"
                            or test.get("status") != "expected" or len(results) != 1
                            or results[0].get("status") != "passed"):
                        raise ValueError("Learning journey did not pass once on locked Chromium")
                    counts[name] += 1
            inspect(suite.get("suites", []))

    inspect(report.get("suites", []))
    if (any(counts[name] < minimum for name, minimum in LEARNING_JOURNEYS.items())
            or stats.get("expected") != sum(counts.values())):
        raise ValueError("Required learning browser journeys were not all collected")


def github_identity(repo: Path) -> dict:
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("GITHUB_REPOSITORY") != "sep-zxy/zhizhi-platform"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"):
        raise ValueError("A manual GitHub-hosted build in the approved repository is required")
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    if source != os.environ.get("GITHUB_SHA"):
        raise ValueError("Build source does not match this workflow's immutable commit")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=repo):
        raise ValueError("Build must start from an unmodified source checkout")
    return {
        "repository": os.environ["GITHUB_REPOSITORY"], "source_commit": source,
        "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "workflow": os.environ["GITHUB_WORKFLOW"],
        "run_url": f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}',
        "runner": "github-hosted", "event": "workflow_dispatch",
    }


def read_official(url: str, maximum: int) -> bytes:
    allowed = ("https://nodejs.org/dist/", "https://fonts.gstatic.com/")
    if not url.startswith(allowed):
        raise ValueError("Unapproved build asset origin")
    with urllib.request.urlopen(url, timeout=90) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Build asset exceeds expected size")
    return data


def fonts(repo: Path, work: Path) -> None:
    """Fetch only the already-reviewed font URLs, hashes and bundled license."""
    docs = repo / "projects/selfweb/docs/build-assets"
    manifest = json.loads((docs / "fonts-manifest.json").read_text())
    license_data = (docs / "NotoSansSC-OFL.txt").read_bytes()
    if hashlib.sha256(license_data).hexdigest() != manifest["license"]["sha256"]:
        raise ValueError("Font license drift")
    archive = work / "reviewed-fonts.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("OFL.txt", license_data)
        for font in manifest["fonts"]:
            data = read_official(font["font_url"], font["bytes"])
            if len(data) != font["bytes"] or hashlib.sha256(data).hexdigest() != font["sha256"]:
                raise ValueError("Font source changed; no fallback or substitution")
            bundle.writestr(font["archive_path"], data)
    script = repo / "projects/selfweb/scripts/offline-fonts.py"
    spec = importlib.util.spec_from_file_location("offline_fonts", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare(repo / "projects/selfweb/site", docs / "fonts-manifest.json", archive)


def template_fixtures(repo: Path, work: Path, source_commit: str) -> tuple[Path, Path]:
    """Independent synthetic inputs; never user knowledge or publication approval."""
    sys.path.insert(0, str(repo / "projects/growth/src"))
    from ahadiff.knowledge_platform.publish import build_projection
    outputs = []
    for name, body in (("template-a", "Synthetic template input A."),
                       ("template-b", "Different synthetic input B, not user knowledge.")):
        output = work / name
        page = (
            "---\ntitle: Template Verification\npublish: true\n"
            "created: '2026-01-01'\nupdated: '2026-02-01'\n"
            "tags: ['Architecture/Tradeoffs']\n---\n\n" + body + "\n"
        ).encode()
        build_projection({"architecture/template.md": page}, output,
                         revision=source_commit, policy_version="ci-template-1",
                         template_version="ci-template-1", policy={})
        outputs.append(output)
    return tuple(outputs)


def build(repo: Path, work: Path) -> dict:
    repo, work = repo.resolve(), work.resolve()
    identity = github_identity(repo)
    if work.exists():
        raise ValueError("Use a fresh build directory")
    work.mkdir(parents=True)
    evidence = work / "evidence"
    evidence.mkdir()
    phases = []
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "npm_config_cache": str(work / "npm-cache"),
           "TZ": "UTC"}
    # Do not propagate the old task-scoped cloud exception into a real CI build.
    env.pop("ZHIZHI_ALLOW_ISOLATED_CLOUD_BUILD", None)
    env.pop("ZHIZHI_BUILD_AUTHORIZATION", None)

    def run(name, command, cwd=repo, extra=None, check=None):
        try:
            subprocess.run(command, cwd=cwd, env={**env, **(extra or {})}, check=True)
            if check is not None:
                check()
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            phases.append({"phase": name, "status": "failed",
                           "error_type": type(error).__name__,
                           "exit_code": getattr(error, "returncode", None)})
            raise
        else:
            phases.append({"phase": name, "status": "passed"})
        finally:
            json_write(evidence / "build-phases.json", phases)

    def python(script, *args):
        return [sys.executable, str(script), *map(str, args)]

    growth = repo / "projects/growth"
    env["PYTHONPATH"] = str(growth / "src") + os.pathsep + str(growth)
    # Locked dependencies are installed by the workflow before this driver.
    # Vitest also contains real Chromium DOM fixtures. Resolve the browser from
    # this app's frozen lock before any long gates, not from another app or PATH.
    run("viewer-browser-install",
        ["pnpm", "--dir", str(growth / "viewer"), "exec", "playwright",
         "install", "--with-deps", "chromium"])
    run("backend-isolated-fixtures", python("-m", "pytest", "tests/unit",
        "tests/knowledge_platform", "../../tests/e2e", "../../tests/repository",
        "--junitxml=" + str(evidence / "backend-isolated.xml")), growth,
        {"PYTHONPATH": str(growth / "src") + os.pathsep + str(growth)})
    run("graphify-performance-gate", python("benchmarks/graphify/bench_graphify.py"), growth)
    # pytest collects both TestCase and free-function regressions. unittest alone
    # would silently omit the existing Codex patch/smoke function tests.
    run("delivery-unit-gates", python("-m", "pytest", "deploy/git-sync"))
    run("portable-assembly-unit-gates", python("-m", "pytest", "deploy/windows-portable"))
    run("viewer-typecheck", ["pnpm", "--dir", str(growth / "viewer"), "typecheck"])
    run("viewer-tests", ["pnpm", "--dir", str(growth / "viewer"), "test"])
    # pnpm test is Vitest, not these full-page learning/domain journeys. Keep
    # this bounded to the approved P0 files, not the historical 15-project matrix.
    learning_report = evidence / "viewer-learning-browser.json"
    run("viewer-learning-browser-fixtures", [
        "pnpm", "--dir", str(growth / "viewer"), "exec", "playwright", "test",
        *("tests/e2e/" + name for name in LEARNING_JOURNEYS),
        "--project=chromium-desktop", "--forbid-only", "--retries=0",
        "--reporter=list,json", "--output", str(evidence / "viewer-learning-browser"),
    ], extra={"PLAYWRIGHT_JSON_OUTPUT_FILE": str(learning_report)},
        check=lambda: verify_learning_report(learning_report))
    run("viewer-production-build", ["pnpm", "--dir", str(growth / "viewer"), "build"])

    # This is a separate private workspace, not the growth learning viewer or
    # the public Wiki renderer. Missing source/lock/build is a hard failure.
    private_wiki = repo / "projects/wiki-private"
    run("private-wiki-locked-install",
        ["npm", "ci", "--ignore-scripts", "--no-audit", "--fund=false"], private_wiki)
    run("private-wiki-types", ["npm", "run", "typecheck"], private_wiki)
    run("private-wiki-tests", ["npm", "test"], private_wiki)
    run("private-wiki-domain-capture",
        python("tests/integration/capture_domain_contract.py"), private_wiki)
    run("private-wiki-domain-contract", ["npm", "run", "test:domain-contract"], private_wiki)
    shutil.copyfile(private_wiki / "test-results/domain-contract.json",
                    evidence / "private-wiki-synthetic-domain-contract.json")
    run("private-wiki-production-build", ["npm", "run", "build"], private_wiki)
    run("private-wiki-browser-install",
        ["npx", "--no-install", "playwright", "install", "--with-deps", "chromium"], private_wiki)
    run("private-wiki-browser-fixtures",
        ["npm", "run", "test:e2e", "--", "--output", str(evidence / "private-wiki-browser")],
        private_wiki)

    pi = repo / "integrations/pi-zhizhi"
    run("pi-locked-install", ["npm", "ci", "--ignore-scripts", "--no-audit", "--fund=false"], pi)
    run("pi-types", ["npm", "run", "typecheck"], pi)
    run("pi-tests", ["npm", "test"], pi)
    run("pi-runtime-build", ["node", "scripts/build-portable.mjs", str(work / "pi")], pi)
    run("pi-portable-tests", ["npm", "run", "test:portable"], pi,
        {"ZHIZHI_TEST_PI_COMPONENT": str(work / "pi")})

    site = repo / "projects/selfweb/site"
    fonts(repo, work)
    phases.append({"phase": "licensed-fonts-exact-hash", "status": "passed"})
    run("wiki-locked-install", ["npm", "ci", "--ignore-scripts", "--no-audit", "--fund=false"], site)
    vendored = json.loads((site / ".quartz/vendored-source-manifest.json").read_text())
    for name in sorted(vendored["plugins"]):
        plugin = site / ".quartz/plugins" / name
        run("wiki-plugin-install:" + name,
            ["npm", "ci", "--ignore-scripts", "--no-audit", "--fund=false"], plugin)
        run("wiki-plugin-build:" + name, ["npm", "run", "build"], plugin)
    run("wiki-types", ["npm", "run", "typecheck"], site)
    run("wiki-core-tests", ["npm", "run", "test:core"], site)
    first, second = template_fixtures(repo, work, identity["source_commit"])
    run("wiki-capture-compile", ["node", "portable/build.mjs", "capture", "portable/capture.generated.mjs"], site)
    capture_output = work / "template-capture-output"
    capture_script = work / "capture-template.mjs"
    capture_script.write_text(
        'globalThis.fetch = async () => { throw new Error("Template capture forbids network"); };\n'
        f'const {{render}} = await import({json.dumps((site / "portable/capture.generated.mjs").as_uri())});\n'
        f'await render({json.dumps(str(first))}, {json.dumps(str(capture_output))});\n',
        encoding="utf-8")
    run("wiki-independent-template-capture", ["node", str(capture_script)], site, {
        "QUARTZ_OFFLINE_PLUGINS": "1", "QUARTZ_PUBLIC_BASE_URL": "https://example.org",
        "QUARTZ_RESOURCE_STORE": str(work / "resources.json"), "QUARTZ_CAPTURE_RESOURCES": "1"})
    run("wiki-runtime-compile", ["node", "portable/build.mjs", "runtime", "portable/runtime.generated.mjs"], site)
    run("wiki-runtime-package", python(site / "portable/package.py", work / "wiki-linux", work / "resources.json"), site)
    run("wiki-dynamic-fixture-gates", python(site / "portable/test-runtime.py",
        work / "wiki-linux", first, second, work / "wiki-fixture-results"), site)
    assets = {
        path.relative_to(capture_output).as_posix(): sha256(path)
        for path in sorted(capture_output.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".css", ".js", ".woff", ".woff2", ".ttf", ".svg", ".png", ".ico"}
    }
    if not assets:
        raise ValueError("Independent template baseline is empty")
    json_write(work / "template-assets.json", {
        "schema_version": 1, "source_commit": identity["source_commit"],
        "purpose": "independent synthetic template baseline; not user publication approval",
        "assets": assets,
    })
    native = work / "wiki-native"
    native.mkdir()
    packed = subprocess.check_output(
        ["npm", "pack", "@img/sharp-win32-x64@0.34.5",
         "@napi-rs/simple-git-win32-x64-msvc@0.1.22", "--json"],
        cwd=native, env=env)
    json.loads(packed)
    (native / "pack.json").write_bytes(packed)
    run("wiki-windows-native-exact-lock", python(site / "portable/windows.py",
        work / "wiki-linux", work / "wiki", native), site)

    # Fresh official downloads and locked Windows wheels, never historical output.
    checks = read_official(RUNTIMES["node"]["source"], 100_000)
    (evidence / "node-SHASUMS256.txt").write_bytes(checks)
    node_hashes = [line.split()[0] for line in checks.decode().splitlines()
                   if line.split()[-1] == RUNTIMES["node"]["filename"]]
    if len(node_hashes) != 1:
        raise ValueError("Official Node checksum entry is not unique")
    for name, record in RUNTIMES.items():
        expected = node_hashes[0] if name == "node" else record["sha256"]
        download(record["url"], work / "downloads" / record["filename"], expected)
    run("windows-locked-wheels", python(repo / "deploy/windows-portable/build_bundle.py",
        "fetch-wheels", "--repo", repo, "--output", work))
    run("windows-official-runtimes", python(repo / "deploy/windows-portable/build_bundle.py",
        "prepare-runtimes", "--repo", repo, "--output", work))
    run("assemble-windows-product", python(repo / "deploy/windows-portable/assemble_product.py",
        "--repo", repo, "--output", work, "--pi", work / "pi", "--wiki", work / "wiki",
        "--private-wiki", private_wiki / "dist",
        "--template-assets", work / "template-assets.json"))
    # Re-seal after adding build provenance so it is part of the immutable payload.
    result = {
        "schema_version": 1, **identity, "status": "ci_candidate_built",
        "phases": phases, "live_model": "not_run", "live_channels": "not_run",
        "windows_e2e": "not_run", "asr": "separate_component_pending_validation",
        "final_delivery": False,
    }
    json_write(work / "bundle/provenance/github-build.json", result)
    from assemble_product import seal
    seal(work / "bundle", work)
    result["archive_sha256"] = sha256(work / "zhizhi-windows-x64-portable.zip")
    result["distribution_sha256"] = sha256(work / "bundle/provenance/distribution.json")
    product_files = [path for path in (work / "bundle").rglob("*") if path.is_file()]
    result["product_files"] = len(product_files)
    result["product_bytes"] = sum(path.stat().st_size for path in product_files)
    json_write(evidence / "github-build-receipt.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.repo, arguments.output), ensure_ascii=False))
