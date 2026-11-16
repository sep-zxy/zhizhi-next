param(
    [Parameter(Mandatory = $true)][string]$RunDirectory,
    [string]$BaseUrl = 'http://127.0.0.1:8765'
)

$ErrorActionPreference = 'Stop'
$manifest = Get-Content -LiteralPath (Join-Path $RunDirectory 'fixture-manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$headers = @{ Origin = $BaseUrl }
$headers['X-AhaDiff-Token'] = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/auth/token" -Headers $headers).token

function Invoke-GrowthApi {
    param([string]$Method, [string]$Path, [object]$Body)
    $arguments = @{ Method = $Method; Uri = "$BaseUrl$Path"; Headers = $headers }
    if ($null -ne $Body) {
        $arguments['ContentType'] = 'application/json'
        $arguments['Body'] = $Body | ConvertTo-Json -Depth 12 -Compress
    }
    try {
        return Invoke-RestMethod @arguments
    } catch {
        Write-Error "$Method $Path failed: $($_.ErrorDetails.Message)"
        throw
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
    $text = ''
    $completed = $null
    $eventName = ''
    try {
        $response = $http.Send($request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead)
        if (-not $response.IsSuccessStatusCode) {
            throw "流式聊天 HTTP $([int]$response.StatusCode): $($response.Content.ReadAsStringAsync().Result)"
        }
        $reader = [System.IO.StreamReader]::new($response.Content.ReadAsStream(), [System.Text.Encoding]::UTF8)
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line.StartsWith('event:')) {
                $eventName = $line.Substring(6).Trim()
                continue
            }
            if (-not $line.StartsWith('data:')) { continue }
            $payload = $line.Substring(5).TrimStart() | ConvertFrom-Json -Depth 30
            if ($eventName -eq 'error') { throw "流式聊天失败: $payload" }
            if ($eventName -eq 'delta') {
                if ($payload -isnot [string]) { throw '流式聊天增量格式错误' }
                $text += $payload
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
        if ($deltaCount -lt 1 -or -not $text.Trim() -or $completed) {
            throw '停止前未收到真实流式回答片段'
        }
        return @{ delta_count = $deltaCount; partial_reply = $text }
    }
    if (-not $completed -or $deltaCount -lt 1 -or $text -ne $completed.reply) {
        throw "流式聊天未完整返回：delta=$deltaCount done=$([bool]$completed)"
    }
    return @{ delta_count = $deltaCount; reply = $completed.reply; conversation = $completed.conversation }
}

$state = Invoke-GrowthApi 'Get' '/api/growth/local' $null
$binding = @($state.bindings | Where-Object canonical_local_path -eq $manifest.repo)[0]
if (-not $binding) {
    Invoke-GrowthApi 'Post' '/api/growth/local/import' @{
        name = '四类提交知识链路'; path = $manifest.repo; model_allowed = $true
    } | Out-Null
    $state = Invoke-GrowthApi 'Get' '/api/growth/local' $null
    $binding = @($state.bindings | Where-Object canonical_local_path -eq $manifest.repo)[0]
}
$feature = @($state.features | Where-Object project_id -eq $binding.project_id)[0]
if (-not $binding -or -not $feature) { throw '验收仓库导入失败' }
$vault = Join-Path $RunDirectory 'obsidian-vault'
New-Item -ItemType Directory -Path (Join-Path $vault '.obsidian') -Force | Out-Null
$targetFolder = '工程成长伴侣/完整链路验收'
$vaultSettings = Invoke-GrowthApi 'Put' '/api/growth/local/knowledge/vault' @{
    path = $vault; target_folder = $targetFolder
}
if ($vaultSettings.target_folder -ne $targetFolder) { throw 'Wiki 目标文件夹未保存' }
$results = @()
$conceptPatterns = @{
    concurrency = 'CompletableFuture|thenCombine|线程池|并行|异步路由'
    'request-race' = '搜索|请求|竞态|latestRequest'
    'cache-expiry' = '缓存|TTL|过期|PriceCache'
    authorization = '授权|认证|归属|越权|所有权|owner'
}

foreach ($group in $manifest.groups) {
    $exploreSessionId = $null
    $exploreTopicId = $null
    $alternateSessionId = $null
    $alternateTopicId = $null
    if ($group.kind -eq 'authorization') {
        $exploreSessionId = [guid]::NewGuid().ToString()
        $exploreMessages = @(
            '我对 JWT 身份认证和资源授权的边界感兴趣，想先理解登录身份与访问他人资源的区别。',
            '我想把认证与授权的边界作为长期成长主题；请给出一个可由我确认的主题建议。'
        )
        foreach ($message in $exploreMessages) {
            $exploreBody = @{
                session_id = $exploreSessionId; binding_id = $binding.binding_id
                provider_name = 'deepseek'; message = $message
            }
            $explorePreview = Invoke-GrowthApi 'Post' "/api/growth/local/projects/$($binding.project_id)/explore/preview" $exploreBody
            $exploreSend = $exploreBody.Clone()
            $exploreSend['approved_payload_hash'] = $explorePreview.approval_hash
            $exploreSend['approved'] = $true
            $exploreReply = Invoke-GrowthApi 'Post' "/api/growth/local/projects/$($binding.project_id)/explore" $exploreSend
            if ($exploreReply.suggestion) { break }
        }
        if (-not $exploreReply.suggestion) { throw 'S04 真实探讨未产生可确认的成长主题' }
        $topic = Invoke-GrowthApi 'Post' "/api/growth/local/projects/$($binding.project_id)/explore/$exploreSessionId/topic" @{}
        $exploreTopicId = [string]$topic.topic_id
        $alternateSessionId = [guid]::NewGuid().ToString()
        foreach ($message in @(
            '我还想单独研究资源所有权校验：document.ownerId 与当前 actor.id 如何决定 Bob 或 Alice 能否更新？请建议一个明确包含“所有权校验”的成长主题。',
            '请把资源所有权校验作为与认证授权总览不同的独立主题，给出可由我确认的主题建议。'
        )) {
            $otherBody = @{
                session_id = $alternateSessionId; binding_id = $binding.binding_id
                provider_name = 'deepseek'; message = $message
            }
            $otherPreview = Invoke-GrowthApi 'Post' "/api/growth/local/projects/$($binding.project_id)/explore/preview" $otherBody
            $otherSend = $otherBody.Clone()
            $otherSend['approved_payload_hash'] = $otherPreview.approval_hash
            $otherSend['approved'] = $true
            $otherReply = Invoke-GrowthApi 'Post' "/api/growth/local/projects/$($binding.project_id)/explore" $otherSend
            if ($otherReply.suggestion) { break }
        }
        if (-not $otherReply.suggestion) { throw 'S04 第二段真实探讨未产生可确认主题' }
        $otherTopic = Invoke-GrowthApi 'Post' "/api/growth/local/projects/$($binding.project_id)/explore/$alternateSessionId/topic" @{}
        $alternateTopicId = [string]$otherTopic.topic_id
        if ($alternateTopicId -eq $exploreTopicId) { throw 'S04 两段探讨合并为同一主题，无法验收显式选择' }
    }
    $mainCommit = [string]$group.commits[0]
    $proofCommit = [string]$group.commits[1]
    $snapshot = Invoke-GrowthApi 'Post' "/api/growth/local/features/$($feature.feature_id)/snapshots" @{
        binding_id = $binding.binding_id; commit_sha = $mainCommit
    }
    if ($snapshot.head_sha -ne $mainCommit) { throw "快照提交不符: $($group.kind)" }
    $sources = Invoke-GrowthApi 'Get' "/api/growth/local/snapshots/$($snapshot.snapshot_id)/model-sources?binding_id=$($binding.binding_id)" $null
    $sourceIds = @($sources.sources | Where-Object { -not $_.deleted } | Select-Object -First 8 -ExpandProperty source_ref_id)
    if ($sourceIds.Count -eq 0) { throw "未捕获源码: $($group.kind)" }
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
    if ($analysis.status -ne 'succeeded') { throw "模型分析未成功: $($group.kind) $($analysis.status)" }
    $workspace = Invoke-GrowthApi 'Get' '/api/growth/local/knowledge' $null
    $candidate = @($workspace.candidates | Where-Object { $_.source_commit_shas -contains $mainCommit })[0]
    if (-not $candidate) { throw "提交未形成知识候选: $($group.kind)" }
    if ($candidate.title -notmatch $conceptPatterns[$group.kind]) {
        throw "候选主题偏离真实补丁: $($group.kind) $($candidate.title)"
    }
    if ($exploreTopicId) {
        $linked = Invoke-GrowthApi 'Post' "/api/growth/local/knowledge/candidates/$($candidate.candidate_id)/discussion" @{
            session_id = $exploreSessionId
        }
        $linked = Invoke-GrowthApi 'Post' "/api/growth/local/knowledge/candidates/$($candidate.candidate_id)/discussion" @{
            session_id = $alternateSessionId
        }
        $linkedTopicIds = @($linked.discussions | Where-Object topic_id | Select-Object -ExpandProperty topic_id -Unique)
        if ($linkedTopicIds.Count -ne 2 -or $linkedTopicIds -notcontains $exploreTopicId -or
            $linkedTopicIds -notcontains $alternateTopicId) { throw 'S04 两个真实探讨主题未同时关联代码候选' }
        $withoutChoice = @{
            request_id = [guid]::NewGuid().ToString(); action = 'confirm_new'; title = $candidate.title
        } | ConvertTo-Json -Compress
        $missingChoice = Invoke-WebRequest -Method Post -Uri "$BaseUrl/api/growth/local/knowledge/candidates/$($candidate.candidate_id)/decision" -Headers $headers -ContentType 'application/json' -Body $withoutChoice -SkipHttpErrorCheck
        $stillPending = @((Invoke-GrowthApi 'Get' '/api/growth/local/knowledge' $null).candidates |
            Where-Object candidate_id -eq $candidate.candidate_id)
        if ($missingChoice.StatusCode -ne 409 -or $stillPending.Count -ne 1) {
            throw 'S04 多主题候选未强制用户明确选择'
        }
    }
    $decisionBody = @{
        request_id = [guid]::NewGuid().ToString(); action = 'confirm_new'; title = $candidate.title
    }
    if ($exploreTopicId) { $decisionBody['topic_id'] = $exploreTopicId }
    $decision = Invoke-GrowthApi 'Post' "/api/growth/local/knowledge/candidates/$($candidate.candidate_id)/decision" $decisionBody
    $cardId = [string]$decision.card.card_id
    if (-not $cardId) { throw "候选未形成正式卡: $($group.kind)" }
    if ($decision.card.title -notmatch $conceptPatterns[$group.kind]) {
        throw "正式卡主题偏离真实补丁: $($group.kind) $($decision.card.title)"
    }
    if ($exploreTopicId) {
        $afterDecision = Invoke-GrowthApi 'Get' '/api/growth/local' $null
        $createdTask = @($afterDecision.growth_tasks | Where-Object task_id -eq $cardId)[0]
        if ($createdTask.topic_id -ne $exploreTopicId) {
            throw 'S04 正式卡没有沿用先前确认的探讨主题'
        }
    }
    Invoke-GrowthApi 'Post' "/api/growth/local/cards/$cardId/commits" @{
        binding_id = $binding.binding_id; commit_shas = @($proofCommit)
    } | Out-Null
    $cardPath = "/api/growth/local/knowledge/cards/$cardId"
    $materialPreview = Invoke-GrowthApi 'Post' "$cardPath/materials/preview" @{
        binding_id = $binding.binding_id; provider_name = 'deepseek'
    }
    $detail = Invoke-GrowthApi 'Post' "$cardPath/materials" @{
        binding_id = $binding.binding_id; provider_name = 'deepseek'
        approved_payload_hash = $materialPreview.approval_hash; approved = $true
    }
    if ($detail.questions.Count -ne 2) { throw "题包不足两题: $($group.kind)" }
    Invoke-GrowthApi 'Put' "$cardPath/note" @{ text = "验收笔记：$($group.kind) 的边界需要回到提交核查。" } | Out-Null
    $detail = Invoke-GrowthApi 'Post' "$cardPath/flip" $null
    $chatReply = $null
    $streamReply = $null
    $stoppedStream = $null
    $streamStopRequestId = $null
    $streamStopMessage = $null
    if ($group.kind -eq 'concurrency') {
        $chatReply = Invoke-GrowthApi 'Post' "$cardPath/chat" @{
            request_id = [guid]::NewGuid().ToString(); provider_name = 'deepseek'
            message = '请结合这张卡的代码证据解释线程池与两个任务的关系，暂不告诉我选项字母。'
            stage = 'back'
        }
        if (-not $chatReply.reply -or $chatReply.conversation.Count -ne 1) {
            throw '卡内上下文 AI 未留下可恢复的对话'
        }
        $streamReply = Invoke-GrowthChatStream "$cardPath/chat/stream" @{
            request_id = [guid]::NewGuid().ToString(); provider_name = 'deepseek'
            message = '继续解释线程池关闭时正在运行的两个任务会怎样，请逐步说明。'
            stage = 'back'
        }
        if (-not $streamReply.reply -or $streamReply.conversation.Count -ne 2) {
            throw '卡内 AI 真流式回复未写入可恢复对话'
        }
    }
    if ($group.kind -eq 'request-race') {
        $streamStopRequestId = [guid]::NewGuid().ToString()
        $streamStopMessage = '请分十个编号步骤详细解释搜索结果竞态里的请求时序、旧结果如何覆盖新结果、以及修复边界，先不要给题目选项。'
        $stoppedStream = Invoke-GrowthChatStream "$cardPath/chat/stream" @{
            request_id = $streamStopRequestId; provider_name = 'deepseek'
            message = $streamStopMessage
            stage = 'back'
        } -StopAfterFirstDelta
        Start-Sleep -Seconds 3
        if ((Invoke-GrowthApi 'Get' $cardPath $null).conversation.Count -ne 0) {
            throw '中止的流式回答被误记为完整会话'
        }
    }
    $checks = @()
    foreach ($kind in @('understanding','prediction')) {
        $question = @($detail.questions | Where-Object kind -eq $kind)[0]
        if (-not $question) { throw "缺少 $kind 题: $($group.kind)" }
        $selectedOption = 'A'
        if ($group.kind -eq 'concurrency' -and $kind -eq 'understanding') {
            $misconception = @($question.options | Where-Object {
                $_.text -match '每个.{0,12}(任务|supplyAsync).{0,20}新.{0,3}线程' -and
                $_.text -notmatch '(不是|并非|不会|不一定)每个'
            })[0]
            if (-not $misconception) { throw 'S01 理解题缺少每任务新建线程的误解选项' }
            $selectedOption = [string]$misconception.id
        }
        $answer = Invoke-GrowthApi 'Post' "$cardPath/answers" @{
            request_id = [guid]::NewGuid().ToString(); question_id = $question.question_id
            version = $question.version; option_id = $selectedOption
        }
        if (-not $answer.answer.correct_option_id) { throw "未客观判定: $($group.kind) $kind" }
        if ($group.kind -eq 'concurrency' -and $kind -eq 'understanding' -and
            $answer.answer.correct_option_id -eq $selectedOption) {
            throw 'S01 的首次误解选项没有被客观判错'
        }
        if ($group.kind -in @('concurrency','request-race') -and -not $answer.answer.assisted) {
            throw '流式提示后的首次作答未记录辅助状态'
        }
        $answered = @($answer.questions | Where-Object kind -eq $kind)[0]
        if (@($answered.explanations.PSObject.Properties.Name).Count -ne 4) {
            throw "逐项解析不完整: $($group.kind) $kind"
        }
        $detail = Invoke-GrowthApi 'Post' "$cardPath/explanations/$($question.question_id)/confirm" @{
            request_id = [guid]::NewGuid().ToString()
        }
        if (-not $detail.learning.answers.$kind.explanation_confirmed) {
            throw "解析未确认: $($group.kind) $kind"
        }
        $checks += [ordered]@{ kind = $kind; selected = $selectedOption; correct = $answer.answer.correct_option_id }
    }
    $detail = Invoke-GrowthApi 'Post' "$cardPath/complete" $null
    if ($detail.learning.stage -ne 'completed') { throw "学习未完成: $($group.kind)" }
    if ($group.kind -eq 'concurrency') {
        $detail = Invoke-GrowthApi 'Put' "$cardPath/mark" @{
            mark = 'confused'; reason = '需要主动回看线程池生命周期'
        }
        if ($detail.card.followup_mark -ne 'confused') {
            throw '手动回看标记未保存'
        }
    }
    $draft = Invoke-GrowthApi 'Post' "$cardPath/wiki/draft" @{
        request_id = [guid]::NewGuid().ToString()
    }
    if ($draft.status -ne 'draft') { throw "Wiki 草稿未生成: $($group.kind)" }
    $edited = $draft.markdown + "`n验收人工补充：$($group.kind) 已核对。`n"
    $draft = Invoke-GrowthApi 'Put' "/api/growth/local/knowledge/wiki/drafts/$($draft.draft_id)" @{
        markdown = $edited
    }
    $article = Invoke-GrowthApi 'Post' "/api/growth/local/knowledge/wiki/drafts/$($draft.draft_id)/publish" @{
        request_id = [guid]::NewGuid().ToString()
    }
    $target = Join-Path $vault $article.relative_path
    if (-not $article.relative_path.StartsWith("$targetFolder/", [StringComparison]::Ordinal)) {
        throw "Wiki 未写入所选目标文件夹：$($article.relative_path)"
    }
    if (-not (Test-Path -LiteralPath $target)) { throw "Wiki 未写入: $($group.kind)" }
    $content = Get-Content -LiteralPath $target -Encoding UTF8 -Raw
    if (-not $content.Contains("验收人工补充：$($group.kind) 已核对。")) {
        throw "人工编辑未保存: $($group.kind)"
    }
    if ($group.kind -eq 'request-race') {
        $streamReply = Invoke-GrowthChatStream "$cardPath/chat/stream" @{
            request_id = $streamStopRequestId; provider_name = 'deepseek'
            message = $streamStopMessage; stage = 'back'
        }
        if ($streamReply.conversation.Count -ne 1) {
            throw '中止后同 ID 重试未形成唯一完整会话'
        }
    }
    $results += [ordered]@{
        kind = $group.kind; commits = @($mainCommit,$proofCommit)
        snapshot_id = $snapshot.snapshot_id; analysis_id = $analysis.analysis_id
        card_id = $cardId; concept_id = $decision.card.concept_id
        checks = $checks; ai_chat = [bool]$chatReply
        ai_stream = [bool]$streamReply
        stream_delta_count = $(if ($streamReply) { $streamReply.delta_count } else { 0 })
        stopped_partial_chars = $(if ($stoppedStream) { $stoppedStream.partial_reply.Length } else { 0 })
        stopped_then_retried = [bool]$stoppedStream
        exploration_session_id = $exploreSessionId
        exploration_topic_id = $exploreTopicId
        alternate_exploration_session_id = $alternateSessionId
        alternate_exploration_topic_id = $alternateTopicId
        wiki = $target
    }
    Write-Output "$($group.kind): $cardId -> $target"
}

$report = Join-Path $RunDirectory 'full-chain-result.json'
$results | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $report -Encoding UTF8
Write-Output "report=$report"
& (Join-Path $PSScriptRoot 'knowledge_first_revision_chain.ps1') -RunDirectory $RunDirectory -BaseUrl $BaseUrl
