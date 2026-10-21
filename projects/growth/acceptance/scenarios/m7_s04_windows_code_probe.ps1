param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [string]$SourceRunId = 'r1-windows-s04-20260926-ff139bac-v1'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$source = Join-Path $root (Join-Path 'acceptance/runs' $SourceRunId)
$bundle = (Resolve-Path -LiteralPath (Join-Path $source 'fixture/baseline.bundle')).Path
$run = Join-Path $root (Join-Path 'acceptance/runs' $RunId)
$repo = Join-Path $run 'repo'
$artifacts = Join-Path $run 'artifacts'
$tsc = (Resolve-Path -LiteralPath (Join-Path $root 'viewer/node_modules/typescript/bin/tsc')).Path
if (Test-Path -LiteralPath $run) { throw "运行目录已存在：$run" }
New-Item -ItemType Directory -Path $artifacts -Force | Out-Null

@'
import { pathToFileURL } from 'node:url';

const { updateContent } = await import(pathToFileURL(process.argv[2]).href);
const doc = { ownerId: 'alice', content: 'old' };
function attempt(user) {
  try { return { status: 'OK', content: updateContent(user, doc, 'new').content }; }
  catch (error) { return { status: error.message }; }
}
process.stdout.write(JSON.stringify({
  alice: attempt({ id: 'alice' }),
  bob: attempt({ id: 'bob' }),
  anonymous: attempt(null),
}) + '\n');
'@ | Set-Content -LiteralPath (Join-Path $artifacts 'DocumentProbe.mjs') -Encoding UTF8

try {
    rtk git clone --quiet -- $bundle $repo
    if ($LASTEXITCODE -ne 0) { throw 'Git bundle 克隆失败' }
    $baseSha = (rtk git -C $repo rev-parse HEAD | Select-Object -Last 1).Trim()
    $manifest = Get-Content -LiteralPath (Join-Path $source 'manifest.json') -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if ($baseSha -ne $manifest.fixture.base_sha) { throw '基线提交与来源 manifest 不一致' }
    $probe = Join-Path $artifacts 'DocumentProbe.mjs'
    $module = Join-Path $repo 'dist/document.js'
    rtk proxy node $tsc -p (Join-Path $repo 'tsconfig.json')
    if ($LASTEXITCODE -ne 0) { throw '基线 TypeScript 编译失败' }
    $baselineOutput = rtk proxy node $probe $module
    if ($LASTEXITCODE -ne 0) { throw '基线行为 Probe 失败' }
    $baselineOutput | Set-Content -LiteralPath (Join-Path $artifacts 'probe-baseline.json') -Encoding UTF8
    $baseline = $baselineOutput | ConvertFrom-Json

    foreach ($path in @('src/access.ts', 'src/document.ts')) {
        $from = Join-Path $source (Join-Path 'fixture/next' $path)
        $to = Join-Path $repo $path
        Copy-Item -LiteralPath $from -Destination $to -Force
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $to).Hash.ToLowerInvariant()
        if ($actualHash -ne $manifest.fixture.next_file_sha256.$path) {
            throw "目标源码哈希不一致：$path"
        }
    }
    rtk git -C $repo add -- 'src/access.ts' 'src/document.ts'
    if ($LASTEXITCODE -ne 0) { throw '目标源码暂存失败' }
    rtk git -C $repo -c 'user.name=Growth Acceptance' `
        -c 'user.email=growth-acceptance@local.invalid' `
        commit --quiet -m '验证文档所有者授权修复'
    if ($LASTEXITCODE -ne 0) { throw '目标源码提交失败' }
    $headSha = (rtk git -C $repo rev-parse HEAD | Select-Object -Last 1).Trim()
    $diffText = @(rtk git -C $repo diff $baseSha $headSha -- 'src/access.ts' 'src/document.ts')
    if ($LASTEXITCODE -ne 0) { throw 'Git diff 读取失败' }
    $diffText | Set-Content -LiteralPath (Join-Path $artifacts 'feature.diff') -Encoding UTF8
    rtk proxy node $tsc -p (Join-Path $repo 'tsconfig.json')
    if ($LASTEXITCODE -ne 0) { throw '目标 TypeScript 编译失败' }
    $targetOutput = rtk proxy node $probe $module
    if ($LASTEXITCODE -ne 0) { throw '目标行为 Probe 失败' }
    $targetOutput | Set-Content -LiteralPath (Join-Path $artifacts 'probe-target.json') -Encoding UTF8
    $target = $targetOutput | ConvertFrom-Json

    $checks = [ordered]@{
        baseline_bob_can_edit = $baseline.bob.status -eq 'OK' -and
            $baseline.bob.content -eq 'new'
        target_bob_forbidden = $target.bob.status -eq 'FORBIDDEN'
        owner_still_can_edit = $baseline.alice.status -eq 'OK' -and
            $target.alice.status -eq 'OK'
        anonymous_still_unauthenticated = $baseline.anonymous.status -eq 'UNAUTHENTICATED' -and
            $target.anonymous.status -eq 'UNAUTHENTICATED'
        real_git_change = $baseSha -ne $headSha -and
            ($diffText -join "`n") -match 'canEdit'
    }
    $status = if ($checks.Values -contains $false) { 'FAIL' } else { 'PASS' }
    [ordered]@{
        run_id = $RunId
        status = $status
        stage_status = 'BLOCKED'
        scope = 'S04 代码案例独立前置诊断；不计入 S04 情境步骤'
        source_run_id = $SourceRunId
        candidate_sha256 = $manifest.candidate_sha256
        base_sha = $baseSha
        head_sha = $headSha
        checks = $checks
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $run 'result.json') -Encoding UTF8
    @(
        '# S04 Windows 文档权限代码 Probe',
        '',
        "当前候选 SHA-256：$($manifest.candidate_sha256)。真实 Git 基线：$baseSha；目标提交：$headSha。",
        "行为检查：**$status**。基线 Bob 可改 Alice 文档；目标 Bob 得到 FORBIDDEN，Alice 仍可修改，未登录用户仍得到 UNAUTHENTICATED。",
        '',
        '本记录只验证代码案例。S04-01 至 S04-03 的主题与对话链路、S04-04 的安装版情境操作、其余步骤均未执行；完整 S04 仍为 BLOCKED。'
    ) | Set-Content -LiteralPath (Join-Path $run 'report.md') -Encoding UTF8
    if ($status -ne 'PASS') { throw 'S04 代码案例行为断言失败' }
    Write-Output "S04_CODE_PROBE_PASS $RunId"
}
catch {
    if (-not (Test-Path -LiteralPath (Join-Path $run 'result.json'))) {
        [ordered]@{ run_id = $RunId; status = 'FAIL'; error = $_.Exception.Message } |
            ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $run 'result.json') -Encoding UTF8
    }
    throw
}
