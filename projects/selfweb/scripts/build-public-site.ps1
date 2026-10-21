param(
  [Parameter(Mandatory = $true)][string]$StagingPath,
  [Parameter(Mandatory = $true)][string]$OutputPath,
  [Parameter(Mandatory = $true)][string]$PolicyPath,
  [Parameter(Mandatory = $true)][string]$TemplateAssetsPath,
  [Parameter(Mandatory = $true)][string]$ReportPath,
  [Parameter(Mandatory = $true)][string]$PublicBaseUrl,
  [string]$DomainDatabase,
  [string]$OwnerId,
  [string]$RepositoryPath,
  [string]$DomainPolicyVersion = '1',
  [string]$SitePath = (Join-Path $PSScriptRoot '../site'),
  [string]$GrowthProjectPath = (Join-Path $PSScriptRoot '../../growth')
)
$ErrorActionPreference = 'Stop'
$staging = [IO.Path]::GetFullPath($StagingPath)
$output = [IO.Path]::GetFullPath($OutputPath)
if ($staging -eq $output -or $staging.StartsWith($output + [IO.Path]::DirectorySeparatorChar) -or
    $output.StartsWith($staging + [IO.Path]::DirectorySeparatorChar)) {
  throw 'Staging and output must be separate directories.'
}
if (Test-Path -LiteralPath $output) { throw 'Output must be new; the existing website is never deleted.' }
foreach ($inputFile in @((Join-Path $staging 'publish-manifest.json'), $PolicyPath, $TemplateAssetsPath)) {
  if (-not (Test-Path -LiteralPath $inputFile -PathType Leaf)) { throw "Required verified input missing: $inputFile" }
}
$verificationArguments = @()
if ($DomainDatabase -or $OwnerId -or $RepositoryPath) {
  if (-not ($DomainDatabase -and $OwnerId -and $RepositoryPath)) {
    throw 'DomainDatabase, OwnerId and RepositoryPath must be supplied together.'
  }
  $verificationArguments = @('--domain-database', $DomainDatabase, '--owner-id', $OwnerId, '--repository', $RepositoryPath, '--domain-policy-version', $DomainPolicyVersion)
}
$manifest = Get-Content (Join-Path $staging 'publish-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.requires_domain_revalidation -and -not $DomainDatabase) {
  throw 'Canonical public output requires a trusted read-only domain ledger for final validation.'
}
# This wrapper never installs plugins, mutates their source, calls a model or deploys.
# Prepare locked dependencies and compiled plugin outputs separately, then verify.
# Font caches are also an explicit verified build input, never an implicit network fallback.
& uv run --project $GrowthProjectPath --no-sync python (Join-Path $PSScriptRoot 'offline-fonts.py') --site $SitePath
if ($LASTEXITCODE -ne 0) { throw 'Approved offline font assets are missing or changed; restore the licensed asset bundle first.' }
$previous = $env:QUARTZ_OFFLINE_PLUGINS
$previousBaseUrl = $env:QUARTZ_PUBLIC_BASE_URL
try {
  $env:QUARTZ_OFFLINE_PLUGINS = '1'
  $env:QUARTZ_PUBLIC_BASE_URL = $PublicBaseUrl
  # The template process receives no domain database argument or inherited model/channel credentials.
  $start = [Diagnostics.ProcessStartInfo]::new()
  $start.FileName = (Get-Command node -CommandType Application).Source
  $start.WorkingDirectory = [IO.Path]::GetFullPath($SitePath)
  $start.UseShellExecute = $false
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $start.Environment.Clear()
  foreach ($name in @('PATH', 'TMPDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL')) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($value) { $start.Environment[$name] = $value }
  }
  $start.Environment['TZ'] = 'UTC'
  $start.Environment['QUARTZ_OFFLINE_PLUGINS'] = '1'
  $start.Environment['QUARTZ_PUBLIC_BASE_URL'] = $PublicBaseUrl
  foreach ($argument in @('quartz/bootstrap-cli.mjs', 'build', '-d', $staging, '-o', $output, '--concurrency=1')) {
    $start.ArgumentList.Add($argument)
  }
  $process = [Diagnostics.Process]::Start($start)
  $stdout = $process.StandardOutput.ReadToEndAsync()
  $stderr = $process.StandardError.ReadToEndAsync()
  $process.WaitForExit()
  Write-Output $stdout.GetAwaiter().GetResult()
  if ($stderr.GetAwaiter().GetResult()) { Write-Output $stderr.GetAwaiter().GetResult() }
  $nodeExit = $process.ExitCode
  $process.Dispose()
  if ($nodeExit -ne 0) { throw 'Quartz build failed; no publication is authorized.' }
  & uv run --project $GrowthProjectPath --no-sync python -m ahadiff.knowledge_platform.publish.verify `
    --output $output --manifest (Join-Path $staging 'publish-manifest.json') `
    --staging $staging `
    --policy $PolicyPath --template-assets $TemplateAssetsPath --report $ReportPath `
    @verificationArguments
  if ($LASTEXITCODE -ne 0) { throw 'Final artifact gate failed; no publication is authorized.' }
}
finally {
  $env:QUARTZ_OFFLINE_PLUGINS = $previous
  $env:QUARTZ_PUBLIC_BASE_URL = $previousBaseUrl
}
