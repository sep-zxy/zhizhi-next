param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$Release,
    [string]$InstallerPath = '',
    [string]$SupersedesRunId = '',
    [switch]$WithUiCodeIndex
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
$artifactDir = Join-Path $run 'artifacts'
$probe = Join-Path $artifactDir 'Probe.java'
$setupJson = Join-Path $artifactDir 'electron-setup.json'
$captureJson = Join-Path $artifactDir 'electron-capture.json'
$snapshotJson = Join-Path $artifactDir 'local-snapshot.json'
$indexJson = Join-Path $artifactDir 'code-index.json'
$sliceResult = Join-Path $artifactDir 'windows-slice-result.json'
$installedSuccessfully = $false

function Get-ArtifactRef([string]$RelativePath) {
    return [ordered]@{
        path = $RelativePath.Replace('\', '/')
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $run $RelativePath)).Hash.ToLowerInvariant()
    }
}

Push-Location -LiteralPath $root
try {
    $prepareArgs = @('run', 'python', '-m', 'ahadiff.growth.acceptance', 'prepare',
        '--case', 'S03', '--run-id', $RunId, '--release', $Release,
        '--candidate-sha256', $hash)
    if ($SupersedesRunId) {
        $prepareArgs += @('--supersedes-run-id', $SupersedesRunId)
    }
    rtk proxy uv @prepareArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'S03 素材准备失败' }
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
        Set-Content -LiteralPath (Join-Path $artifactDir 'windows-node.json') -Encoding UTF8

    $installProcess = Start-Process -FilePath $installer `
        -ArgumentList @('/S', "/D=$installed") -WindowStyle Hidden -Wait -PassThru
    if ($installProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $exe)) {
        throw 'Windows 安装器未生成桌面可执行文件'
    }
    $installedSuccessfully = $true

    @'
package demo.orders;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
public final class Probe {
    public static void main(String[] args) {
        AtomicInteger calls = new AtomicInteger();
        AtomicLong time = new AtomicLong();
        PricingGateway gateway = sku -> { calls.incrementAndGet(); return 1299; };
        OrderController controller = new OrderController(new OrderService(gateway, time::get));
        int first = controller.quote("SKU-1", 2);
        time.set(500);
        int second = controller.quote("SKU-1", 2);
        time.set(1000);
        int third = controller.quote("SKU-1", 2);
        System.out.println(first + "," + second + "," + third + "|" + calls.get());
    }
}
'@ | Set-Content -LiteralPath $probe -Encoding UTF8
    $sourceDir = Join-Path $repo 'src/demo/orders'
    $baseClasses = Join-Path $artifactDir 'base-classes'
    New-Item -ItemType Directory -Path $baseClasses -Force | Out-Null
    $baseSources = @(Get-ChildItem -LiteralPath $sourceDir -Filter '*.java' -File |
        Select-Object -ExpandProperty FullName)
    rtk proxy javac -encoding UTF-8 -d $baseClasses @baseSources $probe
    if ($LASTEXITCODE -ne 0) { throw '基线 Java 编译失败' }
    $baseOutput = rtk proxy java -cp $baseClasses demo.orders.Probe
    if ($LASTEXITCODE -ne 0) { throw '基线 Probe 执行失败' }
    $baseOutput | Set-Content -LiteralPath (Join-Path $artifactDir 'probe-baseline.txt') -Encoding UTF8

    rtk proxy node 'acceptance/scenarios/m7_windows_electron.mjs' setup `
        $exe $repo $baseSha $setupJson 'order-demo' '价格缓存' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '安装版 Electron 项目/Feature 操作失败' }
    $setup = Get-Content -LiteralPath $setupJson -Raw -Encoding UTF8 | ConvertFrom-Json

    $changeStartedAt = (Get-Date).ToUniversalTime().ToString('o')
    $changeTraceId = [guid]::NewGuid().ToString()
    $nextDir = Join-Path $run 'fixture/next/src/demo/orders'
    Copy-Item -LiteralPath (Join-Path $nextDir 'OrderService.java') -Destination $sourceDir -Force
    Copy-Item -LiteralPath (Join-Path $nextDir 'PriceCache.java') -Destination $sourceDir -Force
    $gitStatus = @(rtk git -C $repo status --porcelain | Where-Object { $_ -notmatch '^\[rtk\]' })
    if ($LASTEXITCODE -ne 0) { throw 'Git 工作区状态读取失败' }
    $targetClasses = Join-Path $artifactDir 'target-classes'
    New-Item -ItemType Directory -Path $targetClasses -Force | Out-Null
    $targetSources = @(Get-ChildItem -LiteralPath $sourceDir -Filter '*.java' -File |
        Select-Object -ExpandProperty FullName)
    rtk proxy javac -encoding UTF-8 -d $targetClasses @targetSources $probe
    if ($LASTEXITCODE -ne 0) { throw '改动版 Java 编译失败' }
    $targetOutput = rtk proxy java -cp $targetClasses demo.orders.Probe
    if ($LASTEXITCODE -ne 0) { throw '改动版 Probe 执行失败' }
    $targetOutput | Set-Content -LiteralPath (Join-Path $artifactDir 'probe-target.txt') -Encoding UTF8
    $gitStatus | Set-Content -LiteralPath (Join-Path $artifactDir 'git-status.txt') -Encoding UTF8
    $changeFinishedAt = (Get-Date).ToUniversalTime().ToString('o')

    $electronArgs = @(
        'acceptance/scenarios/m7_windows_electron.mjs', 'capture',
        $exe, $repo, $baseSha, $captureJson, 'src/demo/orders/PriceCache.java'
    )
    if ($WithUiCodeIndex) {
        $electronArgs += 'OrderController.quote -> OrderService.quote -> PriceCache.get'
    }
    rtk proxy node @electronArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '安装版 Electron 工作区快照失败' }
    $capture = Get-Content -LiteralPath $captureJson -Raw -Encoding UTF8 | ConvertFrom-Json
    rtk proxy uv run python 'acceptance/scenarios/m7_windows_snapshot_evidence.py' `
        $repo $capture.snapshot_id $snapshotJson | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '本机快照账本读取失败' }
    $snapshot = Get-Content -LiteralPath $snapshotJson -Raw -Encoding UTF8 | ConvertFrom-Json
    rtk proxy uv run python 'acceptance/scenarios/m7_s03_index_evidence.py' `
        $repo $baseSha $capture.snapshot_id $indexJson | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '同一快照 CodeGraph 索引失败' }
    $index = Get-Content -LiteralPath $indexJson -Raw -Encoding UTF8 | ConvertFrom-Json

    $sourcePaths = @($snapshot.source_refs | ForEach-Object { $_.relative_path })
    $cacheRef = @($snapshot.source_refs | Where-Object {
        $_.relative_path -eq 'src/demo/orders/PriceCache.java'
    })[0]
    $checks = [ordered]@{
        baseline_probe = ($baseOutput -join "`n") -eq '2598,2598,2598|3'
        installed_ui_created_ids = [bool]($setup.project_id -and $setup.binding_id -and
            $setup.feature_id)
        target_probe = ($targetOutput -join "`n") -eq '2598,2598,2598|2'
        uncommitted_worktree = ($gitStatus -join "`n") -match '\?\? src/demo/orders/PriceCache.java' -and
            ($gitStatus -join "`n") -match ' M src/demo/orders/OrderService.java'
        same_ui_identity = $setup.project_id -eq $capture.project_id -and
            $setup.binding_id -eq $capture.binding_id -and
            $setup.feature_id -eq $capture.feature_id
        snapshot_base_and_head = $snapshot.resolved_base_sha -eq $baseSha -and
            $snapshot.head_sha -eq $baseSha
        selected_untracked_in_snapshot = $capture.selected_untracked -eq
            'src/demo/orders/PriceCache.java' -and $sourcePaths.Count -eq 2 -and
            $sourcePaths -contains 'src/demo/orders/PriceCache.java' -and
            $sourcePaths -contains 'src/demo/orders/OrderService.java' -and
            $cacheRef.blob_hash -eq $cacheRef.stored_content_sha256
        snapshot_patch_and_event = $snapshot.diff_hash -eq $snapshot.patch_sha256 -and
            $snapshot.capture_event.event_type -eq 'snapshot_captured' -and
            [bool]$snapshot.capture_event.trace_id
        index_same_source = $index.snapshot_id -eq $snapshot.snapshot_id -and
            $index.effective_tree_hash -eq $snapshot.effective_tree_hash -and
            @($index.indexed_files) -contains 'src/demo/orders/PriceCache.java'
        indexed_main_flow = @($index.flow.edges).Count -eq 2 -and
            @($index.flow.edges | Where-Object { $_.status -eq 'indexed' }).Count -eq 2
    }
    if ($WithUiCodeIndex) {
        $checks['installed_ui_reports_index'] = $capture.code_index_text.Contains($index.index_revision) -and
            $capture.code_index_text.Contains('PriceCache.get')
    } else {
        $checks['local_diagnostic'] = @($snapshot.analysis_runs | Where-Object {
            $_.mode -eq 'dry_run' -and $_.status -eq 'succeeded'
        }).Count -eq 1 -and [bool]$capture.analysis_text
    }
    $status = if ($checks.Values -contains $false) { 'FAIL' } else { 'PASS' }
    $slice = [ordered]@{
        run_id = $RunId
        status = $status
        scope = 'S03-01 至 S03-03 Windows 安装版前段行为'
        candidate_sha256 = $hash
        base_sha = $baseSha
        snapshot_id = $capture.snapshot_id
        index_revision = $index.index_revision
        checks = $checks
    }
    $slice | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $sliceResult -Encoding UTF8
    if ($status -ne 'PASS') { throw 'Windows S03 前段行为断言失败' }

    $projectEvent = @($snapshot.local_events | Where-Object {
        $_.event_type -eq 'project_created'
    })[0]
    $steps = @(
        [ordered]@{
            schema_version = 1; run_id = $RunId; case_id = 'S03'
            step_id = 'S03-01'; status = 'PASS'
            started_at = $manifest.started_at; finished_at = $setup.finished_at
            device_id = $nodeId; trace_id = $projectEvent.trace_id
            action = '建立 C0、基线 Probe；安装版页面接入项目及仓库'
            expected = '无缓存：三次价格查询、结果 2598'
            actual = [ordered]@{
                base_sha = $baseSha; probe = $baseOutput -join "`n"
                project_id = $setup.project_id; binding_id = $setup.binding_id
                feature_id = $setup.feature_id
            }
            artifacts = @(
                (Get-ArtifactRef 'fixture/baseline.bundle'),
                (Get-ArtifactRef 'artifacts/probe-baseline.txt'),
                (Get-ArtifactRef 'artifacts/electron-setup.json')
            )
        },
        [ordered]@{
            schema_version = 1; run_id = $RunId; case_id = 'S03'
            step_id = 'S03-02'; status = 'PASS'
            started_at = $changeStartedAt; finished_at = $changeFinishedAt
            device_id = $nodeId; trace_id = $changeTraceId
            trace_origin = '验收驱动工作区与 Probe 操作'
            action = '新增未跟踪 PriceCache 并改动 OrderService；运行目标 Probe'
            expected = '惰性 TTL：1000ms 时第二次加载，总查询次数 2；文件尚未提交'
            actual = [ordered]@{
                probe = $targetOutput -join "`n"; git_status = $gitStatus
            }
            artifacts = @(
                (Get-ArtifactRef 'fixture/next/src/demo/orders/PriceCache.java'),
                (Get-ArtifactRef 'fixture/next/src/demo/orders/OrderService.java'),
                (Get-ArtifactRef 'artifacts/probe-target.txt'),
                (Get-ArtifactRef 'artifacts/git-status.txt')
            )
        },
        [ordered]@{
            schema_version = 1; run_id = $RunId; case_id = 'S03'
            step_id = 'S03-03'; status = 'PASS'
            started_at = $capture.started_at; finished_at = (Get-Date).ToUniversalTime().ToString('o')
            device_id = $nodeId; trace_id = $snapshot.capture_event.trace_id
            action = '安装版页面勾选未跟踪新文件并捕获快照；按同一源码指纹执行 CodeGraph 索引'
            expected = 'SourceSnapshot 与索引同时包含 PriceCache 且主调用边有源码依据'
            actual = [ordered]@{
                snapshot_id = $snapshot.snapshot_id
                base_sha = $snapshot.resolved_base_sha; head_sha = $snapshot.head_sha
                source_ref_id = $cacheRef.source_ref_id
                effective_tree_hash = $snapshot.effective_tree_hash
                index_revision = $index.index_revision
            }
            artifacts = @(
                (Get-ArtifactRef 'artifacts/electron-capture.json'),
                (Get-ArtifactRef 'artifacts/local-snapshot.json'),
                (Get-ArtifactRef 'artifacts/code-index.json'),
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
        base_sha = $baseSha
        index_revision = $index.index_revision
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
        --case S03 --mode live --run-id $RunId | Out-Null
    if ($LASTEXITCODE -ne 1) { throw '完整 S03 门槛未按预期保持 BLOCKED' }
    $verified = Get-Content -LiteralPath (Join-Path $run 'result.json') -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if (-not ($verified.checks.'S03-01' -and $verified.checks.'S03-02' -and
        $verified.checks.'S03-03' -and $verified.checks.windows_app -and
        $verified.checks.artifact_integrity)) {
        throw '验收门槛未接受三个真实前段步骤或证据哈希'
    }
    Add-Content -LiteralPath (Join-Path $run 'report.md') -Encoding UTF8 -Value @(
        '',
        '## Windows 安装版前段实测',
        '',
        "安装包 SHA-256：$hash。基线 Git：$baseSha；快照：$($capture.snapshot_id)。",
        '基线 Probe 三次 Gateway 查询；未提交 PriceCache 后，目标 Probe 两次查询并在 1000ms 惰性过期。',
        "安装版页面勾选新文件并捕获快照；CodeGraph 索引 revision：$($index.index_revision)。",
        '[行为检查](artifacts/windows-slice-result.json)、[界面捕获](artifacts/electron-capture.json)、[本机账本](artifacts/local-snapshot.json)及[源码索引](artifacts/code-index.json)保留原始 ID 与哈希。',
        '',
        '本次仅记录 S03-01 至 S03-03 三个 Windows 前段步骤。模块锁定、卡片、模型、云同步、ReMe 和 macOS 均未执行；完整 S03 仍为 BLOCKED。'
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
    if ($installedSuccessfully -and (Test-Path -LiteralPath $uninstaller)) {
        $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList '/S' `
            -WindowStyle Hidden -Wait -PassThru
        if ($uninstallProcess.ExitCode -ne 0) {
            Write-Warning "卸载器退出码：$($uninstallProcess.ExitCode)"
        }
    }
    Pop-Location
}
