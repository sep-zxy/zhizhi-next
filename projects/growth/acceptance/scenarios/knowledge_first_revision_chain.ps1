param(
    [Parameter(Mandatory = $true)][string]$RunDirectory,
    [string]$BaseUrl = 'http://127.0.0.1:8772'
)

$ErrorActionPreference = 'Stop'
$manifest = Get-Content -LiteralPath (Join-Path $RunDirectory 'fixture-manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$results = @(Get-Content -LiteralPath (Join-Path $RunDirectory 'full-chain-result.json') -Encoding UTF8 -Raw | ConvertFrom-Json)
$original = @($results | Where-Object kind -eq 'cache-expiry')[0]
if (-not $original) { throw 'S03 缓存知识链路未完成' }
$headers = @{ Origin = $BaseUrl }
$headers['X-AhaDiff-Token'] = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/auth/token" -Headers $headers).token

function Invoke-GrowthApi {
    param([string]$Method, [string]$Path, [object]$Body)
    $arguments = @{ Method = $Method; Uri = "$BaseUrl$Path"; Headers = $headers }
    if ($null -ne $Body) {
        $arguments['ContentType'] = 'application/json'
        $arguments['Body'] = $Body | ConvertTo-Json -Depth 12 -Compress
    }
    try { return Invoke-RestMethod @arguments }
    catch { Write-Error "$Method $Path failed: $($_.ErrorDetails.Message)"; throw }
}

$state = Invoke-GrowthApi 'Get' '/api/growth/local' $null
$binding = @($state.bindings | Where-Object canonical_local_path -eq $manifest.repo)[0]
$feature = @($state.features | Where-Object project_id -eq $binding.project_id)[0]
if (-not $binding -or -not $feature) { throw 'S03 仓库绑定不存在' }

Push-Location -LiteralPath $manifest.repo
try {
    @'
export class PriceCache {
  constructor(load, clock, ttlMs, staleIfError = false) {
    this.load = load;
    this.clock = clock;
    this.ttlMs = ttlMs;
    this.staleIfError = staleIfError;
    this.values = new Map();
  }
  get(key) {
    const now = this.clock();
    const entry = this.values.get(key);
    if (entry && now < entry.expiresAt) return entry.value;
    try {
      const value = this.load(key);
      this.values.set(key, { value, expiresAt: now + this.ttlMs });
      return value;
    } catch (error) {
      if (this.staleIfError && entry) return entry.value;
      throw error;
    }
  }
}
'@ | Set-Content -LiteralPath 'src/cache.mjs' -Encoding UTF8
    rtk git add 'src/cache.mjs'
    if (git diff --cached --name-only -- 'src/cache.mjs') {
        rtk git commit -m '补充缓存回源失败时的过期值边界' | Out-Null
    }
    $newCommit = (rtk git rev-parse HEAD).Trim()
} finally { Pop-Location }

Invoke-GrowthApi 'Get' "/api/growth/local/bindings/$($binding.binding_id)/commits?scan=true" $null | Out-Null
$snapshot = Invoke-GrowthApi 'Post' "/api/growth/local/features/$($feature.feature_id)/snapshots" @{
    binding_id = $binding.binding_id; commit_sha = $newCommit
}
$sources = Invoke-GrowthApi 'Get' "/api/growth/local/snapshots/$($snapshot.snapshot_id)/model-sources?binding_id=$($binding.binding_id)" $null
$sourceIds = @($sources.sources | Where-Object { -not $_.deleted } | Select-Object -First 8 -ExpandProperty source_ref_id)
if ($sourceIds.Count -eq 0) { throw 'S03 修订提交缺少源码快照' }
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
if ($analysis.status -ne 'succeeded') { throw "S03 修订分析失败: $($analysis.status)" }
$workspace = Invoke-GrowthApi 'Get' '/api/growth/local/knowledge' $null
$candidate = @($workspace.candidates | Where-Object { $_.source_commit_shas -contains $newCommit })[0]
if (-not $candidate) { throw 'S03 新提交未形成待确认候选' }
$decision = Invoke-GrowthApi 'Post' "/api/growth/local/knowledge/candidates/$($candidate.candidate_id)/decision" @{
    request_id = [guid]::NewGuid().ToString(); action = 'merge'; card_id = $original.card_id
}
if ($decision.card.card_id -ne $original.card_id -or $decision.card.concept_id -ne $original.concept_id) {
    throw 'S03 归并未保持原卡与概念身份'
}
$cardPath = "/api/growth/local/knowledge/cards/$($original.card_id)"
$before = Invoke-GrowthApi 'Get' $cardPath $null
if ($before.learning.stage -ne 'completed') { throw 'S03 归并重复证据不应自动重学' }
$revised = Invoke-GrowthApi 'Post' "$cardPath/revision" @{
    request_id = [guid]::NewGuid().ToString(); expected_version = $before.card.card_version
    reason = '新增过期后回源失败时可选择返回旧值的边界，需要更新机制和客观题。'
}
if ($revised.card.card_version -ne ($before.card.card_version + 1) -or
    -not $revised.card.revision_pending -or $revised.learning.stage -ne 'front' -or
    $revised.learning_history.Count -ne 1 -or
    $revised.learning_history[0].stage -ne 'completed') {
    throw 'S03 新版卡未保留已完成的旧版学习轨迹'
}
$materialPreview = Invoke-GrowthApi 'Post' "$cardPath/materials/preview" @{
    binding_id = $binding.binding_id; provider_name = 'deepseek'
}
$detail = Invoke-GrowthApi 'Post' "$cardPath/materials" @{
    binding_id = $binding.binding_id; provider_name = 'deepseek'
    approved_payload_hash = $materialPreview.approval_hash; approved = $true
}
if ($detail.questions.Count -ne 2 -or $detail.card.card_version -ne 2) {
    throw 'S03 新版题包未生成'
}
$detail = Invoke-GrowthApi 'Post' "$cardPath/flip" $null
foreach ($kind in @('understanding','prediction')) {
    $question = @($detail.questions | Where-Object kind -eq $kind)[0]
    $answer = Invoke-GrowthApi 'Post' "$cardPath/answers" @{
        request_id = [guid]::NewGuid().ToString(); question_id = $question.question_id
        version = $question.version; option_id = 'A'
    }
    $answered = @($answer.questions | Where-Object kind -eq $kind)[0]
    if (-not $answer.answer.correct_option_id -or
        @($answered.explanations.PSObject.Properties.Name).Count -ne 4) {
        throw "S03 新版 $kind 题缺少客观判定或逐项解析"
    }
    $detail = Invoke-GrowthApi 'Post' "$cardPath/explanations/$($question.question_id)/confirm" @{
        request_id = [guid]::NewGuid().ToString()
    }
}
$detail = Invoke-GrowthApi 'Post' "$cardPath/complete" $null
if ($detail.learning.stage -ne 'completed' -or $detail.learning_history.Count -ne 1) {
    throw 'S03 修订学习未完成或历史丢失'
}
$handEdit = "`n用户在 Obsidian 中手写：该缓存适合故障时可容忍旧值的报价场景。`n"
Add-Content -LiteralPath $original.wiki -Value $handEdit -Encoding UTF8
$vaultSettings = Invoke-GrowthApi 'Put' '/api/growth/local/knowledge/vault' @{
    path = (Join-Path $RunDirectory 'obsidian-vault')
    target_folder = '工程成长伴侣/后来改的目录'
}
if ($vaultSettings.target_folder -ne '工程成长伴侣/后来改的目录') {
    throw 'S03 新目标文件夹未保存'
}
$draft = Invoke-GrowthApi 'Post' "$cardPath/wiki/draft" @{
    request_id = [guid]::NewGuid().ToString()
}
if (-not $draft.markdown.Contains('用户在 Obsidian 中手写')) {
    throw 'S03 Wiki 更新草稿未包含外部手写内容'
}
$article = Invoke-GrowthApi 'Post' "/api/growth/local/knowledge/wiki/drafts/$($draft.draft_id)/publish" @{
    request_id = [guid]::NewGuid().ToString()
}
$updatedPath = Join-Path (Join-Path $RunDirectory 'obsidian-vault') $article.relative_path
$content = Get-Content -LiteralPath $updatedPath -Encoding UTF8 -Raw
if ($updatedPath -ne $original.wiki -or $article.version -ne 2 -or
    -not $content.Contains('用户在 Obsidian 中手写')) {
    throw 'S03 原 Wiki 条目未保留路径、版本或手写内容'
}
$report = [ordered]@{
    kind = 'cache-expiry-revision'; new_commit = $newCommit
    card_id = $original.card_id; concept_id = $original.concept_id
    card_version = $detail.card.card_version; prior_learning_count = $detail.learning_history.Count
    article_version = $article.version; wiki = $updatedPath
}
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $RunDirectory 'revision-result.json') -Encoding UTF8
Write-Output "S03 revised: $($original.card_id) -> version $($article.version) Wiki"
