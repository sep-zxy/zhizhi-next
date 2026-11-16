param(
    [Parameter(Mandatory = $true)][string]$SidecarPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$EvidenceDir,
    [ValidateSet('Windows', 'macOS')][string]$ExpectedPlatform = 'macOS',
    [string]$PackagePath = ''
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$sidecar = (Resolve-Path -LiteralPath $SidecarPath).Path
$output = [System.IO.Path]::GetFullPath($OutputPath)
$evidence = [System.IO.Path]::GetFullPath($EvidenceDir)
$runtime = Join-Path ([System.IO.Path]::GetTempPath()) (
    'growth-m0-installed-git-' + [guid]::NewGuid().ToString('n')
)
$repo = Join-Path $runtime 'repo'
$checks = [ordered]@{}
$errorText = $null
$baseSha = $null
$headSha = $null
$artifactHashes = [ordered]@{}
$packageHash = if ($PackagePath) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath (Resolve-Path -LiteralPath $PackagePath).Path).Hash.ToLowerInvariant()
} else { $null }

function Invoke-Git([string[]]$Arguments) {
    $output = & git -C $repo @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Git 操作失败：$($Arguments -join ' ')：$($output | Out-String)"
    }
    return ($output | Out-String).Trim()
}

New-Item -ItemType Directory -Path $repo, $evidence, (Split-Path -Parent $output) -Force | Out-Null
try {
    $actualPlatform = if ($IsWindows) { 'Windows' } elseif ($IsMacOS) { 'macOS' } else { 'unsupported' }
    $checks['expected_platform'] = $actualPlatform -eq $ExpectedPlatform
    if (-not $checks['expected_platform']) { throw "运行平台不符：$actualPlatform" }

    Invoke-Git @('init', '-b', 'main') | Out-Null
    Invoke-Git @('config', 'user.name', '情境验证') | Out-Null
    Invoke-Git @('config', 'user.email', 'scenario@example.invalid') | Out-Null
    Invoke-Git @('config', 'core.autocrlf', 'false') | Out-Null
    $source = Join-Path $repo 'route.py'
    Set-Content -LiteralPath $source -Encoding UTF8 -Value @'
def route_count(routes):
    return len(routes)
'@
    Invoke-Git @('add', '--', 'route.py') | Out-Null
    Invoke-Git @('commit', '-m', '建立路线查询基线') | Out-Null
    $baseSha = Invoke-Git @('rev-parse', 'HEAD')

    Set-Content -LiteralPath $source -Encoding UTF8 -Value @'
from concurrent.futures import ThreadPoolExecutor

def route_count(routes):
    with ThreadPoolExecutor(max_workers=4) as pool:
        return sum(pool.map(lambda route: len(route), routes))
'@
    Invoke-Git @('add', '--', 'route.py') | Out-Null
    Invoke-Git @('commit', '-m', '增加并发路线统计') | Out-Null
    $headSha = Invoke-Git @('rev-parse', 'HEAD')
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant()

    $sidecarOutput = & $sidecar learn --last --dry-run --repo-root $repo 2>&1 | Out-String
    $sidecarExit = $LASTEXITCODE
    $sidecarOutput = (($sidecarOutput -split "\r?\n" | ForEach-Object {
        $_.TrimEnd()
    }) -join "`n").TrimEnd()
    Set-Content -LiteralPath (Join-Path $evidence 'sidecar-output.txt') -Encoding UTF8 -Value $sidecarOutput
    $checks['installed_sidecar_dry_run'] = $sidecarExit -eq 0
    if (-not $checks['installed_sidecar_dry_run']) { throw "安装版 sidecar 退出码：$sidecarExit" }

    $runsPath = Join-Path $repo '.ahadiff/runs'
    $runs = @(Get-ChildItem -LiteralPath $runsPath -Directory)
    if ($runs.Count -ne 1) { throw "预期仅一个 AhaDiff run，实际 $($runs.Count) 个" }
    $run = $runs[0].FullName
    $metadata = Get-Content -LiteralPath (Join-Path $run 'metadata.json') -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $after = Get-Content -LiteralPath (Join-Path $run 'after_text_by_path.json') -Raw -Encoding UTF8
    $patch = Get-Content -LiteralPath (Join-Path $run 'patch.diff') -Raw -Encoding UTF8
    $checks['source_ref_is_head'] = $metadata.source_ref -eq $headSha
    $checks['changed_file_captured'] = $after.Contains('ThreadPoolExecutor') -and
        $patch.Contains('ThreadPoolExecutor')
    $checks['source_unchanged'] = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant() -eq $sourceHash
    $checks['tracked_worktree_clean'] = -not (Invoke-Git @('status', '--porcelain', '--untracked-files=no'))

    Invoke-Git @('bundle', 'create', (Join-Path $evidence 'fixture.bundle'), '--all') | Out-Null
    foreach ($name in @('metadata.json', 'artifact_set.json', 'before_text_by_path.json',
        'after_text_by_path.json', 'evidence_anchors.json', 'patch.diff')) {
        Copy-Item -LiteralPath (Join-Path $run $name) -Destination (Join-Path $evidence $name)
    }
    foreach ($item in Get-ChildItem -LiteralPath $evidence -File) {
        $artifactHashes[$item.Name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.FullName).Hash.ToLowerInvariant()
    }
}
catch {
    $checks['run_error'] = $false
    $errorText = $_.Exception.Message
}
finally {
    $status = if ($checks.Values -contains $false) { 'FAIL' } else { 'PASS' }
    [ordered]@{
        schema_version = 1
        mode = 'installed_package_git_smoke'
        status = $status
        stage_status = 'BLOCKED'
        platform = if ($IsWindows) { 'Windows' } elseif ($IsMacOS) { 'macOS' } else { 'unsupported' }
        architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
        package_sha256 = $packageHash
        sidecar_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $sidecar).Hash.ToLowerInvariant()
        base_sha = $baseSha
        head_sha = $headSha
        checks = $checks
        artifact_sha256 = $artifactHashes
        error = $errorText
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $output -Encoding UTF8
    Write-Output (Get-Content -LiteralPath $output -Raw -Encoding UTF8)
    if ($status -ne 'PASS') { throw "安装版 Git 捕获失败：$errorText" }
}
