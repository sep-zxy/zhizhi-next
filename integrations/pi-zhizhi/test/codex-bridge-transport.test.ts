import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { JsonlBridgeTransport, packagedPython } from '../src/codex-bridge-transport.ts';
import { parseResponse, uuid, type BridgeRequest } from '../src/codex-bridge-protocol.ts';
const hello = (): BridgeRequest => ({version:1,id:randomUUID(),type:'hello'});
function transport(mode: string) {
  const child = spawn(process.execPath,[fileURLToPath(new URL('./fixtures/codex-jsonl.mjs',import.meta.url)),mode],{stdio:['pipe','pipe','pipe'],env:{}});
  return {child,pipe:new JsonlBridgeTransport(child,40)};
}

test('private pipe parses chunked CRLF responses and closes its owned process', async () => {
  const {pipe,child}=transport('normal');
  try { const request=hello(); assert.equal((await pipe.request(request)).id,request.id); }
  finally { await pipe.close(); }
  assert.ok(child.exitCode!==null || child.signalCode!==null);
});
for(const mode of ['duplicate','oversize','utf8','unsolicited','truncated','stderr-oversize']) test(`private pipe fails closed on ${mode} without returning diagnostics`, async () => {
  const {pipe}=transport(mode);
  try { await assert.rejects(pipe.request(hello()), error => error instanceof Error && /^BRIDGE_/.test(error.message) && !error.message.includes('PRIVATE_EXCEPTION_SENTINEL')); }
  finally { await pipe.close(); }
});

test('stderr is never interpreted as model output or returned to callers', async () => {
  const {pipe}=transport('stderr');
  try { assert.equal((await pipe.request(hello())).status,'ready'); } finally { await pipe.close(); }
});
for (const mode of ['cancel','late-success']) test(`abort sends cancellation and discards ${mode} terminal response`, async () => {
  const {pipe}=transport(mode); const controller=new AbortController();
  const pending=pipe.request(hello(),controller.signal); const check=assert.rejects(pending,/BRIDGE_CANCELLED/);
  controller.abort(); await check; await pipe.close();
});

test('timeout cancels once, bounds termination and never restarts a nonresponsive subprocess', async () => {
  const {pipe,child}=transport('ignore');
  await assert.rejects(pipe.request(hello(),undefined,25),/BRIDGE_TIMEOUT/);
  await pipe.close(); assert.ok(child.exitCode!==null || child.signalCode!==null);
  await assert.rejects(pipe.request(hello()),/BUSY_OR_CLOSED/);
});

test('private pipe rejects simultaneous requests and oversized input before transmitting it', async () => {
  const {pipe}=transport('cancel'); const controller=new AbortController();
  const first=pipe.request(hello(),controller.signal); const checked=assert.rejects(first,/CANCELLED/);
  await assert.rejects(pipe.request(hello()),/BUSY_OR_CLOSED/); controller.abort(); await checked; await pipe.close();
  const second=transport('normal');
  try { await assert.rejects(second.pipe.request({version:1,id:randomUUID(),type:'turn',run_id:randomUUID(),turn:1,messages:[{role:'user',text:'x'.repeat(256000)}]}),/REQUEST_REJECTED/); }
  finally { await second.pipe.close(); }
});

test('wire identifiers and sanitized errors reject trailing newline unlike JS dollar anchors', () => {
  assert.equal(uuid(randomUUID()+'\n'),false);
  assert.throws(()=>parseResponse({version:1,id:randomUUID(),type:'response',status:'failed',error_code:'SAFE\n'}),/PROTOCOL/);
});

test('packaged Python has a fixed executable/argv, isolated portable paths and no credential or proxy environment', async () => {
  const root=await mkdtemp(join(tmpdir(),'zhizhi-packaged-fixture-'));
  try {
    const runtime=join(root,'runtime','python'); const state=join(root,'data'); await mkdir(runtime,{recursive:true}); await mkdir(state);
    const python=join(runtime,'python.exe'); await writeFile(python,'not executable; path validation fixture only');
    const env:NodeJS.ProcessEnv={ZHIZHI_PORTABLE_ROOT:root,ZHIZHI_PI_PYTHON:python,ZHIZHI_PORTABLE_STATE:state,PATH:join(root,'runtime','git'),
      OPENAI_API_KEY:'PRIVATE_EXCEPTION_SENTINEL',ZHIZHI_AGENT_TOKEN:'PRIVATE_EXCEPTION_SENTINEL',ZHIZHI_USER_TOKEN:'PRIVATE_EXCEPTION_SENTINEL',HTTP_PROXY:'https://example.invalid',PYTHONPATH:'/outside',NODE_OPTIONS:'forbidden'};
    for(const name of ['HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','TMPDIR','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME'])env[name]=state;
    const value=packagedPython(env);
    assert.deepEqual(value.args,['-B','-s','-X','utf8','-m','ahadiff.knowledge_platform.model.pi_bridge','--owner-acceptance']);
    assert.equal(value.executable,python); assert.equal(value.env.PYTHONNOUSERSITE,'1'); assert.equal(value.env.ZHIZHI_PORTABLE_STATE,state);
    assert.ok(!JSON.stringify(value).includes('PRIVATE_EXCEPTION_SENTINEL')); assert.equal(value.env.HTTP_PROXY,undefined); assert.equal(value.env.PYTHONPATH,undefined);
    assert.throws(()=>packagedPython({...env,ZHIZHI_PI_PYTHON:process.execPath}),/PACKAGED_RUNTIME/);
    assert.throws(()=>packagedPython({...env,HOME:tmpdir()}),/PORTABLE_PATH/);
    assert.throws(()=>packagedPython({...env,XDG_CONFIG_HOME:undefined}),/PORTABLE_PATH/);
    await symlink(tmpdir(),join(root,'escape'));
    assert.throws(()=>packagedPython({...env,TEMP:join(root,'escape')}),/PORTABLE_PATH/);
  } finally {await rm(root,{recursive:true,force:true});}
});

test('missing portable paths reject before the production factory can spawn any subprocess', async () => {
  const childProcess = await import('node:child_process');
  const { syncBuiltinESMExports } = await import('node:module');
  const { mock } = await import('node:test');
  const { spawnPackagedBridge } = await import('../src/codex-bridge-transport.ts');
  let spawns = 0;
  const patched = mock.method(childProcess.default, 'spawn', (() => { spawns++; throw new Error('MUST_NOT_SPAWN'); }) as typeof spawn);
  const oldRoot = process.env.ZHIZHI_PORTABLE_ROOT;
  try {
    syncBuiltinESMExports(); delete process.env.ZHIZHI_PORTABLE_ROOT;
    assert.throws(() => spawnPackagedBridge(), /PACKAGED_RUNTIME_REQUIRED/); assert.equal(spawns, 0);
  } finally {
    if (oldRoot === undefined) delete process.env.ZHIZHI_PORTABLE_ROOT; else process.env.ZHIZHI_PORTABLE_ROOT = oldRoot;
    patched.mock.restore(); syncBuiltinESMExports();
  }
});
