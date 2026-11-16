param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$Release,
    [string]$InstallerPath = '',
    [string]$SupersedesRunId = ''
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $root 'dist/desktop/工程成长伴侣 Setup 0.1.0.exe'
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
$tsc = (Resolve-Path -LiteralPath (Join-Path $root 'viewer/node_modules/typescript/bin/tsc')).Path
$run = Join-Path $root (Join-Path 'acceptance/runs' $RunId)
$repo = Join-Path $run 'fixture/repo'
$artifacts = Join-Path $run 'artifacts'
$sliceResult = Join-Path $artifacts 'windows-probe-result.json'

function Get-ArtifactRef([string]$RelativePath) {
    return [ordered]@{
        path = $RelativePath.Replace('\', '/')
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $run $RelativePath)).Hash.ToLowerInvariant()
    }
}

Push-Location -LiteralPath $root
try {
    $prepareArgs = @(
        '-m', 'ahadiff.growth.acceptance', 'prepare',
        '--case', 'S02', '--run-id', $RunId,
        '--release', $Release, '--candidate-sha256', $hash
    )
    if ($SupersedesRunId) { $prepareArgs += @('--supersedes-run-id', $SupersedesRunId) }
    rtk proxy uv run python @prepareArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'S02 素材准备失败' }
    $manifest = Get-Content -LiteralPath (Join-Path $run 'manifest.json') -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $baseSha = [string]$manifest.fixture.base_sha
    $nodeId = [guid]::NewGuid().ToString()
    $manifest.environment | Add-Member -NotePropertyName windows_node_id -NotePropertyValue $nodeId
    $manifest | ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath (Join-Path $run 'manifest.json') -Encoding UTF8
    [ordered]@{
        node_id = $nodeId
        platform = 'win32-x64'
        identity_scope = '本机验收节点；云设备身份尚未注册'
        installer_sha256 = $hash
    } | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $artifacts 'windows-node.json') -Encoding UTF8

    @'
import { pathToFileURL } from 'node:url';

