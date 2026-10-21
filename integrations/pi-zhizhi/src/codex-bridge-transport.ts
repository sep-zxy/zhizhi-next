/** Fixed packaged Python, inherited private pipe only. No URL, shell, fallback or retry. */
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { realpathSync } from 'node:fs';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { BridgeError, MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, fail, parseResponse, parseStrictJson, type BridgeRequest, type BridgeResponse, type BridgeTransport } from './codex-bridge-protocol.ts';

export function packagedPython(env: NodeJS.ProcessEnv = process.env): { executable: string; root: string; args: string[]; env: NodeJS.ProcessEnv } {
  const root = env.ZHIZHI_PORTABLE_ROOT; const python = env.ZHIZHI_PI_PYTHON;
  if (!root || !python || !isAbsolute(root) || !isAbsolute(python) || /^(?:\\\\|\/\/)/u.test(root)) fail('BRIDGE_PACKAGED_RUNTIME_REQUIRED');
  const canonicalRoot = realpathSync(root); const expected = join(canonicalRoot, 'runtime', 'python', 'python.exe');
  const same = (left: string, right: string) => process.platform === 'win32' ? left.toLowerCase() === right.toLowerCase() : left === right;
  if (!same(resolve(root), canonicalRoot) || !same(resolve(python), expected) || !same(realpathSync(expected), expected)) fail('BRIDGE_PACKAGED_RUNTIME_REQUIRED');
  const safe: NodeJS.ProcessEnv = {};
  for (const key of ['PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR']) if (env[key]) safe[key] = env[key];
  for (const key of ['ZHIZHI_PORTABLE_STATE', 'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME']) {
    const value = env[key]; if (!value || !isAbsolute(value)) fail('BRIDGE_PORTABLE_PATH_REQUIRED');
    const path = realpathSync(value); const tail = relative(canonicalRoot, path);
    if (tail === '..' || tail.startsWith('..' + sep) || isAbsolute(tail)) fail('BRIDGE_PORTABLE_PATH_REQUIRED');
    safe[key] = path;
  }
  if (safe.TEMP !== safe.TMP || safe.TEMP !== safe.TMPDIR) fail('BRIDGE_PORTABLE_PATH_REQUIRED');
  safe.ZHIZHI_PORTABLE_ROOT = canonicalRoot; safe.PYTHONUTF8 = '1'; safe.PYTHONDONTWRITEBYTECODE = '1'; safe.PYTHONNOUSERSITE = '1';
  return { executable: expected, root: canonicalRoot, args: ['-B', '-s', '-X', 'utf8', '-m', 'ahadiff.knowledge_platform.model.pi_bridge', '--owner-acceptance'], env: safe };
}
type Pending = { finish: (value: BridgeResponse | BridgeError) => void; cancel: (code: string) => void };
export class JsonlBridgeTransport implements BridgeTransport {
  private pending = new Map<string, Pending>(); private cancelIds = new Set<string>(); private input = Buffer.alloc(0);
  private stopped = false; private closed?: Promise<void>; private stderrBytes = 0;
  constructor(private readonly child: ChildProcessWithoutNullStreams, private readonly graceMs = 5000) {
    child.stdout.on('data', (chunk: Buffer) => { try { this.receive(chunk); } catch { this.stop('BRIDGE_PROTOCOL_INVALID'); } });
    child.stderr.on('data', (chunk: Buffer) => { this.stderrBytes += chunk.length; if (this.stderrBytes > 65536) this.stop('BRIDGE_DIAGNOSTIC_TOO_LARGE'); });
    child.on('error', () => this.stop('BRIDGE_PROCESS_UNAVAILABLE'));
    child.on('exit', () => this.stop('BRIDGE_PROCESS_EXITED'));
    child.stdin.on('error', () => this.stop('BRIDGE_PIPE_CLOSED'));
  }
  private receive(chunk: Buffer): void {
    this.input = Buffer.concat([this.input, chunk]);
    for (;;) {
      const end = this.input.indexOf(10);
      if (end < 0) { if (this.input.length > MAX_OUTPUT_BYTES) fail(); return; }
      if (end > MAX_OUTPUT_BYTES) fail();
      const bytes = this.input.subarray(0, end); this.input = this.input.subarray(end + 1);
      const line = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      const response = parseResponse(parseStrictJson(line));
      if (response.id === null) { this.stop('BRIDGE_NOT_READY'); return; }
      if (this.cancelIds.delete(response.id)) { if (!['cancel_requested', 'blocked'].includes(response.status)) fail(); continue; }
      const target = this.pending.get(response.id); if (!target) fail(); target.finish(response);
    }
  }
  private write(request: BridgeRequest): void {
    const line = JSON.stringify(request) + '\n'; if (Buffer.byteLength(line) > MAX_INPUT_BYTES) fail('BRIDGE_INPUT_TOO_LARGE');
    if (this.stopped || !this.child.stdin.writable) fail('BRIDGE_PIPE_CLOSED'); this.child.stdin.write(line);
  }
  request(request: BridgeRequest, signal?: AbortSignal, timeoutMs = 10000): Promise<BridgeResponse> {
    if (signal?.aborted) return Promise.reject(new BridgeError('BRIDGE_CANCELLED'));
    if (this.stopped || this.pending.size || request.type === 'cancel') return Promise.reject(new BridgeError('BRIDGE_BUSY_OR_CLOSED'));
    return new Promise((resolveResponse, reject) => {
      let cancellation: string | undefined; let grace: NodeJS.Timeout | undefined;
      const timeout = setTimeout(() => cancel('BRIDGE_TIMEOUT'), Math.min(timeoutMs, 180000));
      const abort = () => cancel('BRIDGE_CANCELLED');
      const finish = (value: BridgeResponse | BridgeError) => {
        clearTimeout(timeout); if (grace) clearTimeout(grace); signal?.removeEventListener('abort', abort); this.pending.delete(request.id);
        if (cancellation) reject(new BridgeError(cancellation)); else if (value instanceof BridgeError) reject(value); else resolveResponse(value);
      };
      const cancel = (code: string) => {
        if (cancellation) return; cancellation = code;
        try { const id = randomUUID(); this.cancelIds.add(id); this.write({ version: 1, id, type: 'cancel', request_id: request.id }); }
        catch { finish(new BridgeError(code)); void this.close(); return; }
        grace = setTimeout(() => { finish(new BridgeError(code)); void this.close(); }, this.graceMs);
      };
      this.pending.set(request.id, { finish, cancel }); signal?.addEventListener('abort', abort, { once: true });
      try { this.write(request); } catch { finish(new BridgeError('BRIDGE_REQUEST_REJECTED')); }
    });
  }
  private stop(code: string): void {
    if (this.stopped) return; this.stopped = true;
    for (const value of [...this.pending.values()]) value.finish(new BridgeError(code));
    void this.close();
  }
  close(): Promise<void> {
    if (this.closed) return this.closed;
    let resolveClosed!: () => void;
    this.closed = new Promise<void>(done => { resolveClosed = done; });
    {
      for (const value of this.pending.values()) value.cancel('BRIDGE_CANCELLED');
      this.stopped = true; this.child.stdin.end();
      if (this.child.exitCode !== null || this.child.signalCode !== null) { resolveClosed(); return this.closed; }
      const term = setTimeout(() => this.child.kill('SIGTERM'), this.graceMs);
      const kill = setTimeout(() => this.child.kill('SIGKILL'), this.graceMs + 2000);
      const final = setTimeout(() => { cleanup(); resolveClosed(); }, this.graceMs + 3000);
      const cleanup = () => { clearTimeout(term); clearTimeout(kill); clearTimeout(final); };
      this.child.once('exit', () => { cleanup(); resolveClosed(); });
    }
    return this.closed;
  }
}
export function spawnPackagedBridge(): BridgeTransport {
  const config = packagedPython();
  return new JsonlBridgeTransport(spawn(config.executable, config.args, { cwd: config.root, env: config.env, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'] }));
}
