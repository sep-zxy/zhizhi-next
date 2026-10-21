param(
    [string]$InstallerPath = ''
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $root 'dist\desktop\工程成长伴侣 Setup 0.1.0.exe'
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$runId = 'm0-install-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runDir = Join-Path $root (Join-Path 'acceptance\runs' $runId)
$installDir = Join-Path $runDir 'installed'
$workspace = Join-Path $runDir 'workspace'
New-Item -ItemType Directory -Path $workspace -Force | Out-Null
rtk git init --quiet -- $workspace
if ($LASTEXITCODE -ne 0) { throw '隔离工作区初始化失败' }
$installerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash
$checks = [ordered]@{}
$activeSidecar = $null
$uninstaller = Join-Path $installDir 'Uninstall 工程成长伴侣.exe'

function Invoke-SilentInstall {
    $process = Start-Process -FilePath $installer -ArgumentList @('/S', "/D=$installDir") `
        -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "安装器退出码：$($process.ExitCode)" }
}

function Invoke-DesktopSmoke([string]$Label) {
    $appExe = Join-Path $installDir '工程成长伴侣.exe'
    $stdout = Join-Path $runDir ($Label + '-stdout.log')
    $stderr = Join-Path $runDir ($Label + '-stderr.log')
    $env:GROWTH_WORKSPACE = $workspace
    $env:GROWTH_SMOKE_EXIT = '1'
    try {
        $process = Start-Process -FilePath $appExe -WindowStyle Hidden -Wait -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        return $process.ExitCode -eq 0 -and (
            (Get-Content -LiteralPath $stdout -Raw -Encoding UTF8) -match 'GROWTH_DESKTOP_SMOKE_OK'
        )
    }
    finally {
        Remove-Item Env:GROWTH_WORKSPACE -ErrorAction SilentlyContinue
        Remove-Item Env:GROWTH_SMOKE_EXIT -ErrorAction SilentlyContinue
    }
}

function Start-IsolatedSidecar {
    $exe = Join-Path $installDir 'resources\sidecar\growth-sidecar.exe'
    $viewer = Join-Path $installDir 'resources\viewer'
    if (-not (Test-Path -LiteralPath $exe) -or
        -not (Test-Path -LiteralPath (Join-Path $viewer 'index.html'))) {
        throw '安装目录缺少 sidecar 或 Viewer'
    }
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    $origin = 'http://127.0.0.1:' + $port
    $env:AHADIFF_VIEWER_DIST = $viewer
    $script:activeSidecar = Start-Process -FilePath $exe -ArgumentList @(
        'serve', '--repo-root', $workspace, '--port', [string]$port, '--no-browser'
    ) -WorkingDirectory $workspace -WindowStyle Hidden -PassThru
    $ready = $false
    for ($index = 0; $index -lt 100; $index++) {
        try {
            $healthUri = $origin + '/healthz'
            if ((Invoke-RestMethod -Uri $healthUri -TimeoutSec 2).ok) {
                $ready = $true
                break
            }
        }
        catch { Start-Sleep -Milliseconds 300 }
    }
    if (-not $ready) { throw '安装后的 sidecar 未启动' }
    return $origin
}

function Stop-IsolatedSidecar {
    if ($script:activeSidecar) {
        $script:activeSidecar.Refresh()
        if (-not $script:activeSidecar.HasExited) {
            Stop-Process -Id $script:activeSidecar.Id -Force
        }
        $script:activeSidecar = $null
    }
    Remove-Item Env:AHADIFF_VIEWER_DIST -ErrorAction SilentlyContinue
}

try {
    Invoke-SilentInstall
    $checks['installer_created_executable'] = Test-Path -LiteralPath (
        Join-Path $installDir '工程成长伴侣.exe'
    )
    $checks['installed_desktop_launch'] = Invoke-DesktopSmoke 'first-launch'
    $origin = Start-IsolatedSidecar
    $tokenUri = $origin + '/api/auth/token'
    $token = (Invoke-RestMethod -Uri $tokenUri -Headers @{ Origin = $origin }).token
    $headers = @{ Origin = $origin; 'X-AhaDiff-Token' = $token }
    $projectUri = $origin + '/api/growth/local/projects'
    $project = Invoke-RestMethod -Method Post -Uri $projectUri -Headers $headers `
        -ContentType 'application/json' -Body '{"name":"安装重装保留测试"}'
    $checks['installed_sidecar_wrote_project'] = [bool]$project.project_id
    $syncUri = $origin + '/api/growth/local/sync'
    $invalidCloud = Invoke-WebRequest -Method Post -Uri $syncUri -Headers $headers `
        -ContentType 'application/json' -SkipHttpErrorCheck `
        -Body '{"cloud_url":"http://example.com","access_token":"invalid"}'
    $checks['installed_sidecar_has_sync_route'] = $invalidCloud.StatusCode -eq 422
    $syncWorkspaceUri = $origin + '/api/growth/local/sync/workspace'
    $cachedWorkspace = Invoke-RestMethod -Uri $syncWorkspaceUri -Headers $headers
    $checks['installed_sidecar_has_offline_workspace'] = (
        $null -ne $cachedWorkspace.accounts -and
        $null -ne $cachedWorkspace.notes -and
        $null -ne $cachedWorkspace.tasks
    )
    $createdListing = Invoke-RestMethod -Uri ($origin + '/api/growth/local') -Headers $headers
    $createdPolicy = @($createdListing.projects | Where-Object {
        $_.project_id -eq $project.project_id
    })[0]
    $checks['installed_sidecar_has_project_policy'] = (
        $createdPolicy.local_processing -eq $true -and
        $createdPolicy.model_allowed -eq $false -and
        $createdPolicy.cloud_allowed -eq $false
    )
    $eventInbox = Invoke-RestMethod -Uri ($origin + '/api/growth/local/sync/events') `
        -Headers $headers
    $checks['installed_sidecar_has_event_inbox'] = $null -ne $eventInbox.events
    $exportProbe = Invoke-WebRequest -Method Post `
        -Uri ($origin + '/api/growth/local/cloud/exports') -Headers $headers `
        -ContentType 'application/json' -SkipHttpErrorCheck `
        -Body '{"cloud_url":"http://127.0.0.1:9001","access_token":"invalid","account_id":"00000000-0000-0000-0000-000000000001","export_id":"00000000-0000-0000-0000-000000000002"}'
    $checks['installed_sidecar_has_cloud_export_route'] = $exportProbe.StatusCode -eq 409
    $cloudAccessProbe = '{"cloud_url":"http://127.0.0.1:9001","access_token":"invalid","account_id":"00000000-0000-0000-0000-000000000001"}'
    $topicProbe = Invoke-WebRequest -Method Post `
        -Uri ($origin + '/api/growth/local/cloud/topics') -Headers $headers `
        -ContentType 'application/json' -SkipHttpErrorCheck -Body $cloudAccessProbe
    $checks['installed_sidecar_has_topic_maintenance_route'] = $topicProbe.StatusCode -eq 502
    $chatProbe = Invoke-WebRequest -Method Post `
        -Uri ($origin + '/api/growth/local/cloud/chats') -Headers $headers `
        -ContentType 'application/json' -SkipHttpErrorCheck -Body $cloudAccessProbe
    $checks['installed_sidecar_has_chat_route'] = $chatProbe.StatusCode -eq 502
    Stop-IsolatedSidecar

    Invoke-SilentInstall
    $checks['reinstalled_desktop_launch'] = Invoke-DesktopSmoke 'second-launch'
    $origin = Start-IsolatedSidecar
    $tokenUri = $origin + '/api/auth/token'
    $token = (Invoke-RestMethod -Uri $tokenUri -Headers @{ Origin = $origin }).token
    $headers = @{ Origin = $origin; 'X-AhaDiff-Token' = $token }
    $listUri = $origin + '/api/growth/local'
    $listing = Invoke-RestMethod -Uri $listUri -Headers $headers
    $checks['reinstall_preserved_project_id'] = [bool](@(
        $listing.projects | Where-Object { $_.project_id -eq $project.project_id }
    ).Count -eq 1)
    $checks['reinstall_preserved_growth_db'] = Test-Path -LiteralPath (
        Join-Path $workspace '.ahadiff\growth.sqlite'
    )
    Stop-IsolatedSidecar
}
catch {
    $checks['run_error'] = $false
    $errorText = $_.Exception.Message
}
finally {
    Stop-IsolatedSidecar
    if (Test-Path -LiteralPath $uninstaller) {
        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList '/S' `
            -WindowStyle Hidden -Wait -PassThru
        $checks['uninstaller_exited'] = $uninstall.ExitCode -eq 0
    } else {
        $checks['uninstaller_exited'] = $false
    }
    $checks['workspace_survived_uninstall'] = Test-Path -LiteralPath $workspace
    $status = if ($checks.Values -contains $false) { 'FAIL' } else { 'PASS' }
    $result = [ordered]@{
        run_id = $runId
        status = $status
        stage_status = 'BLOCKED'
        mode = 'replay'
        platform = 'win32-x64'
        installer_sha256 = $installerHash
        project_id = $project.project_id
        checks = $checks
        error = $errorText
    }
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (
        Join-Path $runDir 'result.json'
    ) -Encoding UTF8
    $report = "# M0 Windows 安装与重装核验`n`n" +
        "安装器 SHA-256：$installerHash。以独立安装目录和工作区执行安装、写入本机项目、重装、重读项目 ID、卸载。`n`n" +
        "检查结果：**$status**。未执行 macOS 安装，也未通过 S01–S04 live 情境。`n"
    Set-Content -LiteralPath (Join-Path $runDir 'report.md') -Value $report -Encoding UTF8 -NoNewline
    Write-Output ($result | ConvertTo-Json -Compress -Depth 6)
    if ($status -ne 'PASS') { throw "Windows 安装重装核验失败：$runDir" }
}
