param([Parameter(Mandatory = $true)][string]$RunDirectory)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$run = [System.IO.Path]::GetFullPath($RunDirectory)
$repo = Join-Path $run 'fixture/search-demo'
if (Test-Path -LiteralPath $repo) {
    throw "S02 验收仓库已存在，必须使用新的 run 目录：$repo"
}
New-Item -ItemType Directory -Path (Join-Path $repo 'src') -Force | Out-Null

@'
{"name":"knowledge-first-s02-fixture","private":true,"type":"module"}
'@ | Set-Content -LiteralPath (Join-Path $repo 'package.json') -Encoding UTF8
@'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "rootDir": "src",
    "outDir": "dist",
    "lib": ["ES2022"],
    "types": []
  },
  "include": ["src/**/*.ts"]
}
'@ | Set-Content -LiteralPath (Join-Path $repo 'tsconfig.json') -Encoding UTF8
@'
dist/
'@ | Set-Content -LiteralPath (Join-Path $repo '.gitignore') -Encoding UTF8
@'
*.ts text eol=lf
*.json text eol=lf
*.mjs text eol=lf
'@ | Set-Content -LiteralPath (Join-Path $repo '.gitattributes') -Encoding UTF8

$source = Join-Path $repo 'src/search.ts'
@'
export type FetchResults = (query: string) => Promise<string[]>;

export function createSearch(
  fetchResults: FetchResults,
  render: (rows: string[]) => void,
) {
  return async (query: string): Promise<void> => {
    const rows = await fetchResults(query);
    render(rows);
  };
}
'@ | Set-Content -LiteralPath $source -Encoding UTF8

Push-Location -LiteralPath $repo
try {
    git init -b main | Out-Null
    git config user.name '成长伴侣验收'
    git config user.email 'growth-fixture@example.test'
    git add -- '.gitattributes' '.gitignore' 'package.json' 'tsconfig.json' 'src/search.ts'
    git commit -m '建立搜索竞态模拟基线' | Out-Null
    $base = (git rev-parse HEAD).Trim()
    git switch -c 'fix/search-response-order' | Out-Null
    @'
export type FetchResults = (query: string) => Promise<string[]>;

export function createSearch(
  fetchResults: FetchResults,
  render: (rows: string[]) => void,
) {
  let latestRequest = 0;
  return async (query: string): Promise<void> => {
    const requestId = ++latestRequest;
    const rows = await fetchResults(query);
    if (requestId === latestRequest) {
      render(rows);
    }
  };
}
'@ | Set-Content -LiteralPath $source -Encoding UTF8
    git add -- 'src/search.ts'
    git commit -m '避免较早请求覆盖最新搜索结果' | Out-Null
    $head = (git rev-parse HEAD).Trim()
} finally {
    Pop-Location
}

@'
import { pathToFileURL } from 'node:url';

const { createSearch } = await import(pathToFileURL(process.argv[2]).href);
const pending = new Map();
const rendered = [];
const search = createSearch(
  (query) => new Promise((resolve) => pending.set(query, resolve)),
  (rows) => rendered.push(...rows),
);
const older = search('a');
const latest = search('ab');
pending.get('ab')(['ab-result']);
await latest;
pending.get('a')(['a-result']);
await older;
process.stdout.write(JSON.stringify({ rendered, both_completed: true }));
'@ | Set-Content -LiteralPath (Join-Path $run 'search-probe.mjs') -Encoding UTF8

[ordered]@{
    case_id = 'S02'
    repo = $repo
    branch = 'fix/search-response-order'
    base_sha = $base
    head_sha = $head
    source_path = 'src/search.ts'
    probe_path = (Join-Path $run 'search-probe.mjs')
    expected_base_render = @('ab-result', 'a-result')
    expected_head_render = @('ab-result')
} | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $run 'fixture-manifest.json') -Encoding UTF8
Write-Output (Join-Path $run 'fixture-manifest.json')
