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
            Invoke-Tool 'uv' @('sync','--project','backend','--locked','--group','dev','--group','desktop','--extra','wechat','--extra','captions','--no-install-project','--python',$toolchain.python)
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
        Push-Location -LiteralPath $targetPath
        try {
            switch ($Command) {
                'lint' { Invoke-Tool 'uv' @('run','--no-sync','ruff','check','--select','E9,F63,F7,F82','src') }
                'typecheck' { Invoke-Tool 'uv' @('run','--no-sync','pyright','--project','.', 'src/ahadiff/knowledge_platform') }
                'test' { Invoke-Tool 'uv' @('run','--no-sync','python','-m','pytest','tests/unit','../tests/repository','--junitxml=../artifacts/backend-unit.xml') }
                'test-integration' { Invoke-Tool 'uv' @('run','--no-sync','python','-m','pytest','tests/knowledge_platform','../tests/e2e','--junitxml=../artifacts/backend-integration.xml') }
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
