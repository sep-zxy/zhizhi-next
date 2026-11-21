param(
    [ValidateSet('help','check','install','lint','typecheck','test','test-integration','build','package')]
    [string]$Command = 'help',
    [ValidateSet('backend','learning-web','wiki-private','desktop','public-wiki','pi-zhizhi','vault-manager','continuity-widget','obsidian-ai-writer')]
    [string]$Target = 'backend',
    [switch]$All
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$toolchain = Get-Content -LiteralPath (Join-Path $repoRoot 'config/toolchains.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$targets = @{
    'backend' = @{ Path = 'backend'; Kind = 'python' }
    'learning-web' = @{ Path = 'apps/learning-web'; Kind = 'pnpm' }
    'wiki-private' = @{ Path = 'apps/wiki-private'; Kind = 'npm' }
    'desktop' = @{ Path = 'apps/desktop'; Kind = 'pnpm' }
    'public-wiki' = @{ Path = 'apps/public-wiki/site'; Kind = 'npm' }
    'pi-zhizhi' = @{ Path = 'integrations/pi-zhizhi'; Kind = 'npm' }
    'vault-manager' = @{ Path = 'optional/vault-manager'; Kind = 'rust' }
    'continuity-widget' = @{ Path = 'optional/continuity-widget'; Kind = 'rust-ui' }
    'obsidian-ai-writer' = @{ Path = 'optional/obsidian-ai-writer'; Kind = 'npm' }
}
function Invoke-Tool {
    param([string]$Name, [string[]]$Arguments)
    if (Get-Command rtk -ErrorAction SilentlyContinue) { & rtk proxy $Name @Arguments }
    else { & $Name @Arguments }
    if ($LASTEXITCODE -ne 0) { throw "$Name failed ($LASTEXITCODE)" }
}
function Invoke-Python {
    param([string[]]$Arguments)
    Invoke-Tool 'uv' (@('run','--project',(Join-Path $repoRoot 'backend'),'--no-sync','python') + $Arguments)
}
function Invoke-Js {
    param([string]$Script)
    $manager = if ($entry.Kind -eq 'pnpm') { 'pnpm' } else { 'npm' }
    Invoke-Tool $manager @('run',$Script)
}
if ($Command -eq 'help') {
    Write-Output 'check [-All]: local syntax only; no installs, tests, typechecks or builds.'
    Write-Output 'GitHub-hosted only: install|lint|typecheck|test|test-integration|build|package -Target <component>'
    Write-Output ($targets.Keys | Sort-Object)
    exit 0
}
Push-Location -LiteralPath $repoRoot
try {
    if ($Command -eq 'check') {
        $arguments = @('scripts/syntax.mjs')
        if ($All) { $arguments += '--all' }
        Invoke-Tool 'node' $arguments
        exit 0
    }
    if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
        throw '此命令仅供 GitHub 托管 Actions runner；本地默认仅 check。不要伪造环境标记。'
    }
    $entry = $targets[$Target]
    $targetPath = Join-Path $repoRoot $entry.Path
    New-Item -ItemType Directory -Path (Join-Path $repoRoot 'artifacts') -Force | Out-Null
    if ($Command -eq 'install') {
        if ($entry.Kind -eq 'python') {
            Invoke-Tool 'uv' @('sync','--project','backend','--locked','--group','dev','--extra','captions','--no-install-project','--python',$toolchain.python)
        } elseif ($entry.Kind -eq 'rust') {
            Invoke-Tool 'cargo' @('fetch','--locked','--manifest-path',"$targetPath/Cargo.toml")
        } else {
            Push-Location -LiteralPath $targetPath
            try {
                if ($entry.Kind -eq 'pnpm') { Invoke-Tool 'pnpm' @('install','--frozen-lockfile','--ignore-scripts') }
                else { Invoke-Tool 'npm' @('ci','--ignore-scripts','--no-audit','--fund=false') }
                if ($Target -eq 'public-wiki') {
                    $manifest = Get-Content -LiteralPath '.quartz/vendored-source-manifest.json' -Encoding UTF8 -Raw | ConvertFrom-Json
                    foreach ($plugin in $manifest.plugins.PSObject.Properties.Name) {
                        Push-Location -LiteralPath (Join-Path '.quartz/plugins' $plugin)
                        try { Invoke-Tool 'npm' @('ci','--ignore-scripts','--no-audit','--fund=false') } finally { Pop-Location }
                    }
                }
            } finally { Pop-Location }
            if ($entry.Kind -eq 'rust-ui') { Invoke-Tool 'cargo' @('fetch','--locked','--manifest-path',"$targetPath/Cargo.toml") }
        }
        exit 0
    }
    if ($entry.Kind -eq 'python') {
        $env:PYTHONPATH = (Join-Path $repoRoot 'backend/src') + [IO.Path]::PathSeparator + (Join-Path $repoRoot 'backend')
        Push-Location -LiteralPath $targetPath
        try {
            switch ($Command) {
                'lint' { Invoke-Tool 'uv' @('run','--no-sync','ruff','check','--select','E9,F63,F7,F82','src') }
                'typecheck' { Invoke-Tool 'uv' @('run','--no-sync','python','../scripts/check-python-types.py') }
                'test' {
                    Invoke-Tool 'uv' @(
                        'run','--no-sync','python','-m','pytest','tests/unit',
                        '--ignore=tests/unit/growth/test_m7_local_cloud_server.py',
                        '--ignore=tests/unit/serve/test_growth_cloud_export.py',
                        '--ignore=tests/unit/test_apkg_export.py',
                        '-m','not performance','-v','--tb=short','--junitxml=../artifacts/backend-unit.xml'
                    )
                }
                'test-integration' {
                    # Retain the test files for manual regression of historical delivery paths.
                    $historicalSuites = @(
                        '../tests/e2e/test_channel_configuration.py'
                        '../tests/e2e/test_channel_secret_recovery.py'
                        '../tests/e2e/test_desktop_configuration.py'
                        'tests/knowledge_platform/agent/test_domain_publication_integration.py'
                        'tests/knowledge_platform/agent/test_public_derivatives.py'
                        'tests/knowledge_platform/domain/test_acceptance_model.py'
                        'tests/knowledge_platform/domain/test_acceptance_scope.py'
                        'tests/knowledge_platform/domain/test_desktop_relation_bridge.py'
                        'tests/knowledge_platform/domain/test_operations_publication.py'
                        'tests/knowledge_platform/domain/test_pi_acceptance_scope.py'
                        'tests/knowledge_platform/domain/test_portable_isolation.py'
                        'tests/knowledge_platform/domain/test_publication_build.py'
                        'tests/knowledge_platform/domain/test_publication_certificate.py'
                        'tests/knowledge_platform/intake/test_channels_routes.py'
                        'tests/knowledge_platform/intake/test_native_notifications.py'
                        'tests/knowledge_platform/intake/test_official_transport.py'
                        'tests/knowledge_platform/intake/test_publication_status.py'
                        'tests/knowledge_platform/intake/test_wechat_sdk.py'
                        'tests/knowledge_platform/zhizhi/test_publication_certificates.py'
                        'tests/knowledge_platform/zhizhi/test_publish_artifacts.py'
                    )
                    $historicalCases = @(
                        'tests/knowledge_platform/agent/test_codex_notice_projection.py::test_closed_output_vocabulary_and_reviewed_source_manifest'
                        'tests/knowledge_platform/agent/test_codex_notice_templates.py::test_exact_templates_match_reviewed_official_source_record'
                        'tests/knowledge_platform/agent/test_codex_runner.py::test_exact_version_allowlist_matches_official_source_review_and_packaging_example'
                        'tests/knowledge_platform/agent/test_codex_runner.py::test_patch_review_records_changed_utf16_decoder_without_claiming_blob_identity'
                    )
                    $exclude = @($historicalSuites | ForEach-Object { "--ignore=$_" })
                    $exclude += @($historicalCases | ForEach-Object { "--deselect=$_" })
                    Invoke-Tool 'uv' (@(
                        'run','--no-sync','python','-m','pytest','tests/knowledge_platform','../tests/e2e',
                        '-v','--tb=short','--junitxml=../artifacts/backend-integration.xml'
                    ) + $exclude)
                }
                { $_ -in 'build','package' } {
                    $webDist = Join-Path $repoRoot 'apps/learning-web/dist'
                    if (-not (Test-Path -LiteralPath (Join-Path $webDist 'index.html'))) { throw '先构建 learning-web，后端 wheel 必须含真实前端产物。' }
                    New-Item -ItemType Directory -Path 'viewer' -Force | Out-Null
                    Copy-Item -LiteralPath $webDist -Destination 'viewer/dist' -Recurse -Force
                    Invoke-Tool 'uv' @('build','--wheel','--out-dir','../artifacts/backend')
                    $wheel = Get-ChildItem -LiteralPath '../artifacts/backend' -Filter '*.whl' | Select-Object -First 1
                    if (-not $wheel) { throw '缺少后端 wheel' }
                    Invoke-Tool 'uv' @('run','--no-sync','python','scripts/check_wheel_webui.py',$wheel.FullName)
                }
                default { throw "Unsupported Python command: $Command" }
            }
        } finally { Pop-Location }
        exit 0
    }
    if ($entry.Kind -in 'rust','rust-ui') {
        switch ($Command) {
            'lint' { Invoke-Tool 'cargo' @('fmt','--manifest-path',"$targetPath/Cargo.toml",'--','--check') }
            'typecheck' { Invoke-Tool 'cargo' @('check','--locked','--manifest-path',"$targetPath/Cargo.toml") }
            'test' { Invoke-Tool 'cargo' @('test','--locked','--manifest-path',"$targetPath/Cargo.toml") }
            { $_ -in 'build','package' } {
                Invoke-Tool 'cargo' @('build','--release','--locked','--manifest-path',"$targetPath/Cargo.toml")
                if ($entry.Kind -eq 'rust-ui') {
                    Push-Location -LiteralPath $targetPath
                    try { Invoke-Tool 'npm' @('run','build') } finally { Pop-Location }
                }
            }
            default { throw "Unsupported Rust command: $Command" }
        }
        exit 0
    }
    Push-Location -LiteralPath $targetPath
    try {
        switch ($Command) {
            'lint' {
                if ($Target -eq 'desktop') { Invoke-Tool 'node' @('--check','main.mjs') }
                else { Invoke-Js 'typecheck' }
            }
            'typecheck' { if ($Target -eq 'desktop') { Invoke-Tool 'node' @('--check','main.mjs') } else { Invoke-Js 'typecheck' } }
            'test' {
                if ($Target -eq 'public-wiki') { Invoke-Js 'test:core' }
                elseif ($Target -eq 'desktop') { Invoke-Tool 'node' @('--check','main.mjs') }
                else { Invoke-Js 'test' }
            }
            'test-integration' {
                if ($Target -eq 'pi-zhizhi') {
                    $env:PYTHONPATH = Join-Path $repoRoot 'backend/src'
                    $env:UV_PYTHON = Join-Path $repoRoot $(if ($IsWindows) {'backend/.venv/Scripts/python.exe'} else {'backend/.venv/bin/python'})
                    $env:VIRTUAL_ENV = Join-Path $repoRoot 'backend/.venv'
                    foreach ($case in @('bridge-schema-contract.mts','python-bridge-contract.mts','all-tools-python-handshake.mts','history-python-contract.mts','material-python-contract.mts')) {
                        Invoke-Tool 'node' @('--import','tsx',"test/$case")
                    }
                } else { throw '浏览器交互使用独立 CI 步骤和合成资料，不从通用入口隐式启动。' }
            }
            { $_ -in 'build','package' } {
                if ($Target -eq 'public-wiki') {
                    $manifest = Get-Content -LiteralPath '.quartz/vendored-source-manifest.json' -Encoding UTF8 -Raw | ConvertFrom-Json
                    foreach ($plugin in $manifest.plugins.PSObject.Properties.Name) {
                        Push-Location -LiteralPath (Join-Path '.quartz/plugins' $plugin)
                        try { Invoke-Tool 'npm' @('run','build') } finally { Pop-Location }
                    }
                    Invoke-Tool 'node' @('portable/build.mjs','runtime','portable/runtime.generated.mjs')
                } elseif ($Target -eq 'desktop') {
                    Invoke-Tool 'node' @('node_modules/electron/install.js')
                    & './build-sidecar.ps1'
                    Invoke-Js $(if ($IsWindows) { 'package:win' } elseif ($IsMacOS) { 'package:mac' } else { throw 'Desktop packaging requires Windows or macOS' })
                } else { Invoke-Js 'build' }
            }
            default { throw "Unsupported command: $Command" }
        }
    } finally { Pop-Location }
} finally { Pop-Location }
