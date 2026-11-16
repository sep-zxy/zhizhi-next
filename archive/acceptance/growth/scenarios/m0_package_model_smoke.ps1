param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$repo = (Resolve-Path -LiteralPath $RepositoryPath).Path
$exe = (Resolve-Path -LiteralPath (Join-Path $root 'dist\desktop\win-unpacked\resources\sidecar\growth-sidecar.exe')).Path
$viewer = (Resolve-Path -LiteralPath (Join-Path $root 'dist\desktop\win-unpacked\resources\viewer')).Path
$runId = 'm0-package-model-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runDir = Join-Path $root (Join-Path 'acceptance\runs' $runId)
$workspace = Join-Path $runDir 'workspace'
New-Item -ItemType Directory -Path $workspace -Force | Out-Null

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
$origin = 'http://127.0.0.1:' + $port
$env:AHADIFF_VIEWER_DIST = $viewer
$process = Start-Process -FilePath $exe -ArgumentList @(
    'serve', '--repo-root', $workspace, '--port', [string]$port, '--no-browser'
) -WorkingDirectory $workspace -WindowStyle Hidden -PassThru

try {
    $ready = $false
    for ($index = 0; $index -lt 100; $index++) {
        try {
            $health = Invoke-RestMethod -Uri ($origin + '/healthz') -TimeoutSec 2
            if ($health.ok) { $ready = $true; break }
        }
        catch { Start-Sleep -Milliseconds 300 }
    }
    if (-not $ready) { throw '打包 sidecar 未启动' }

    $token = (Invoke-RestMethod -Uri ($origin + '/api/auth/token') -Headers @{ Origin = $origin }).token
    $headers = @{ Origin = $origin; 'X-AhaDiff-Token' = $token }
    $project = Invoke-RestMethod -Method Post -Uri ($origin + '/api/growth/local/projects') `
        -Headers $headers -ContentType 'application/json' -Body '{"name":"打包模型入口核验"}'
    $bindingBody = @{ path = $repo } | ConvertTo-Json -Compress
    $binding = Invoke-RestMethod -Method Post `
        -Uri ($origin + '/api/growth/local/projects/' + $project.project_id + '/bindings') `
        -Headers $headers -ContentType 'application/json' -Body $bindingBody
    $featureBody = @{
        binding_id = $binding.binding_id
        label = '打包模型入口'
        base_ref = 'HEAD'
    } | ConvertTo-Json -Compress
    $feature = Invoke-RestMethod -Method Post `
        -Uri ($origin + '/api/growth/local/projects/' + $project.project_id + '/features') `
        -Headers $headers -ContentType 'application/json' -Body $featureBody
    $captureBody = @{ binding_id = $binding.binding_id } | ConvertTo-Json -Compress
    $snapshot = Invoke-RestMethod -Method Post `
        -Uri ($origin + '/api/growth/local/features/' + $feature.feature_id + '/snapshots') `
        -Headers $headers -ContentType 'application/json' -Body $captureBody
    $sources = Invoke-RestMethod `
        -Uri ($origin + '/api/growth/local/snapshots/' + $snapshot.snapshot_id + '/model-sources?binding_id=' + $binding.binding_id) `
        -Headers $headers
    if (-not $sources.sources -or $sources.sources.Count -lt 1) {
        throw '打包版未返回源码引用'
    }
    $localState = Invoke-RestMethod -Uri ($origin + '/api/growth/local') -Headers $headers
    if (-not ($localState.PSObject.Properties.Name -contains 'topic_proposals') -or
        -not ($localState.PSObject.Properties.Name -contains 'growth_tasks')) {
        throw '打包版未返回学习状态接口'
    }
    $result = [ordered]@{
        status = 'PASS'
        run_id = $runId
        packaged_exe = $exe
        project_id = $project.project_id
        binding_id = $binding.binding_id
        feature_id = $feature.feature_id
        snapshot_id = $snapshot.snapshot_id
        source_count = $sources.sources.Count
        provider_count = $sources.provider_names.Count
        learning_state = $true
        note = '仅本机打包接口核验，非 S01-S04 live 验收'
    }
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runDir 'result.json') -Encoding UTF8
    Write-Output ($result | ConvertTo-Json -Compress)
}
finally {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    Remove-Item Env:AHADIFF_VIEWER_DIST -ErrorAction SilentlyContinue
}
