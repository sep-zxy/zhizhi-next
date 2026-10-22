/** Real Pi settings API, entirely synthetic local profile fixtures. */
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
const { SettingsManager } = await import(join(dirname(fileURLToPath(import.meta.resolve('@earendil-works/pi-coding-agent'))),'core/settings-manager.js'));
const { disableAutomaticRetries } = await import(new URL('../portable/retry-policy.mjs',import.meta.url).href);

test('dedicated profile disables automatic retry through official API without replacing unrelated preferences',async()=>{
 const root=await mkdtemp(join(tmpdir(),'pi-retry-fixture-'));
 try {
  await writeFile(join(root,'settings.json'),JSON.stringify({theme:'fixture-theme',retry:{enabled:true,maxRetries:3,baseDelayMs:2000}}));
  await disableAutomaticRetries(SettingsManager,root);
  const value=JSON.parse(await readFile(join(root,'settings.json'),'utf8'));
  assert.deepEqual(value,{theme:'fixture-theme',retry:{enabled:false,maxRetries:3,baseDelayMs:2000}});
  assert.equal(SettingsManager.create(root,root).getRetryEnabled(),false);
  await mkdir(join(root,'.pi'));await writeFile(join(root,'.pi/settings.json'),JSON.stringify({retry:{enabled:true}}));
  await assert.rejects(disableAutomaticRetries(SettingsManager,root),/PI_RETRY_POLICY_UNAVAILABLE/);
 } finally {await rm(root,{recursive:true,force:true});}
});
test('profile malformed settings and redirected settings fail closed without overwriting the target',async()=>{
 const root=await mkdtemp(join(tmpdir(),'pi-retry-fixture-'));
 try {
  await writeFile(join(root,'settings.json'),'{PRIVATE_SENTINEL');
  await assert.rejects(disableAutomaticRetries(SettingsManager,root),/PI_RETRY_POLICY_UNAVAILABLE/);
  assert.equal(await readFile(join(root,'settings.json'),'utf8'),'{PRIVATE_SENTINEL');
  await rm(join(root,'settings.json'));await writeFile(join(root,'other.json'),'{}');
  await symlink(join(root,'other.json'),join(root,'settings.json'));
  await assert.rejects(disableAutomaticRetries(SettingsManager,root),/PI_RETRY_POLICY_UNAVAILABLE/);
  assert.equal(await readFile(join(root,'other.json'),'utf8'),'{}');
 } finally {await rm(root,{recursive:true,force:true});}
});
