param(
    [Parameter(Mandatory = $true)][string]$RunDirectory,
    [string]$BaseUrl = 'http://127.0.0.1:8782'
)

$ErrorActionPreference = 'Stop'
$run = [System.IO.Path]::GetFullPath($RunDirectory)
$manifest = Get-Content -LiteralPath (Join-Path $run 'fixture-manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if ($manifest.case_id -ne 'S02') { throw '夹具不是 S02' }
if (-not (Test-Path -LiteralPath (Join-Path $run 'probe-result.json'))) {
    throw 'S02 受控 Promise 前后 Probe 尚未完成'
}
$headers = @{ Origin = $BaseUrl }
$headers['X-AhaDiff-Token'] = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/auth/token" -Headers $headers).token

function Invoke-GrowthApi {
    param([string]$Method, [string]$Path, [object]$Body)
    $arguments = @{ Method = $Method; Uri = "$BaseUrl$Path"; Headers = $headers }
    if ($null -ne $Body) {
        $arguments['ContentType'] = 'application/json'
        $arguments['Body'] = $Body | ConvertTo-Json -Depth 15 -Compress
    }
    try {
        return Invoke-RestMethod @arguments
    } catch {
        throw "$Method $Path 失败：$($_.ErrorDetails.Message)"
    }
}

function Invoke-GrowthChatStream {
    param([string]$Path, [object]$Body, [switch]$StopAfterFirstDelta)
    $http = [System.Net.Http.HttpClient]::new()
    $http.Timeout = [TimeSpan]::FromMinutes(5)
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$BaseUrl$Path")
    $request.Headers.Add('Origin', $BaseUrl)
    $request.Headers.Add('X-AhaDiff-Token', [string]$headers['X-AhaDiff-Token'])
    $request.Content = [System.Net.Http.StringContent]::new(
        ($Body | ConvertTo-Json -Depth 12 -Compress), [System.Text.Encoding]::UTF8, 'application/json'
    )
    $response = $null
    $reader = $null
    $deltaCount = 0
    $reply = ''
    $completed = $null
    $eventName = ''
    try {
        $response = $http.Send($request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead)
        if (-not $response.IsSuccessStatusCode) {
            throw "流式聊天 HTTP $([int]$response.StatusCode): $($response.Content.ReadAsStringAsync().Result)"
        }
        $reader = [System.IO.StreamReader]::new($response.Content.ReadAsStream(), [System.Text.Encoding]::UTF8)
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line.StartsWith('event:')) { $eventName = $line.Substring(6).Trim(); continue }
            if (-not $line.StartsWith('data:')) { continue }
            $payload = $line.Substring(5).TrimStart() | ConvertFrom-Json -Depth 30
            if ($eventName -eq 'error') { throw "流式聊天失败: $payload" }
            if ($eventName -eq 'delta') {
                if ($payload -isnot [string]) { throw '流式聊天增量格式错误' }
                $reply += $payload
                $deltaCount++
                if ($StopAfterFirstDelta -and $payload.Trim()) { break }
            }
            if ($eventName -eq 'done') { $completed = $payload }
        }
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
        $request.Dispose()
        $http.Dispose()
    }
    if ($StopAfterFirstDelta) {
        if ($deltaCount -lt 1 -or -not $reply.Trim() -or $completed) {
            throw '停止前未收到真实流式回答片段'
        }
        return @{ delta_count = $deltaCount; partial_chars = $reply.Length }
    }
    if (-not $completed -or $deltaCount -lt 1 -or $reply -ne $completed.reply) {
        throw "流式聊天未完整返回：delta=$deltaCount done=$([bool]$completed)"
    }
    return @{ delta_count = $deltaCount; reply = $completed.reply; conversation = $completed.conversation }
}

