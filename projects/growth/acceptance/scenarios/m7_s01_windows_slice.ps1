param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$Release,
    [string]$InstallerPath = '',
    [string]$SupersedesRunId = '',
    [switch]$KeepInstalled
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $root 'dist/desktop/工程成长伴侣 Setup 0.1.0.exe'
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
$run = Join-Path $root (Join-Path 'acceptance/runs' $RunId)
$repo = Join-Path $run 'fixture/repo'
$installed = Join-Path $run 'installed'
$exe = Join-Path $installed '工程成长伴侣.exe'
$uninstaller = Join-Path $installed 'Uninstall 工程成长伴侣.exe'
$probe = Join-Path $run 'artifacts/RouteProbe.java'
$setupJson = Join-Path $run 'artifacts/electron-setup.json'
$captureJson = Join-Path $run 'artifacts/electron-capture.json'
$snapshotJson = Join-Path $run 'artifacts/local-snapshot.json'
$sliceResult = Join-Path $run 'artifacts/windows-slice-result.json'
$installedSuccessfully = $false

function Get-ArtifactRef([string]$RelativePath) {
    $absolute = Join-Path $run $RelativePath
    return [ordered]@{
        path = $RelativePath.Replace('\', '/')
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $absolute).Hash.ToLowerInvariant()
    }
}

