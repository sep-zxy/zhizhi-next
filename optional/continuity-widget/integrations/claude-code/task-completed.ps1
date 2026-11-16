param(
    [Parameter(ValueFromPipeline = $true)]
    [string]$InputJson,
    [switch]$Submit
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($InputJson)) {
    $InputJson = [Console]::In.ReadToEnd()
}

$hook = $InputJson | ConvertFrom-Json
$metadata = $hook.task.metadata
$itemId = [string]$metadata.continuityItemId
$projectId = [string]$metadata.continuityProjectId

if ([string]::IsNullOrWhiteSpace($itemId) -or [string]::IsNullOrWhiteSpace($projectId)) {
    [ordered]@{ ok = $true; disposition = 'audit_only'; reason = 'missing_explicit_target' } | ConvertTo-Json -Compress
    exit 0
}

$eventId = "claude-code:$($hook.session_id):$($hook.task.id):completed-v1"
$event = [ordered]@{
    version = 1
    source = 'claude-code'
    eventId = $eventId
    eventType = 'work_item.completed'
    occurredAt = [DateTimeOffset]::Now.ToString('o')
    outcome = 'success'
    project = [ordered]@{ id = $projectId; path = $null }
    target = [ordered]@{ itemId = $itemId; fingerprint = $null; milestoneId = $null }
    evidence = [ordered]@{
        sourceRef = "claude-code:$($hook.session_id)"
        summary = "Claude Code TaskCompleted：$($hook.task.subject)"
        shortQuote = ''
    }
}

if ($Submit) {
    & 'C:\Code\codex\dev-tools\continuity-event.ps1' `
        -Source 'claude-code' `
        -EventId $eventId `
        -ProjectId $projectId `
        -ItemId $itemId `
        -Outcome 'success' `
        -SourceRef "claude-code:$($hook.session_id)" `
        -Summary "Claude Code TaskCompleted：$($hook.task.subject)"
    exit $LASTEXITCODE
}

$event | ConvertTo-Json -Depth 8 -Compress
