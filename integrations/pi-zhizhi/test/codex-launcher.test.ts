/** Source launcher negative tests never enter Pi, Python or Codex. */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { registeredTools } from './fixtures/bridge-fixture.ts';
const launcher=fileURLToPath(new URL('../portable/launch-codex-interactive.mjs',import.meta.url));
const run=(env:NodeJS.ProcessEnv)=>spawnSync(process.execPath,[launcher],{env,encoding:'utf8',timeout:3000});
test('independent launcher rejects unconfigured or alternate routes without probing another provider',()=>{
  for(const env of [{},{ZHIZHI_MODEL_PROVIDER:'openai',ZHIZHI_MODEL_ID:'gpt-6.1-sol'},{ZHIZHI_MODEL_PROVIDER:'zhizhi-codex-cli',ZHIZHI_MODEL_ID:'gpt-5'}]){
    const result=run({...env,OPENAI_API_KEY:'PRIVATE_SENTINEL'}); assert.equal(result.status,0,result.stderr);
    assert.equal(JSON.parse(result.stdout).status,'waiting_auth'); assert.ok(!result.stdout.includes('PRIVATE_SENTINEL'));
  }
});
test('independent launcher requires packaged Python and an unmodified in-root Pi directory',async()=>{
  const root=await mkdtemp(join(tmpdir(),'zhizhi-launch-contract-'));
  try {
    const agent=join(root,'agent'); const runtime=join(root,'runtime','python'); await mkdir(agent);await mkdir(runtime,{recursive:true});
    const python=join(runtime,'python.exe');await writeFile(python,'non executable path fixture only');
    const env={ZHIZHI_MODEL_PROVIDER:'zhizhi-codex-cli',ZHIZHI_MODEL_ID:'gpt-6.1-sol',ZHIZHI_PORTABLE_ROOT:root,ZHIZHI_PI_PYTHON:python,PI_CODING_AGENT_DIR:agent};
    assert.equal(JSON.parse(run({...env,ZHIZHI_PI_PYTHON:process.execPath}).stdout).code,'PACKAGED_CODEX_BRIDGE_REQUIRED');
    assert.equal(JSON.parse(run({...env,PI_CODING_AGENT_DIR:tmpdir()}).stdout).code,'DEDICATED_UNMODIFIED_PI_HOST_REQUIRED');
    await writeFile(join(agent,'models.json'),'{}');
    assert.equal(JSON.parse(run(env).stdout).code,'DEDICATED_UNMODIFIED_PI_HOST_REQUIRED');
  } finally {await rm(root,{recursive:true,force:true});}
});
test('launcher fixed tool allowlist matches registered audited tools; built-ins and ambient discovery remain disabled',async()=>{
  const source=await readFile(launcher,'utf8');
  const match=source.match(/'--tools', '([^']+)'/);assert.ok(match);
  assert.deepEqual(match[1].split(',').sort(),registeredTools().map(t=>t.name).sort());
  for(const flag of ['--no-builtin-tools','--no-mcp','--no-extensions','--no-context-files','--no-session','--no-skills'])assert.ok(source.includes(`'${flag}'`));
  assert.ok(!source.includes("'auth', 'check'")); assert.ok(!source.includes('launch-interactive.mjs'));
  assert.ok(source.includes("process.argv[2] === '--once-json'"));
  assert.ok(source.includes("...(once ? ['--mode', 'json'] : [])"));
  const rejected=spawnSync(process.execPath,[launcher,'--once-json','--model','other'],{encoding:'utf8'});
  assert.notEqual(rejected.status,0);
  assert.match(rejected.stderr,/INTERACTIVE_ARGUMENT_OVERRIDES_FORBIDDEN/);
});
