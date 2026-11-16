param(
    [string]$InstallerPath = 'dist/desktop/工程成长伴侣 Setup 0.1.0.exe'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
$runId = 'm0-installed-nested-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runDir = Join-Path $root (Join-Path 'acceptance/runs' $runId)
$runtime = Join-Path $root (Join-Path 'dist/desktop-tests' $runId)
$parentRepo = Join-Path $runtime 'parent-repo'
$workspace = Join-Path $parentRepo 'growth-workspace'
$installed = Join-Path $runtime 'installed'
$profile = Join-Path $runtime 'electron-profile'
$stdout = Join-Path $runDir 'electron-stdout.log'
$stderr = Join-Path $runDir 'electron-stderr.log'
$checks = [ordered]@{}
$errorText = $null

New-Item -ItemType Directory -Path $runDir,$workspace,$profile -Force | Out-Null
try {
    rtk git init --quiet -- $parentRepo
    if ($LASTEXITCODE -ne 0) { throw '父 Git fixture 初始化失败' }
    $checks['parent_is_git'] = Test-Path -LiteralPath (Join-Path $parentRepo '.git')
    $checks['workspace_is_not_git'] = -not (Test-Path -LiteralPath (Join-Path $workspace '.git'))

    $installProcess = Start-Process -FilePath $installer -ArgumentList @(
        '/S', "/D=$installed"
    ) -WindowStyle Hidden -Wait -PassThru
    if ($installProcess.ExitCode -ne 0) { throw "安装器退出码：$($installProcess.ExitCode)" }
    $appExe = Join-Path $installed '工程成长伴侣.exe'
    $checks['installed_executable'] = Test-Path -LiteralPath $appExe
    if (-not $checks['installed_executable']) { throw '安装目录缺少桌面可执行文件' }

    $env:GROWTH_WORKSPACE = $workspace
    $env:GROWTH_SMOKE_EXIT = '1'
    try {
        $launch = Start-Process -FilePath $appExe -ArgumentList @(
            "--user-data-dir=$profile"
        ) -WindowStyle Hidden -Wait -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    }
    finally {
        Remove-Item Env:GROWTH_WORKSPACE -ErrorAction SilentlyContinue
        Remove-Item Env:GROWTH_SMOKE_EXIT -ErrorAction SilentlyContinue
    }
    $output = Get-Content -LiteralPath $stdout -Raw -Encoding UTF8
    $checks['installed_desktop_launched'] = $launch.ExitCode -eq 0 -and
        $output.Contains('GROWTH_DESKTOP_SMOKE_OK')
    $checks['nested_growth_db'] = Test-Path -LiteralPath (
        Join-Path $workspace '.ahadiff/growth.sqlite'
    )
    $checks['parent_repo_untouched'] = -not (Test-Path -LiteralPath (
        Join-Path $parentRepo '.ahadiff'
    ))
}
catch {
    $checks['run_error'] = $false
    $errorText = $_.Exception.Message
}
finally {
    $uninstaller = Join-Path $installed 'Uninstall 工程成长伴侣.exe'
    if (Test-Path -LiteralPath $uninstaller) {
        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList '/S' `
            -WindowStyle Hidden -Wait -PassThru
        $checks['uninstaller_exited'] = $uninstall.ExitCode -eq 0
    }
    else {
        $checks['uninstaller_exited'] = $false
    }
    $status = if ($checks.Values -contains $false) { 'FAIL' } else { 'PASS' }
    $result = [ordered]@{
        run_id = $runId
        status = $status
        mode = 'local_live_installed'
        platform = 'win32-x64'
        installer_sha256 = $hash
        checks = $checks
        error = $errorText
    }
    $result | ConvertTo-Json -Depth 6 |
        Set-Content -LiteralPath (Join-Path $runDir 'result.json') -Encoding UTF8
    $report = "# Windows 安装版嵌套工作区隔离`n`n" +
        "状态：**$status**。安装包 SHA-256：${hash}。真实 NSIS 安装后启动 Electron，" +
        "其指定工作区位于另一个 Git 仓库内。子工作区的 growth.sqlite 存在，" +
        "父仓库无 .ahadiff；随后卸载应用。[启动日志](electron-stdout.log)与" +
        "[机读结果](result.json)保留。此为 M0 Windows 子情境，不能替代 macOS 或 S01–S04 完整故事。`n"
    Set-Content -LiteralPath (Join-Path $runDir 'report.md') `
        -Value $report -Encoding UTF8 -NoNewline
    Write-Output ($result | ConvertTo-Json -Compress -Depth 6)
    if ($status -ne 'PASS') { throw "安装版嵌套工作区验证失败：$runDir" }
}
