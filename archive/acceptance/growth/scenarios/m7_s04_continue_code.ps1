param(
    [Parameter(Mandatory = $true)][string]$ChatRunId,
    [Parameter(Mandatory = $true)][string]$SourceRunId,
    [switch]$Formal
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$run = Join-Path $root (Join-Path 'acceptance/runs' $ChatRunId)
$source = Join-Path $root (Join-Path 'acceptance/runs' $SourceRunId)
$repo = (Resolve-Path -LiteralPath (Join-Path $run 'fixture/repo')).Path
$artifacts = Join-Path $run 'artifacts'
$tsc = (Resolve-Path -LiteralPath (Join-Path $root 'viewer/node_modules/typescript/bin/tsc')).Path
$manifest = Get-Content -LiteralPath (Join-Path $source 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Formal) {
    if ($ChatRunId -ne $SourceRunId) { throw '正式代码步骤必须沿用同一 S04 运行' }
    $steps = @(Get-Content -LiteralPath (Join-Path $run 'steps.jsonl') -Encoding UTF8 |
        Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    if (($steps.step_id -join ',') -ne 'S04-01,S04-02,S04-03' -or
        @($steps | Where-Object { $_.status -ne 'PASS' }).Count -ne 0) {
        throw '正式 S04-01/02/03 尚未连续通过'
    }
    $confirmed = Get-Content -LiteralPath (Join-Path $run 'artifacts/s04-03-cloud-audit.json') `
        -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($confirmed.status -ne 'PASS') { throw '正式主题确认数据库审计未通过' }
    $chatAudit = [pscustomobject]@{ status = 'PASS'; candidate_sha256 = $manifest.candidate_sha256 }
    $confirmAudit = $confirmed
} else {
    $chatAudit = Get-Content -LiteralPath (Join-Path $run 'audit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $confirmAudit = Get-Content -LiteralPath (Join-Path $run 'artifacts/confirm-audit.json') `
        -Raw -Encoding UTF8 | ConvertFrom-Json
}
if ($chatAudit.status -ne 'PASS' -or $confirmAudit.status -ne 'PASS') {
    throw 'S04 同候选对话与主题确认前置未通过'
}
if ($chatAudit.candidate_sha256 -ne $manifest.candidate_sha256) {
    throw 'S04 代码来源与对话候选不同'
}
$gitRoot = (rtk git -C $repo rev-parse --show-toplevel | Select-Object -Last 1).Trim()
if ([IO.Path]::GetFullPath($gitRoot).ToLowerInvariant() -ne
    [IO.Path]::GetFullPath($repo).ToLowerInvariant()) {
    throw "Git 根目录不符：$gitRoot"
}
$baseSha = (rtk git -C $repo rev-parse HEAD | Select-Object -Last 1).Trim()
if ($baseSha -ne $manifest.fixture.base_sha) { throw '仓库不在指定基线提交' }
$initialStatus = @(rtk git -C $repo status --porcelain)
if ($initialStatus.Count -gt 0) { throw '开始时仓库存在未提交变更' }
if (Test-Path -LiteralPath (Join-Path $artifacts 'code-result.json')) {
    throw '代码阶段记录已存在，不得就地重写'
}

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

$probe = Join-Path $artifacts 'DocumentProbe.mjs'
$module = Join-Path $repo 'dist/document.js'
rtk proxy node $tsc -p (Join-Path $repo 'tsconfig.json')
if ($LASTEXITCODE -ne 0) { throw '基线 TypeScript 编译失败' }
$baselineOutput = rtk proxy node $probe $module
if ($LASTEXITCODE -ne 0) { throw '基线行为 Probe 失败' }
$baselineOutput | Set-Content -LiteralPath (Join-Path $artifacts 'code-probe-baseline.json') -Encoding UTF8
$baseline = $baselineOutput | ConvertFrom-Json

foreach ($relative in @('src/access.ts', 'src/document.ts')) {
    $from = Join-Path $source (Join-Path 'fixture/next' $relative)
    $to = Join-Path $repo $relative
    Copy-Item -LiteralPath $from -Destination $to -Force
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $to).Hash.ToLowerInvariant()
    if ($actualHash -ne $manifest.fixture.next_file_sha256.$relative) {
        throw "目标源码哈希不一致：$relative"
    }
}
rtk git -C $repo add -- 'src/access.ts' 'src/document.ts'
if ($LASTEXITCODE -ne 0) { throw '目标源码暂存失败' }
rtk git -C $repo -c 'user.name=Growth Acceptance' `
    -c 'user.email=growth-acceptance@local.invalid' `
    commit --quiet -m '增加文档所有者编辑权限检查'
if ($LASTEXITCODE -ne 0) { throw '目标源码提交失败' }
$headSha = (rtk git -C $repo rev-parse HEAD | Select-Object -Last 1).Trim()
$diffText = @(rtk git -C $repo diff $baseSha $headSha -- 'src/access.ts' 'src/document.ts')
if ($LASTEXITCODE -ne 0) { throw 'Git diff 读取失败' }
$diffText | Set-Content -LiteralPath (Join-Path $artifacts 'code-feature.diff') -Encoding UTF8
rtk proxy node $tsc -p (Join-Path $repo 'tsconfig.json')
if ($LASTEXITCODE -ne 0) { throw '目标 TypeScript 编译失败' }
$targetOutput = rtk proxy node $probe $module
if ($LASTEXITCODE -ne 0) { throw '目标行为 Probe 失败' }
$targetOutput | Set-Content -LiteralPath (Join-Path $artifacts 'code-probe-target.json') -Encoding UTF8
$target = $targetOutput | ConvertFrom-Json
$finalStatus = @(rtk git -C $repo status --porcelain)
$checks = [ordered]@{
    same_candidate = $chatAudit.candidate_sha256 -eq $manifest.candidate_sha256
    real_git_commit = $baseSha -ne $headSha -and $finalStatus.Count -eq 0
    baseline_bob_can_edit = $baseline.bob.status -eq 'OK' -and $baseline.bob.content -eq 'new'
    target_bob_forbidden = $target.bob.status -eq 'FORBIDDEN'
    alice_still_can_edit = $baseline.alice.status -eq 'OK' -and $target.alice.status -eq 'OK'
    anonymous_unchanged = $baseline.anonymous.status -eq 'UNAUTHENTICATED' -and
        $target.anonymous.status -eq 'UNAUTHENTICATED'
    patch_has_owner_check = ($diffText -join "`n") -match 'canEdit'
}
$status = if ($checks.Values -contains $false) { 'FAIL' } else { 'PASS' }
[ordered]@{
    status = $status
    mode = if ($Formal) { 'live_formal_windows' } else { 'installed_windows_local_cloud_rehearsal' }
    candidate_sha256 = $manifest.candidate_sha256
    source_run_id = $SourceRunId
    chat_run_id = $ChatRunId
    workspace = $repo
    base_sha = $baseSha
    head_sha = $headSha
    checks = $checks
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $artifacts 'code-result.json') -Encoding UTF8
if ($status -ne 'PASS') { throw 'S04 代码阶段检查未通过' }
Write-Output "S04_SAME_CHAIN_CODE_PASS $headSha"
