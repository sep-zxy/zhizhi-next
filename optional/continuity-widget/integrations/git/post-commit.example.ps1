$ErrorActionPreference = 'Stop'

$message = & git log -1 --pretty=%B
$itemMatch = [regex]::Match(($message -join "`n"), '(?im)^Continuity-Item:\s*([0-9a-f-]{36})\s*$')
$projectMatch = [regex]::Match(($message -join "`n"), '(?im)^Continuity-Project:\s*([0-9a-f-]{36})\s*$')

if (-not $itemMatch.Success -or -not $projectMatch.Success) {
    exit 0
}

if ([string]::IsNullOrWhiteSpace($env:CONTINUITY_TEST_EVIDENCE)) {
    Write-Warning '检测到 Continuity trailer，但缺少 CONTINUITY_TEST_EVIDENCE；未提交完成事件。'
    exit 0
}

$commit = (& git rev-parse HEAD).Trim()
$event = [ordered]@{
    version = 1
    source = 'git'
    eventId = "git:${commit}:$($itemMatch.Groups[1].Value):completed-v1"
    eventType = 'work_item.completed'
    occurredAt = [DateTimeOffset]::Now.ToString('o')
    outcome = 'success'
    project = [ordered]@{ id = $projectMatch.Groups[1].Value; path = (Get-Location).Path }
    target = [ordered]@{ itemId = $itemMatch.Groups[1].Value; fingerprint = $null; milestoneId = $null }
    evidence = [ordered]@{ sourceRef = "git:$commit"; summary = $env:CONTINUITY_TEST_EVIDENCE; shortQuote = '' }
}

if ($env:CONTINUITY_SUBMIT -eq '1') {
    & 'C:\Code\codex\dev-tools\continuity-event.ps1' `
        -Source 'git' `
        -EventId $event.eventId `
        -ProjectId $projectMatch.Groups[1].Value `
        -ProjectPath (Get-Location).Path `
        -ItemId $itemMatch.Groups[1].Value `
        -Outcome 'success' `
        -SourceRef "git:$commit" `
        -Summary $env:CONTINUITY_TEST_EVIDENCE
    exit $LASTEXITCODE
}

$event | ConvertTo-Json -Depth 8 -Compress
