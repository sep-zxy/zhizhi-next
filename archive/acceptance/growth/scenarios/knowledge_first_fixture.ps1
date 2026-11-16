param([Parameter(Mandatory = $true)][string]$RunDirectory)

$ErrorActionPreference = 'Stop'
$repo = Join-Path $RunDirectory 'fixture/repo'
New-Item -ItemType Directory -Path (Join-Path $repo 'src') -Force | Out-Null
Push-Location -LiteralPath $repo
try {
    rtk git init -b main | Out-Null
    rtk git config user.name '成长伴侣验收'
    rtk git config user.email 'growth-fixture@example.test'
    @'
import java.util.List;

public class RouteService {
    public static String routes() {
        return String.join("", List.of("A", "B"));
    }
}
'@ | Set-Content -LiteralPath 'src/RouteService.java' -Encoding UTF8
    @'
export async function search(query, transport, render) {
  const result = await transport(query);
  render(result);
}
'@ | Set-Content -LiteralPath 'src/search.mjs' -Encoding UTF8
    @'
export class PriceCache {
  constructor(load) { this.load = load; }
  get(key) { return this.load(key); }
}
'@ | Set-Content -LiteralPath 'src/cache.mjs' -Encoding UTF8
    @'
export function updateDocument(actor, document) {
  if (!actor) return 401;
  return 200;
}
'@ | Set-Content -LiteralPath 'src/auth.mjs' -Encoding UTF8
    rtk git add -- 'src'
    rtk git commit -m '建立四类工程问题的基线实现' | Out-Null
    $initial = (rtk git rev-parse HEAD).Trim()
    rtk git switch -c 'feature/knowledge-lab' | Out-Null
    $groups = @()

    $base = (rtk git rev-parse HEAD).Trim()
    @'
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class RouteService {
    public static String routes() {
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            CompletableFuture<String> first = CompletableFuture.supplyAsync(() -> "A", executor);
            CompletableFuture<String> second = CompletableFuture.supplyAsync(() -> "B", executor);
            return first.thenCombine(second, String::concat).join();
        } finally {
            executor.shutdown();
        }
    }
}
'@ | Set-Content -LiteralPath 'src/RouteService.java' -Encoding UTF8
    rtk git add -- 'src/RouteService.java'
    rtk git commit -m '并行查询复用显式线程池' | Out-Null
    $c1 = (rtk git rev-parse HEAD).Trim()
    @'
public class RouteProbe {
    public static void main(String[] args) {
        if (!"AB".equals(RouteService.routes())) throw new AssertionError("并行结果顺序错误");
        System.out.println(RouteService.routes());
    }
}
'@ | Set-Content -LiteralPath 'src/RouteProbe.java' -Encoding UTF8
    rtk git add -- 'src/RouteProbe.java'
    rtk git commit -m '验证并行结果与线程池关闭边界' | Out-Null
    $c2 = (rtk git rev-parse HEAD).Trim()
    $groups += [ordered]@{ kind='concurrency'; base=$base; commits=@($c1,$c2) }

    $base = (rtk git rev-parse HEAD).Trim()
    @'
export function createSearch(transport, render) {
  let latestRequest = 0;
  return async function search(query) {
    const requestId = ++latestRequest;
    const result = await transport(query);
    if (requestId !== latestRequest) return false;
    render(result);
    return true;
  };
}
'@ | Set-Content -LiteralPath 'src/search.mjs' -Encoding UTF8
    rtk git add -- 'src/search.mjs'
    rtk git commit -m '阻止旧搜索响应覆盖新结果' | Out-Null
    $c1 = (rtk git rev-parse HEAD).Trim()
    @'
import { createSearch } from './search.mjs';
const pending = [];
const shown = [];
const search = createSearch(() => new Promise(resolve => pending.push(resolve)), value => shown.push(value));
const old = search('old');
const current = search('new');
pending[1]('new');
await current;
pending[0]('old');
await old;
if (shown.join(',') !== 'new') throw new Error('旧响应覆盖了新结果');
console.log(shown.join(','));
'@ | Set-Content -LiteralPath 'src/search-probe.mjs' -Encoding UTF8
    rtk git add -- 'src/search-probe.mjs'
    rtk git commit -m '复现乱序响应并验证最新请求保护' | Out-Null
    $c2 = (rtk git rev-parse HEAD).Trim()
    $groups += [ordered]@{ kind='request-race'; base=$base; commits=@($c1,$c2) }

    $base = (rtk git rev-parse HEAD).Trim()
    @'
export class PriceCache {
  constructor(load, clock, ttlMs) {
    this.load = load;
    this.clock = clock;
    this.ttlMs = ttlMs;
    this.values = new Map();
  }
  get(key) {
    const now = this.clock();
    const entry = this.values.get(key);
    if (entry && now < entry.expiresAt) return entry.value;
    const value = this.load(key);
    this.values.set(key, { value, expiresAt: now + this.ttlMs });
    return value;
  }
}
'@ | Set-Content -LiteralPath 'src/cache.mjs' -Encoding UTF8
    rtk git add -- 'src/cache.mjs'
    rtk git commit -m '增加访问时检查 TTL 的价格缓存' | Out-Null
    $c1 = (rtk git rev-parse HEAD).Trim()
    @'
import { PriceCache } from './cache.mjs';
let now = 0;
let calls = 0;
const cache = new PriceCache(() => ++calls, () => now, 10);
if (cache.get('item') !== 1 || cache.get('item') !== 1) throw new Error('缓存命中失败');
now = 10;
if (cache.get('item') !== 2) throw new Error('TTL 边界未在访问时失效');
console.log(calls);
'@ | Set-Content -LiteralPath 'src/cache-probe.mjs' -Encoding UTF8
    rtk git add -- 'src/cache-probe.mjs'
    rtk git commit -m '验证 TTL 边界与惰性失效' | Out-Null
    $c2 = (rtk git rev-parse HEAD).Trim()
    $groups += [ordered]@{ kind='cache-expiry'; base=$base; commits=@($c1,$c2) }

    $base = (rtk git rev-parse HEAD).Trim()
    @'
export function updateDocument(actor, document) {
  if (!actor) return 401;
  if (document.ownerId !== actor.id) return 403;
  return 200;
}
'@ | Set-Content -LiteralPath 'src/auth.mjs' -Encoding UTF8
    rtk git add -- 'src/auth.mjs'
    rtk git commit -m '认证后校验文档资源归属' | Out-Null
    $c1 = (rtk git rev-parse HEAD).Trim()
    @'
import { updateDocument } from './auth.mjs';
const alice = { id: 'Alice' };
const bob = { id: 'Bob' };
const document = { ownerId: 'Alice' };
if (updateDocument(bob, document) !== 403) throw new Error('越权请求被放行');
if (updateDocument(alice, document) !== 200) throw new Error('所有者被拒绝');
console.log('403,200');
'@ | Set-Content -LiteralPath 'src/auth-probe.mjs' -Encoding UTF8
    rtk git add -- 'src/auth-probe.mjs'
    rtk git commit -m '验证 Bob 越权与 Alice 正常访问' | Out-Null
    $c2 = (rtk git rev-parse HEAD).Trim()
    $groups += [ordered]@{ kind='authorization'; base=$base; commits=@($c1,$c2) }

    [ordered]@{ repo=$repo; initial=$initial; branch='feature/knowledge-lab'; groups=$groups } |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $RunDirectory 'fixture-manifest.json') -Encoding UTF8
    Write-Output (Join-Path $RunDirectory 'fixture-manifest.json')
} finally {
    Pop-Location
}
