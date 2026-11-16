param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -gt 0) {
    $parseErrors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }
    exit 1
}
