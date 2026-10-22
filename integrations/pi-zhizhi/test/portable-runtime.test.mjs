/** Runs the cloud-produced artifact with native Node only, never tsx or a real model. */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
const root = resolve(process.env.ZHIZHI_TEST_PI_COMPONENT ?? 'portable-build');
const cli = join(root,'cli-native.mjs');
const envFor = directory => ({PATH:process.env.PATH, HOME:directory,
  PI_CODING_AGENT_DIR:join(directory,'agent'), PI_OFFLINE:'1', PI_SKIP_VERSION_CHECK:'1',
  ZHIZHI_MODEL_PROVIDER:'openai', ZHIZHI_MAX_OUTPUT_TOKENS:'128'});
test('native component has no compiler and declares verifiable patched loader', async () => {
  const manifest = JSON.parse(await readFile(join(root,'component.json'),'utf8'));
  assert.equal(manifest.version,'1.3.0+zhizhi-native.1');
  for (const name of ['jiti','tsx','typescript','esbuild']) assert.equal(existsSync(join(root,'node_modules',name)),false);
  const loader = await readFile(join(root,'node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js'),'utf8');
  assert.ok(loader.includes('await loadNativeExtension(extensionPath)'));
  assert.ok(!loader.includes('await jiti.import(extensionPath'));
  assert.ok(manifest.runtime_requirements.native_files.some(name => name.endsWith('win32-x64/win32-platform.node')));
  assert.equal(manifest.runtime_requirements.native_files.length,1);
  const version = spawnSync(process.execPath,['--no-experimental-strip-types',cli,'--version'],{encoding:'utf8'});
  assert.equal(version.status,0,version.stderr); assert.equal(version.stdout.trim(),'1.3.0');
});
test('unconfigured interactive launcher waits for authorization without starting a model', () => {
  const result = spawnSync(process.execPath,['--no-experimental-strip-types',join(root,'launch-interactive.mjs')],{env:{PATH:process.env.PATH},encoding:'utf8'});
  assert.equal(result.status,0,result.stderr); assert.equal(JSON.parse(result.stdout).status,'waiting_auth');
});
test('native loader rejects external JS and TypeScript rather than transpiling', async () => {
  const directory = await mkdtemp(join(tmpdir(),'pi-native-reject-'));
  const {loadNativeExtension} = await import(pathToFileURL(join(root,'native-extension-loader.mjs')).href);
  try {
    for (const suffix of ['js','ts']) {
      const file = join(directory,`evil.${suffix}`); await writeFile(file,'throw new Error("MUST_NOT_EXECUTE")');
      await assert.rejects(loadNativeExtension(file),/ONLY_PACKAGED_NATIVE_JS_EXTENSIONS_ALLOWED/);
    }
    assert.equal(typeof await loadNativeExtension(join(root,'extensions/index.js')),'function');
    assert.equal(typeof await loadNativeExtension(join(root,'extensions/model-guard.js')),'function');
  } finally { await rm(directory,{recursive:true,force:true}); }
});
for (const extension of ['index.js','model-guard.js']) test(`real Pi RPC loads ${extension} without any runtime transpiler`, async () => {
  const directory = await mkdtemp(join(tmpdir(),'pi-native-rpc-'));
  const child = spawn(process.execPath,['--no-experimental-strip-types',cli,'--mode','rpc','--no-session',
    '--offline','--no-approve','--no-tools','--no-mcp','--no-extensions','--no-skills',
    '--no-prompt-templates','--no-context-files','--no-themes','--extension',join(root,'extensions',extension)],
    {cwd:directory,env:envFor(directory),stdio:['pipe','pipe','pipe']});
  let stderr = ''; child.stderr.on('data',chunk => {stderr += chunk;});
  const timeout = setTimeout(() => child.kill('SIGKILL'),20000);
  try {
    const response = new Promise((resolveResponse,reject) => {
      let buffer = '';
      child.stdout.on('data',chunk => {
        buffer += chunk;
        let index;
        while ((index = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0,index); buffer = buffer.slice(index+1);
          if (!line) continue;
          try { const record = JSON.parse(line); if (record.id === 'native-smoke') resolveResponse(record); }
          catch (error) { reject(error); }
        }
      });
      child.on('error',reject);
      child.on('exit',code => reject(new Error(`Exited ${code}: ${stderr}`)));
    });
    child.stdin.write(JSON.stringify({type:'get_state',id:'native-smoke'})+'\n');
    assert.equal((await response).success,true);
    assert.ok(!/failed to load|extension error|unknown option|transpilation_forbidden/i.test(stderr),stderr);
  } finally {
    clearTimeout(timeout); child.stdin.end();
    if (child.exitCode === null) { child.kill('SIGTERM'); await new Promise(resolveExit => child.once('exit',resolveExit)); }
    await rm(directory,{recursive:true,force:true});
  }
});
test('configured exact route with empty auth remains waiting_auth and reveals no credentials', async () => {
  const directory = await mkdtemp(join(tmpdir(),'pi-native-empty-auth-'));
  try {
    const result = spawnSync(process.execPath,['--no-experimental-strip-types',join(root,'launch-interactive.mjs')],
      {env:{...envFor(directory),PI_CODING_AGENT_DIR:directory,ZHIZHI_MODEL_ID:'approved-fixture-model',
        OPENAI_API_KEY:'forbidden-sentinel-not-a-real-key'},encoding:'utf8',timeout:20000});
    assert.equal(result.status,0,result.stderr);
    assert.equal(JSON.parse(result.stdout).status,'waiting_auth');
    assert.ok(!(`${result.stdout}${result.stderr}`).includes('forbidden-sentinel'));
  } finally { await rm(directory,{recursive:true,force:true}); }
});
test('compiled extension preserves tool denial and human-only approval boundary', async () => {
  const {default:extension} = await import(pathToFileURL(join(root,'extensions/index.js')).href);
  const commands = new Map(), events = new Map(), tools = [];
  extension({registerTool:tool => tools.push(tool),registerCommand:(name,command) => commands.set(name,command),
    on:(name,handler) => events.set(name,[...events.get(name) ?? [],handler])});
  assert.ok(tools.some(tool => tool.name === 'zhizhi_intake'));
  for (const name of ['bash','write','codemode','mcp__evil']) {
    assert.equal(events.get('tool_call')[0]({toolName:name}).block,true);
  }
  await assert.rejects(commands.get('zhizhi-review').handler('change-1',{mode:'rpc',hasUI:true}),/REQUIRES_LOCAL_TUI/);
});

test('packaged native retry policy uses official Pi settings and preserves unrelated profile settings', async () => {
  const directory = await mkdtemp(join(tmpdir(),'pi-native-retry-fixture-'));
  try {
    const { SettingsManager } = await import(pathToFileURL(join(root,'node_modules/@earendil-works/pi-coding-agent/dist/core/settings-manager.js')).href);
    const { disableAutomaticRetries } = await import(pathToFileURL(join(root,'retry-policy.mjs')).href);
    await writeFile(join(directory,'settings.json'),JSON.stringify({theme:'fixture-theme',retry:{enabled:true,maxRetries:3}}));
    await disableAutomaticRetries(SettingsManager,directory);
    assert.equal(SettingsManager.create(directory,directory).getRetryEnabled(),false);
    assert.deepEqual(JSON.parse(await readFile(join(directory,'settings.json'),'utf8')),{theme:'fixture-theme',retry:{enabled:false,maxRetries:3}});
  } finally { await rm(directory,{recursive:true,force:true}); }
});
