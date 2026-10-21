param(
  [string]$VaultPath = 'C:\Code\notes\zxy',
  [string]$SitePath = 'C:\Code\codex\selfweb\site',
  [string]$OssBucket = '',
  [string]$OssPrefix = 'assets',
  [string]$OssEndpoint = '',
  [string]$OssRegion = '',
  [string]$OssProfile = 'default',
  [string]$OssPublicBaseUrl = '',
  [switch]$DeleteOssExtra,
  [switch]$SkipOss,
  [string]$Server = '',
  [int]$ServerPort = 22,
  [string]$SshKeyPath = '',
  [string]$RemotePath = '/var/www/selfweb',
  [switch]$UseSudo,
  [switch]$SkipServer,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Get-RepoPath {
  return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}

function Get-OssutilPath {
  $command = Get-Command ossutil -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }

  $bundled = 'C:\Code\codex\tools\ossutil-2.3.0-windows-amd64\ossutil.exe'
  if (Test-Path -LiteralPath $bundled -PathType Leaf) { return $bundled }

  throw 'ossutil was not found. Open a new terminal or add C:\Code\codex\tools\ossutil-2.3.0-windows-amd64 to PATH.'
}

function Convert-ToShellSingleQuoted {
  param([string]$Value)
  return "'" + ($Value -replace "'", "'\''") + "'"
}

function Invoke-LoggedCommand {
  param(
    [string]$FilePath,
    [string[]]$ArgumentList
  )

  Write-Host "Running: $FilePath $($ArgumentList -join ' ')"
  & $FilePath @ArgumentList
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
  }
}

function Resolve-OssRegion {
  param(
    [string]$Region,
    [string]$Endpoint
  )

  if (-not [string]::IsNullOrWhiteSpace($Region)) {
    return $Region
  }

  if ($Endpoint -match '^oss-(?<region>[a-z0-9-]+)\.aliyuncs\.com$') {
    return $Matches['region']
  }

  return ''
}

