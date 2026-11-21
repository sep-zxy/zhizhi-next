param([Parameter(Mandatory = $true)][string]$Target, [Parameter(Mandatory = $true)][string]$Output)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$destination = [IO.Path]::GetFullPath((Join-Path $root $Output))
if (-not $destination.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) { throw 'Output must be within the checkout' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$source = (& git -C $root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot read source identity' }
$files = @(Get-ChildItem -LiteralPath $destination -File -Recurse -Force | Where-Object FullName -NotIn @(
    (Join-Path $destination 'build-receipt.json'), (Join-Path $destination 'SHA256SUMS.txt')
) | Sort-Object FullName)
if ($files.Count -eq 0) { throw 'No build artifact was collected' }
$checksums = foreach ($file in $files) {
    $relative = [IO.Path]::GetRelativePath($destination, $file.FullName).Replace('\','/')
    "$((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $relative"
}
$checksums | Set-Content -LiteralPath (Join-Path $destination 'SHA256SUMS.txt') -Encoding UTF8
@{schema_version=1;source=$source;target=$Target;run_id=$env:GITHUB_RUN_ID;run_attempt=$env:GITHUB_RUN_ATTEMPT;repository=$env:GITHUB_REPOSITORY;files=$files.Count;live_e2e_verified=$false;deploy_executed=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'build-receipt.json') -Encoding UTF8
