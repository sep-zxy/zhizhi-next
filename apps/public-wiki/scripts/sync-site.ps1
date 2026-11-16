param(
  [Parameter(Mandatory = $true)][string]$VaultPath,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{40,64}$')][string]$Revision,
  [Parameter(Mandatory = $true)][string]$PolicyPath,
  [Parameter(Mandatory = $true)][string]$StagingPath,
  [string]$InternalManifestPath,
  [string]$PolicyVersion = '1',
  [string]$TemplateVersion = '1',
  [string]$DomainDatabase,
  [string]$OwnerId,
  [string]$DomainPolicyVersion = '1',
  [string]$GrowthProjectPath = (Join-Path $PSScriptRoot '../../../backend'),
  [string]$SitePath,
  [switch]$Build
)

$ErrorActionPreference = 'Stop'

# The historical adapter is preserved as legacy-sync-site.ps1.txt. This entry
# point never removes live Quartz content or invokes a model.
if ($Build) {
  throw 'This command only creates public staging. Use the explicitly authorized build workflow; deployment is separate.'
}
if ($SitePath) {
  throw 'Use -StagingPath for a new empty directory. Direct site mutation is no longer supported.'
}
if (-not (Test-Path -LiteralPath $PolicyPath -PathType Leaf)) {
  throw 'An explicit public-source and asset allowlist policy is required.'
}
$policy = Get-Content -LiteralPath $PolicyPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $policy) { throw 'Publication policy must be a JSON object.' }
$arguments = @(
  'run', '--project', $GrowthProjectPath, '--no-sync', 'python', '-m',
  'ahadiff.knowledge_platform.publish', '--repository', $VaultPath,
  '--revision', $Revision, '--staging', $StagingPath, '--policy', $PolicyPath,
  '--policy-version', $PolicyVersion, '--template-version', $TemplateVersion
)
if ($InternalManifestPath) { $arguments += @('--internal-manifest', $InternalManifestPath) }
if ([bool]$DomainDatabase -ne [bool]$OwnerId) { throw 'DomainDatabase and OwnerId must be supplied together.' }
if ($DomainDatabase) { $arguments += @('--domain-database', $DomainDatabase, '--owner-id', $OwnerId, '--domain-policy-version', $DomainPolicyVersion) }
& uv @arguments
if ($LASTEXITCODE -ne 0) { throw "Projection failed with exit code $LASTEXITCODE; the existing site was not changed." }