Push-Location -LiteralPath $root
try {
    $prepareArgs = @('run', 'python', '-m', 'ahadiff.growth.acceptance', 'prepare',
        '--case', 'S01', '--run-id', $RunId, '--release', $Release,
        '--candidate-sha256', $hash)
    if ($SupersedesRunId) { $prepareArgs += @('--supersedes-run-id', $SupersedesRunId) }
    rtk proxy uv @prepareArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'S01 素材准备失败' }
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
        Set-Content -LiteralPath (Join-Path $run 'artifacts/windows-node.json') -Encoding UTF8

    $installProcess = Start-Process -FilePath $installer `
        -ArgumentList @('/S', "/D=$installed") -WindowStyle Hidden -Wait -PassThru
    if ($installProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $exe)) {
        throw 'Windows 安装器未生成桌面可执行文件'
    }
    $installedSuccessfully = $true

    @'
import demo.route.RoutePlanner;
import java.util.concurrent.ConcurrentSkipListSet;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.atomic.AtomicInteger;
public final class RouteProbe {
    public static void main(String[] args) {
        AtomicInteger next = new AtomicInteger();
        ExecutorService executor = Executors.newFixedThreadPool(2, action ->
            new Thread(action, "route-worker-" + next.incrementAndGet()));
        ConcurrentSkipListSet<String> threads = new ConcurrentSkipListSet<>();
        try {
            String result = new RoutePlanner().plan(route -> {
                threads.add(Thread.currentThread().getName());
                return route;
            }, executor);
            System.out.println("result=" + result);
            System.out.println("query_threads=" + String.join(",", threads));
        } finally {
            executor.shutdown();
        }
    }
}
'@ | Set-Content -LiteralPath $probe -Encoding UTF8
    $source = Join-Path $repo 'src/demo/route/RoutePlanner.java'
    $baseClasses = Join-Path $run 'artifacts/base-classes'
    New-Item -ItemType Directory -Path $baseClasses -Force | Out-Null
    rtk proxy javac -encoding UTF-8 -d $baseClasses $source $probe
    if ($LASTEXITCODE -ne 0) { throw '基线 Java 编译失败' }
    $baseOutput = rtk proxy java -cp $baseClasses RouteProbe
    if ($LASTEXITCODE -ne 0) { throw '基线 Probe 执行失败' }
    $baseOutput | Set-Content -LiteralPath (Join-Path $run 'artifacts/probe-baseline.txt') -Encoding UTF8

    rtk proxy node 'acceptance/scenarios/m7_windows_electron.mjs' setup `
        $exe $repo $baseSha $setupJson | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '安装版 Electron 项目/Feature 操作失败' }
    $setup = Get-Content -LiteralPath $setupJson -Raw -Encoding UTF8 | ConvertFrom-Json

    $targetStartedAt = (Get-Date).ToUniversalTime().ToString('o')
    $targetTraceId = [guid]::NewGuid().ToString()
    Copy-Item -LiteralPath (Join-Path $run 'fixture/next/src/demo/route/RoutePlanner.java') `
        -Destination $source -Force
    rtk git -C $repo add -- 'src/demo/route/RoutePlanner.java'
    if ($LASTEXITCODE -ne 0) { throw '目标源码暂存失败' }
    rtk git -C $repo commit -m '将路线查询改为显式执行器并发调用' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '目标源码提交失败' }
    $headSha = (rtk git -C $repo rev-parse HEAD | Select-Object -Last 1).Trim()

    $targetClasses = Join-Path $run 'artifacts/target-classes'
    New-Item -ItemType Directory -Path $targetClasses -Force | Out-Null
    rtk proxy javac -encoding UTF-8 -d $targetClasses $source $probe
    if ($LASTEXITCODE -ne 0) { throw '目标 Java 编译失败' }
    $targetOutput = rtk proxy java -cp $targetClasses RouteProbe
    if ($LASTEXITCODE -ne 0) { throw '目标 Probe 执行失败' }
    $targetOutput | Set-Content -LiteralPath (Join-Path $run 'artifacts/probe-target.txt') -Encoding UTF8
    $targetFinishedAt = (Get-Date).ToUniversalTime().ToString('o')

    rtk proxy node 'acceptance/scenarios/m7_windows_electron.mjs' capture `
        $exe $repo $baseSha $captureJson | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '安装版 Electron 快照操作失败' }
    $capture = Get-Content -LiteralPath $captureJson -Raw -Encoding UTF8 | ConvertFrom-Json
    rtk proxy uv run python 'acceptance/scenarios/m7_windows_snapshot_evidence.py' `
        $repo $capture.snapshot_id $snapshotJson | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '本机业务账本读取失败' }
    $snapshot = Get-Content -LiteralPath $snapshotJson -Raw -Encoding UTF8 | ConvertFrom-Json

    $checks = [ordered]@{
        baseline_probe = ($baseOutput -join "`n") -match 'result=A\|B' -and
            ($baseOutput -join "`n") -match 'query_threads=main'
        installed_ui_created_ids = [bool]($setup.project_id -and $setup.binding_id -and
            $setup.feature_id)
        target_probe = ($targetOutput -join "`n") -match 'result=A\|B' -and
            ($targetOutput -join "`n") -match 'query_threads=route-worker-'
        same_ui_identity = $setup.project_id -eq $capture.project_id -and
            $setup.binding_id -eq $capture.binding_id -and
            $setup.feature_id -eq $capture.feature_id
        git_base_and_head = $snapshot.resolved_base_sha -eq $baseSha -and
            $snapshot.head_sha -eq $headSha
        captured_source = @($snapshot.source_refs).Count -eq 1 -and
            $snapshot.source_refs[0].relative_path -eq 'src/demo/route/RoutePlanner.java' -and
            $snapshot.source_refs[0].blob_hash -eq
                $snapshot.source_refs[0].stored_content_sha256
        patch_and_event = $snapshot.diff_hash -eq $snapshot.patch_sha256 -and
            $snapshot.capture_event.event_type -eq 'snapshot_captured' -and
            [bool]$snapshot.capture_event.trace_id
        ui_reports_snapshot = $capture.snapshot_id -eq $snapshot.snapshot_id -and
            $capture.capture_text.Contains('src/demo/route/RoutePlanner.java')
        local_diagnostic = @($snapshot.analysis_runs | Where-Object {
            $_.mode -eq 'dry_run' -and $_.status -eq 'succeeded'
        }).Count -eq 1 -and [bool]$capture.analysis_text
    }
    $status = if ($checks.Values -contains $false) { 'FAIL' } else { 'PASS' }
    $slice = [ordered]@{
        run_id = $RunId
        status = $status
        scope = 'S01-01 至 S01-03 Windows 安装版前段行为'
        candidate_sha256 = $hash
        base_sha = $baseSha
        head_sha = $headSha
        snapshot_id = $capture.snapshot_id
        checks = $checks
    }
    $slice | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $sliceResult -Encoding UTF8
    if ($status -ne 'PASS') { throw 'Windows S01 前段行为断言失败' }

    $projectEvent = @($snapshot.local_events | Where-Object {
        $_.event_type -eq 'project_created'
    })[0]
    $analysis = @($snapshot.analysis_runs | Where-Object {
        $_.mode -eq 'dry_run' -and $_.status -eq 'succeeded'
    })[0]
    $steps = @(
        [ordered]@{
            schema_version = 1; run_id = $RunId; case_id = 'S01'
            step_id = 'S01-01'; status = 'PASS'
            started_at = $manifest.started_at; finished_at = $setup.finished_at
            device_id = $nodeId; trace_id = $projectEvent.trace_id
            action = '真实 Git C0 与基线 Probe；安装版 Electron 页面绑定项目和仓库'
            expected = '基线返回 A|B，仓库、项目和 Feature ID 建立'
            actual = [ordered]@{
                base_sha = $baseSha; probe = $baseOutput -join "`n"
                project_id = $setup.project_id; binding_id = $setup.binding_id
                feature_id = $setup.feature_id
            }
            artifacts = @(
                (Get-ArtifactRef 'fixture/baseline.bundle'),
                (Get-ArtifactRef 'artifacts/probe-baseline.txt'),
                (Get-ArtifactRef 'artifacts/electron-setup.json'),
                (Get-ArtifactRef 'artifacts/windows-node.json')
            )
        },
        [ordered]@{
            schema_version = 1; run_id = $RunId; case_id = 'S01'
            step_id = 'S01-02'; status = 'PASS'
            started_at = $targetStartedAt; finished_at = $targetFinishedAt
            device_id = $nodeId; trace_id = $targetTraceId
            trace_origin = '验收驱动 Git 与 Probe 操作'
            action = '提交显式执行器并发改动并运行目标 Probe'
            expected = '目标返回 A|B，查询在传入执行器线程上运行'
            actual = [ordered]@{
                base_sha = $baseSha; head_sha = $headSha
                probe = $targetOutput -join "`n"
            }
            artifacts = @(
                (Get-ArtifactRef 'fixture/next/src/demo/route/RoutePlanner.java'),
                (Get-ArtifactRef 'artifacts/probe-target.txt')
            )
        },
        [ordered]@{
            schema_version = 1; run_id = $RunId; case_id = 'S01'
            step_id = 'S01-03'; status = 'PASS'
            started_at = $capture.started_at; finished_at = $capture.finished_at
            device_id = $nodeId; trace_id = $snapshot.capture_event.trace_id
            action = '安装版 Electron 页面捕获 Git 快照并运行本机诊断'
            expected = '快照引用 C0、当前 HEAD 和唯一源码文件，诊断成功'
            actual = [ordered]@{
                project_id = $snapshot.project_id; feature_id = $snapshot.feature_id
                snapshot_id = $snapshot.snapshot_id; analysis_id = $analysis.analysis_id
                base_sha = $snapshot.resolved_base_sha; head_sha = $snapshot.head_sha
                diff_hash = $snapshot.diff_hash
                source_ref_id = $snapshot.source_refs[0].source_ref_id
            }
            artifacts = @(
                (Get-ArtifactRef 'artifacts/electron-capture.json'),
                (Get-ArtifactRef 'artifacts/local-snapshot.json'),
                (Get-ArtifactRef 'artifacts/windows-slice-result.json')
            )
        }
    )
    $steps | ForEach-Object { $_ | ConvertTo-Json -Compress -Depth 10 } |
        Set-Content -LiteralPath (Join-Path $run 'steps.jsonl') -Encoding UTF8
    $snapshot.local_events | ForEach-Object { $_ | ConvertTo-Json -Compress -Depth 10 } |
        Set-Content -LiteralPath (Join-Path $run 'events.jsonl') -Encoding UTF8
    [ordered]@{
        project_id = $snapshot.project_id
        binding_id = $snapshot.binding_id
        feature_id = $snapshot.feature_id
        snapshot_id = $snapshot.snapshot_id
        analysis_id = $analysis.analysis_id
        base_sha = $baseSha
        head_sha = $headSha
    } | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $run 'chain.json') -Encoding UTF8

    $preparedResult = Get-Content -LiteralPath (Join-Path $run 'result.json') -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $preparedResult | Add-Member -NotePropertyName proof -Force `
        -NotePropertyValue ([ordered]@{
            windows_app = Get-ArtifactRef 'artifacts/windows-slice-result.json'
        })
    $preparedResult | ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath (Join-Path $run 'result.json') -Encoding UTF8
    rtk proxy uv run python -m ahadiff.growth.acceptance run `
        --case S01 --mode live --run-id $RunId | Out-Null
    if ($LASTEXITCODE -ne 1) { throw '完整 S01 门槛未按预期保持 BLOCKED' }
    $verified = Get-Content -LiteralPath (Join-Path $run 'result.json') -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if (-not ($verified.checks.'S01-01' -and $verified.checks.'S01-02' -and
        $verified.checks.'S01-03' -and $verified.checks.windows_app -and
        $verified.checks.artifact_integrity)) {
        throw '验收门槛未接受三个真实前段步骤或证据哈希'
    }
    Add-Content -LiteralPath (Join-Path $run 'report.md') -Encoding UTF8 -Value @(
        '',
        '## Windows 安装版前段实测',
        '',
        "安装包 SHA-256：$hash。真实安装后，经 Electron 页面创建项目、绑定 Git 仓库、建立 Feature 并捕获快照。",
        "基线 Git：$baseSha；改动 Git：$headSha；快照：$($capture.snapshot_id)。",
        '基线 Probe 返回 A|B 且查询运行在 main；改动版返回 A|B 且查询运行在 route-worker 线程。安装版页面的本机诊断已完成。',
        '[行为检查](artifacts/windows-slice-result.json)、[界面捕获](artifacts/electron-capture.json)、[本机账本](artifacts/local-snapshot.json)均保留原始 ID 与哈希。',
        '',
        '本次记录 S01-01 至 S01-03 三个真实前段步骤。节点 ID 是本机验收标识，云设备尚未注册。模型机会、卡片、答案、笔记、云同步、ReMe、复习和 macOS 均未执行；完整 S01 仍为 BLOCKED。'
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
    if (-not $KeepInstalled -and $installedSuccessfully -and (Test-Path -LiteralPath $uninstaller)) {
        $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList '/S' `
            -WindowStyle Hidden -Wait -PassThru
        if ($uninstallProcess.ExitCode -ne 0) {
            Write-Warning "卸载器退出码：$($uninstallProcess.ExitCode)"
        }
    }
    Pop-Location
}