$state = Invoke-GrowthApi 'Get' '/api/growth/local' $null
if (@($state.bindings | Where-Object canonical_local_path -eq $manifest.repo).Count -ne 0) {
    throw 'S02 目标仓库已导入；重跑必须使用新的 run 目录'
}
Invoke-GrowthApi 'Post' '/api/growth/local/import' @{
    name = '搜索结果竞态修复'; path = $manifest.repo; model_allowed = $true
} | Out-Null
$state = Invoke-GrowthApi 'Get' '/api/growth/local' $null
$binding = @($state.bindings | Where-Object canonical_local_path -eq $manifest.repo)[0]
$feature = @($state.features | Where-Object project_id -eq $binding.project_id)[0]
if (-not $binding -or -not $feature) { throw 'S02 仓库绑定或功能不存在' }
$snapshot = Invoke-GrowthApi 'Post' "/api/growth/local/features/$($feature.feature_id)/snapshots" @{
    binding_id = $binding.binding_id; commit_sha = $manifest.head_sha
}
if ($snapshot.head_sha -ne $manifest.head_sha) { throw 'S02 捕获的 HEAD 不是目标提交' }
$sources = Invoke-GrowthApi 'Get' "/api/growth/local/snapshots/$($snapshot.snapshot_id)/model-sources?binding_id=$($binding.binding_id)" $null
$source = @($sources.sources | Where-Object { $_.relative_path -eq 'src/search.ts' -and -not $_.deleted })[0]
if (-not $source) { throw 'S02 模型可见源码缺少 src/search.ts' }
$sourceIds = @([string]$source.source_ref_id)
$preview = Invoke-GrowthApi 'Post' "/api/growth/local/snapshots/$($snapshot.snapshot_id)/model-preview" @{
    binding_id = $binding.binding_id; provider_name = 'deepseek'; source_ref_ids = $sourceIds
}
$queued = Invoke-GrowthApi 'Post' "/api/growth/local/snapshots/$($snapshot.snapshot_id)/analysis" @{
    binding_id = $binding.binding_id; request_id = [guid]::NewGuid().ToString()
    mode = 'live'; provider_name = 'deepseek'; source_ref_ids = $sourceIds
    approved_payload_hash = $preview.approval_hash; approved = $true
}
$analysis = $null
for ($attempt = 0; $attempt -lt 90; $attempt++) {
    Start-Sleep -Seconds 2
    $state = Invoke-GrowthApi 'Get' '/api/growth/local' $null
    $analysis = @($state.analysis_runs | Where-Object analysis_id -eq $queued.analysis_id)[0]
    if ($analysis.status -in @('succeeded','failed')) { break }
}
if ($analysis.status -ne 'succeeded') { throw "S02 DeepSeek 分析失败：$($analysis.status)" }
$workspace = Invoke-GrowthApi 'Get' '/api/growth/local/knowledge' $null
$candidate = @($workspace.candidates | Where-Object {
    $_.source_commit_shas -contains $manifest.head_sha
})[0]
if (-not $candidate -or $candidate.title -notmatch '搜索|请求|竞态|覆盖|结果') {
    throw "S02 候选未对应真实竞态修复：$($candidate.title)"
}
$decision = Invoke-GrowthApi 'Post' "/api/growth/local/knowledge/candidates/$($candidate.candidate_id)/decision" @{
    request_id = [guid]::NewGuid().ToString(); action = 'confirm_new'; title = $candidate.title
}
$cardId = [string]$decision.card.card_id
if (-not $cardId) { throw 'S02 未创建正式知识卡' }
$cardPath = "/api/growth/local/knowledge/cards/$cardId"
$materialPreview = Invoke-GrowthApi 'Post' "$cardPath/materials/preview" @{
    binding_id = $binding.binding_id; provider_name = 'deepseek'
}
$detail = Invoke-GrowthApi 'Post' "$cardPath/materials" @{
    binding_id = $binding.binding_id; provider_name = 'deepseek'
    approved_payload_hash = $materialPreview.approval_hash; approved = $true
}
if (@($detail.questions).Count -ne 2 -or
    @($detail.questions | Where-Object kind -eq 'understanding').Count -ne 1 -or
    @($detail.questions | Where-Object kind -eq 'prediction').Count -ne 1) {
    throw 'S02 真实题包不是理解/预测各一道四选一'
}
foreach ($question in $detail.questions) {
    if (@($question.options).Count -ne 4) {
        throw "S02 $($question.kind) 题不是四选一"
    }
}
$detail = Invoke-GrowthApi 'Post' "$cardPath/flip" $null
$streamRequestId = [guid]::NewGuid().ToString()
$streamBody = @{
    request_id = $streamRequestId; provider_name = 'deepseek'
    message = '请分十个编号步骤详细解释搜索结果竞态里的请求时序、旧结果如何覆盖新结果、以及修复边界，先不要给题目选项。'
    stage = 'back'
}
$stoppedStream = Invoke-GrowthChatStream "$cardPath/chat/stream" $streamBody -StopAfterFirstDelta
Start-Sleep -Seconds 3
if (@((Invoke-GrowthApi 'Get' $cardPath $null).conversation).Count -ne 0) {
    throw '中止的流式回答被误记为完整会话'
}
$retriedStream = Invoke-GrowthChatStream "$cardPath/chat/stream" $streamBody
if (@($retriedStream.conversation).Count -ne 1) {
    throw '中止后同 ID 重试未形成唯一完整会话'
}
$question = @($detail.questions | Where-Object kind -eq 'understanding')[0]
$drafts = @{}
$drafts[[string]$question.question_id] = 'A'
$detail = Invoke-GrowthApi 'Put' "$cardPath/learning" @{
    stage = 'understanding'; option_drafts = $drafts
    note_text = '我原以为较早请求会先返回；现在知道编号只限制界面更新，并不取消请求。'
}
$detail = Invoke-GrowthApi 'Put' "$cardPath/mark" @{
    mark = 'revisit'; reason = '需要再次核对旧响应与最新请求的区别'
}
if ($detail.learning.stage -ne 'understanding' -or
    $detail.learning.option_drafts.([string]$question.question_id) -ne 'A' -or
    $null -ne $detail.learning.answers.PSObject.Properties.Name -or
    $detail.card.followup_mark -ne 'revisit' -or
    @($detail.conversation).Count -ne 1) {
    throw 'S02 暂停前未保留未提交选项及学习状态'
}
$afterDecision = Invoke-GrowthApi 'Get' '/api/growth/local/knowledge' $null
$article = @($afterDecision.wiki_articles | Where-Object concept_id -eq $decision.card.concept_id)
if ($article.Count -ne 0) { throw 'S02 候选确认时过早写入 Wiki' }
[ordered]@{
    case_id = 'S02'; stage = 'paused_before_understanding_answer'
    project_id = $binding.project_id; binding_id = $binding.binding_id
    feature_id = $feature.feature_id; snapshot_id = $snapshot.snapshot_id
    analysis_id = $analysis.analysis_id; candidate_id = $candidate.candidate_id
    card_id = $cardId; concept_id = $decision.card.concept_id
    question_id = $question.question_id; question_version = $question.version
    selected_draft = 'A'; head_sha = $manifest.head_sha
    stream_request_id = $streamRequestId
    stopped_stream_delta_count = $stoppedStream.delta_count
    stopped_stream_partial_chars = $stoppedStream.partial_chars
    retried_stream_delta_count = $retriedStream.delta_count
    retried_conversation_count = @($retriedStream.conversation).Count
    followup_mark = $detail.card.followup_mark
} | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath (Join-Path $run 'model-paused-result.json') -Encoding UTF8
Write-Output "S02_REAL_MODEL_PAUSED $cardId"
