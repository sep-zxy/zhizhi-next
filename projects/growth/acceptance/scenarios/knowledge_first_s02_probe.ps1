param([Parameter(Mandatory = $true)][string]$RunDirectory)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$run = [System.IO.Path]::GetFullPath($RunDirectory)
$manifest = Get-Content -LiteralPath (Join-Path $run 'fixture-manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if ($manifest.case_id -ne 'S02') { throw '夹具不是 S02' }
$repo = [System.IO.Path]::GetFullPath([string]$manifest.repo)
if (-not $repo.StartsWith((Join-Path $run 'fixture') + [System.IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw '验收仓库不在当前 run 的 fixture 目录中'
}
$compiler = Join-Path (Split-Path $PSScriptRoot -Parent) '..\viewer\node_modules\typescript\bin\tsc'
$compiler = [System.IO.Path]::GetFullPath($compiler)
if (-not (Test-Path -LiteralPath $compiler)) { throw '锁定的 TypeScript 编译器不可用' }
$module = Join-Path $repo 'dist/search.js'
$probe = [string]$manifest.probe_path
$baseResult = $null
$headResult = $null
try {
    git -C $repo switch --detach $manifest.base_sha | Out-Null
    node $compiler -p $repo
    $baseResult = (node $probe $module | ConvertFrom-Json)
    git -C $repo switch $manifest.branch | Out-Null
    node $compiler -p $repo
    $headResult = (node $probe $module | ConvertFrom-Json)
} finally {
    git -C $repo switch $manifest.branch | Out-Null
}
if (-not $baseResult.both_completed -or -not $headResult.both_completed) {
    throw '两个受控请求没有全部完成'
}
if ((@($baseResult.rendered) -join ',') -ne (@($manifest.expected_base_render) -join ',')) {
    throw "旧实现没有复现覆盖：$(@($baseResult.rendered) -join ',')"
}
if ((@($headResult.rendered) -join ',') -ne (@($manifest.expected_head_render) -join ',')) {
    throw "新实现仍被旧结果覆盖：$(@($headResult.rendered) -join ',')"
}
if ((git -C $repo rev-parse HEAD).Trim() -ne $manifest.head_sha) {
    throw 'Probe 后 Git HEAD 未恢复到目标提交'
}
[ordered]@{
    case_id = 'S02'
    base_sha = $manifest.base_sha
    head_sha = $manifest.head_sha
    baseline = $baseResult
    target = $headResult
    compiler_version = (node $compiler --version).Trim()
    node_version = (node --version).Trim()
} | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath (Join-Path $run 'probe-result.json') -Encoding UTF8
Write-Output (Join-Path $run 'probe-result.json')