function Clear-SitePublicOutput {
  param([string]$Path, [string]$SiteRoot)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
  $resolvedSiteRoot = (Resolve-Path -LiteralPath $SiteRoot).Path
  if (-not $resolvedPath.StartsWith($resolvedSiteRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clear public output outside site: $resolvedPath"
  }

  Remove-Item -LiteralPath $Path -Recurse -Force
}

function Resolve-OssPublicBaseUrl {
  param(
    [string]$ExplicitBaseUrl,
    [string]$Bucket,
    [string]$Endpoint
  )

  if (-not [string]::IsNullOrWhiteSpace($ExplicitBaseUrl)) {
    return $ExplicitBaseUrl.TrimEnd('/')
  }
  if ([string]::IsNullOrWhiteSpace($Bucket) -or [string]::IsNullOrWhiteSpace($Endpoint)) {
    return ''
  }
  return "https://$Bucket.$Endpoint"
}

function Convert-PublicAssetUrlsToOss {
  param(
    [string]$PublicPath,
    [string]$BaseUrl,
    [string]$Prefix
  )

  if ([string]::IsNullOrWhiteSpace($BaseUrl)) { return }
  $prefixClean = $Prefix.Trim('/')
  if ([string]::IsNullOrWhiteSpace($prefixClean)) { $prefixClean = 'assets' }
  $assetBase = $BaseUrl.TrimEnd('/') + '/' + $prefixClean + '/'
  $escapedAssetBase = [System.Text.RegularExpressions.Regex]::Escape($assetBase)
  $extensions = 'png|jpg|jpeg|gif|webp|svg|avif'
  $files = Get-ChildItem -LiteralPath $PublicPath -Recurse -File |
    Where-Object { $_.Extension.ToLowerInvariant() -in @('.html', '.css', '.js', '.json', '.xml') }

  foreach ($file in $files) {
    $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
    $updated = [regex]::Replace($text, '(?<prefix>["''(=]\s*)(?<path>(?:\.\./)*assets/)(?<file>[^"''\)\s?#]+\.(?:' + $extensions + '))', {
      param($match)
      return $match.Groups['prefix'].Value + $assetBase + $match.Groups['file'].Value
    }, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $updated = [regex]::Replace($updated, '(?<prefix>["''(=]\s*)/assets/(?<file>[^"''\)\s?#]+\.(?:' + $extensions + '))', {
      param($match)
      return $match.Groups['prefix'].Value + $assetBase + $match.Groups['file'].Value
    }, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $updated = [regex]::Replace($updated, $escapedAssetBase + '(?:\.\./)+assets/', $assetBase, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($updated -ne $text) {
      Set-Content -LiteralPath $file.FullName -Value $updated -Encoding UTF8
    }
  }
}

$repoPath = Get-RepoPath
$syncScript = Join-Path $repoPath 'scripts\sync-site.ps1'
$reportPath = Join-Path $repoPath 'reports\site-sync-report.json'
$publicPath = Join-Path $SitePath 'public'
$assetsPath = Join-Path $publicPath 'assets'

if (-not (Test-Path -LiteralPath $syncScript -PathType Leaf)) {
  throw "Sync script not found: $syncScript"
}

Clear-SitePublicOutput -Path $publicPath -SiteRoot $SitePath

Write-Host 'Step 1/4: sync content and build Quartz.'
& pwsh -NoProfile -ExecutionPolicy Bypass -File $syncScript -VaultPath $VaultPath -SitePath $SitePath -Build
if ($LASTEXITCODE -ne 0) {
  throw "Site build failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
  throw "Sync report not found: $reportPath"
}

$report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "Sync report: pages=$($report.publishedPageCount), images=$($report.copiedImageCount), canPublish=$($report.canPublish)"

if (-not $report.canPublish) {
  throw "Sync report says this build cannot be published. Check $reportPath"
}

if (-not (Test-Path -LiteralPath $publicPath -PathType Container)) {
  throw "Quartz public output not found: $publicPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $publicPath 'index.html') -PathType Leaf)) {
  throw "Quartz index.html not found under public output: $publicPath"
}

if (-not $SkipOss) {
  if ([string]::IsNullOrWhiteSpace($OssBucket)) {
    throw 'OssBucket is required unless -SkipOss is used.'
  }
  if (-not (Test-Path -LiteralPath $assetsPath -PathType Container)) {
    throw "Public assets directory not found: $assetsPath"
  }

  Write-Host 'Step 2/4: upload public assets to OSS.'
  $ossutil = Get-OssutilPath
  $ossDest = "oss://$OssBucket/$($OssPrefix.Trim('/'))"
  $ossArgs = @('sync', $assetsPath, $ossDest, '--force')
  if (-not [string]::IsNullOrWhiteSpace($OssProfile) -and $OssProfile -ne 'default') {
    $ossArgs += @('--profile', $OssProfile)
  }
  $resolvedOssRegion = Resolve-OssRegion -Region $OssRegion -Endpoint $OssEndpoint
  if (-not [string]::IsNullOrWhiteSpace($OssEndpoint)) {
    $ossArgs += @('--endpoint', $OssEndpoint)
  }
  if (-not [string]::IsNullOrWhiteSpace($resolvedOssRegion)) {
    $ossArgs += @('--region', $resolvedOssRegion)
  }
  if ($DeleteOssExtra) {
    $ossArgs += '--delete'
  }
  if ($DryRun) {
    $ossArgs += '--dry-run'
  }
  Invoke-LoggedCommand -FilePath $ossutil -ArgumentList $ossArgs
  $ossPublicBase = Resolve-OssPublicBaseUrl -ExplicitBaseUrl $OssPublicBaseUrl -Bucket $OssBucket -Endpoint $OssEndpoint
  if (-not [string]::IsNullOrWhiteSpace($OssPublicBaseUrl) -and -not [string]::IsNullOrWhiteSpace($ossPublicBase)) {
    Write-Host "Rewriting public asset URLs to OSS: $ossPublicBase/$($OssPrefix.Trim('/'))/"
    Convert-PublicAssetUrlsToOss -PublicPath $publicPath -BaseUrl $ossPublicBase -Prefix $OssPrefix
  } elseif (-not [string]::IsNullOrWhiteSpace($ossPublicBase)) {
    Write-Host "OSS upload finished; keeping local asset URLs because -OssPublicBaseUrl was not provided."
  }
} else {
  Write-Host 'Step 2/4: skip OSS upload.'
}

if (-not $SkipServer) {
  if ([string]::IsNullOrWhiteSpace($Server)) {
    throw 'Server is required unless -SkipServer is used. Example: -Server ubuntu@1.2.3.4'
  }
  if ([string]::IsNullOrWhiteSpace($RemotePath) -or $RemotePath -eq '/' -or -not $RemotePath.StartsWith('/')) {
    throw "RemotePath must be an absolute non-root path. Current value: $RemotePath"
  }

  Write-Host 'Step 3/4: package public output.'
  $archiveName = 'selfweb-public-' + (Get-Date -Format 'yyyyMMddHHmmss') + '.tgz'
  $archivePath = Join-Path (Join-Path $repoPath 'work') $archiveName
  if (-not (Test-Path -LiteralPath (Split-Path -Parent $archivePath))) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $archivePath) | Out-Null
  }
  if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
  }
  Invoke-LoggedCommand -FilePath 'tar' -ArgumentList @('-C', $publicPath, '-czf', $archivePath, '.')

  $sshArgs = @()
  $scpArgs = @()
  if ($ServerPort -ne 22) {
    $sshArgs += @('-p', [string]$ServerPort)
    $scpArgs += @('-P', [string]$ServerPort)
  }
  if (-not [string]::IsNullOrWhiteSpace($SshKeyPath)) {
    $sshArgs += @('-i', $SshKeyPath)
    $scpArgs += @('-i', $SshKeyPath)
  }

  $remoteArchive = "/tmp/$archiveName"
  if ($DryRun) {
    Write-Host "Dry run: would upload $archivePath to ${Server}:$remoteArchive"
    Write-Host "Dry run: would deploy to $RemotePath"
  } else {
    Write-Host 'Step 4/4: upload and unpack on Ubuntu server.'
    Invoke-LoggedCommand -FilePath 'scp' -ArgumentList ($scpArgs + @($archivePath, "${Server}:$remoteArchive"))

    $sudo = if ($UseSudo) { 'sudo ' } else { '' }
    $quotedRemotePath = Convert-ToShellSingleQuoted -Value $RemotePath
    $quotedArchive = Convert-ToShellSingleQuoted -Value $remoteArchive
    $remoteScript = @"
set -euo pipefail
archive=$quotedArchive
target=$quotedRemotePath
if [ -z "`$target" ] || [ "`$target" = "/" ]; then
  echo "Refusing to deploy to unsafe target: `$target" >&2
  exit 64
fi
deploy_tmp=`$(mktemp -d)
tar -xzf "`$archive" -C "`$deploy_tmp"
${sudo}mkdir -p "`$target"
${sudo}find "`$target" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
${sudo}cp -a "`$deploy_tmp"/. "`$target"/
rm -rf "`$deploy_tmp"
rm -f "`$archive"
if command -v nginx >/dev/null 2>&1; then
  ${sudo}nginx -t
  ${sudo}systemctl reload nginx
fi
"@
    $quotedRemoteScript = Convert-ToShellSingleQuoted -Value $remoteScript
    Invoke-LoggedCommand -FilePath 'ssh' -ArgumentList ($sshArgs + @($Server, "bash -lc $quotedRemoteScript"))
  }
} else {
  Write-Host 'Step 3/4 and 4/4: skip server deployment.'
}

Write-Host 'Deploy workflow finished.'