const modulePath = process.argv[2];
const { createSearch } = await import(pathToFileURL(modulePath).href);
const pending = new Map();
const completed = [];
const renders = [];
const fetchResults = (query) => new Promise((resolve) => {
  pending.set(query, (rows) => { completed.push(query); resolve(rows); });
});
const search = createSearch(fetchResults, (rows) => renders.push(rows));
const older = search('a');
const newer = search('ab');
pending.get('ab')(['ab-result']);
await newer;
pending.get('a')(['a-result']);
await older;
process.stdout.write(JSON.stringify({ completed, renders }) + '\n');
'@ | Set-Content -LiteralPath (Join-Path $artifacts 'PromiseProbe.mjs') -Encoding UTF8
    $probe = Join-Path $artifacts 'PromiseProbe.mjs'
    $compiled = Join-Path $repo 'dist/search.js'

    rtk proxy node $tsc -p (Join-Path $repo 'tsconfig.json')
    if ($LASTEXITCODE -ne 0) { throw '基线 TypeScript 编译失败' }
    $baselineOutput = rtk proxy node $probe $compiled
    if ($LASTEXITCODE -ne 0) { throw '基线 Promise Probe 失败' }
    $baselineOutput | Set-Content -LiteralPath (Join-Path $artifacts 'probe-baseline.json') -Encoding UTF8
    $baseline = $baselineOutput | ConvertFrom-Json

    $changeStartedAt = (Get-Date).ToUniversalTime().ToString('o')
    $traceId = [guid]::NewGuid().ToString()
    $target = Join-Path $repo 'src/search.ts'
    Copy-Item -LiteralPath (Join-Path $run 'fixture/next/src/search.ts') `
        -Destination $target -Force
    rtk git -C $repo add -- 'src/search.ts'
    if ($LASTEXITCODE -ne 0) { throw '目标源码暂存失败' }
    rtk git -C $repo commit -m '避免较早请求覆盖最新搜索结果' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '目标源码提交失败' }
    $headSha = (rtk git -C $repo rev-parse HEAD | Select-Object -Last 1).Trim()
    $diffText = @(rtk git -C $repo diff $baseSha $headSha -- 'src/search.ts')
    if ($LASTEXITCODE -ne 0) { throw 'Git patch 读取失败' }
    $diffText | Set-Content -LiteralPath (Join-Path $artifacts 'feature.diff') -Encoding UTF8
    rtk proxy node $tsc -p (Join-Path $repo 'tsconfig.json')
    if ($LASTEXITCODE -ne 0) { throw '改动版 TypeScript 编译失败' }
    $targetOutput = rtk proxy node $probe $compiled
    if ($LASTEXITCODE -ne 0) { throw '改动版 Promise Probe 失败' }
    $targetOutput | Set-Content -LiteralPath (Join-Path $artifacts 'probe-target.json') -Encoding UTF8
    $changed = $targetOutput | ConvertFrom-Json
    $changeFinishedAt = (Get-Date).ToUniversalTime().ToString('o')

    $checks = [ordered]@{
        baseline_overwrite = @($baseline.renders).Count -eq 2 -and
            $baseline.renders[0][0] -eq 'ab-result' -and
            $baseline.renders[1][0] -eq 'a-result'
        target_only_latest = @($changed.renders).Count -eq 1 -and
            $changed.renders[0][0] -eq 'ab-result'
        both_requests_complete = (@($baseline.completed) -join ',') -eq 'ab,a' -and
            (@($changed.completed) -join ',') -eq 'ab,a'
        real_git_diff = $baseSha -ne $headSha -and
            ($diffText -join "`n") -match 'latestRequest' -and
            ($diffText -join "`n") -match 'requestId'
    }
    $status = if ($checks.Values -contains $false) { 'FAIL' } else { 'PASS' }
    $slice = [ordered]@{
        run_id = $RunId
        status = $status
        scope = 'S02-01 受控 Promise 竞态修复与真实 Git diff'
        candidate_sha256 = $hash
        base_sha = $baseSha
        head_sha = $headSha
        checks = $checks
    }
    $slice | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $sliceResult -Encoding UTF8
    if ($status -ne 'PASS') { throw 'S02 受控 Promise 行为断言失败' }

    [ordered]@{
        schema_version = 1; run_id = $RunId; case_id = 'S02'
        step_id = 'S02-01'; status = 'PASS'
        started_at = $manifest.started_at; finished_at = $changeFinishedAt
        device_id = $nodeId; trace_id = $traceId
        trace_origin = '验收驱动 Git 与受控 Promise 操作'
        action = '创建真实基线和变更提交；分别编译并驱动旧请求晚完成的 Promise Probe'
        expected = '基线旧结果覆盖最新输入；修改后仅显示 ab，a 请求仍完成'
        actual = [ordered]@{
            base_sha = $baseSha; head_sha = $headSha
            baseline = $baseline; target = $changed
            probe_started_at = $changeStartedAt
        }
        artifacts = @(
            (Get-ArtifactRef 'fixture/baseline.bundle'),
            (Get-ArtifactRef 'fixture/next/src/search.ts'),
            (Get-ArtifactRef 'artifacts/PromiseProbe.mjs'),
            (Get-ArtifactRef 'artifacts/probe-baseline.json'),
            (Get-ArtifactRef 'artifacts/probe-target.json'),
            (Get-ArtifactRef 'artifacts/feature.diff'),
            (Get-ArtifactRef 'artifacts/windows-probe-result.json')
        )
    } | ConvertTo-Json -Compress -Depth 10 |
        Set-Content -LiteralPath (Join-Path $run 'steps.jsonl') -Encoding UTF8
    [ordered]@{ base_sha = $baseSha; head_sha = $headSha } | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $run 'chain.json') -Encoding UTF8
    rtk proxy uv run python -m ahadiff.growth.acceptance run `
        --case S02 --mode live --run-id $RunId | Out-Null
    if ($LASTEXITCODE -ne 1) { throw '完整 S02 门槛未按预期保持 BLOCKED' }
    $verified = Get-Content -LiteralPath (Join-Path $run 'result.json') -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if (-not ($verified.checks.'S02-01' -and $verified.checks.artifact_integrity)) {
        throw '验收门槛未接受真实 S02-01 步骤或证据哈希'
    }
    Add-Content -LiteralPath (Join-Path $run 'report.md') -Encoding UTF8 -Value @(
        '',
        '## Windows 受控 Promise 前段实测',
        '',
        "安装包 SHA-256：$hash。基线 Git：$baseSha；目标 Git：$headSha。",
        '基线先完成 ab 后完成 a，渲染依次为 ab-result、a-result；目标两个 Promise 仍按同样顺序完成，但仅渲染 ab-result。',
        '[Probe](artifacts/PromiseProbe.mjs)、[行为检查](artifacts/windows-probe-result.json)与[真实 diff](artifacts/feature.diff)可复核。',
        '',
        '本次只记录 S02-01。安装版 UI 学习、模型、云同步、macOS 续学与冲突均未执行；完整 S02 仍为 BLOCKED。'
    )
    $global:LASTEXITCODE = 0
    Write-Output ($slice | ConvertTo-Json -Compress -Depth 8)
}
catch {
    if (Test-Path -LiteralPath $run) {
        [ordered]@{ run_id = $RunId; status = 'FAIL'; error = $_.Exception.Message } |
            ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $sliceResult -Encoding UTF8
    }
    throw
}
finally {
    Pop-Location
}
