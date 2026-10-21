/** Cloud build only. Produces a relocatable, credential-free, native-JS Pi component. */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(process.argv[2] ?? 'portable-build');
if (existsSync(out)) throw new Error('OUTPUT_MUST_NOT_EXIST');
const run = (command, args, cwd = root) => {
  const result = spawnSync(command, args, { cwd, stdio:'inherit', shell:false });
  if (result.status !== 0) throw new Error(`BUILD_STEP_FAILED:${command}`);
};
run(process.execPath, ['node_modules/typescript/bin/tsc', '-p', 'tsconfig.build.json']);
mkdirSync(out, {recursive:true});
for (const file of ['package.json','package-lock.json']) cpSync(join(root,file),join(out,file));
run(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['ci','--omit=dev','--ignore-scripts','--offline',
  '--cache', process.env.npm_config_cache ?? '/workspace/shared/tools/npm-cache'], out);
cpSync(join(root,'dist'),join(out,'extensions'),{recursive:true});
for (const file of readdirSync(join(root,'portable'))) cpSync(join(root,'portable',file),join(out,file));
const packageRoot = join(out,'node_modules/@earendil-works/pi-coding-agent');
const loader = join(packageRoot,'dist/core/extensions/loader.js');
const original = readFileSync(loader,'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const originalHash = '44a356da552c9cf2ea619c61944cfb28f81b45f09b325f152c91a914bfce1bc0';
if (sha(original) !== originalHash) throw new Error('UPSTREAM_LOADER_HASH_MISMATCH');
const start = original.indexOf('    const createJitiImpl = await getCreateJiti();', original.indexOf('async function loadExtensionModule('));
const end = original.indexOf('    const factory = module;', start);
if (start < 0 || end < 0) throw new Error('UPSTREAM_LOADER_ANCHOR_MISMATCH');
const replacement = '    const { loadNativeExtension } = await import("../../../../../../native-extension-loader.mjs");\n    const module = await loadNativeExtension(extensionPath);\n';
writeFileSync(loader, original.slice(0,start) + replacement + original.slice(end));
// No compiler or alternative bundled loader is shipped in this constrained component.
for (const name of ['jiti','tsx','typescript','esbuild','@esbuild']) rmSync(join(out,'node_modules',name),{recursive:true,force:true});
rmSync(join(packageRoot,'dist/bundle'),{recursive:true,force:true});
for (const name of ['jiti-loader.js','jiti-static-loader.js']) rmSync(join(packageRoot,'dist/core/extensions',name),{force:true});
const nativeRoot = join(out,'node_modules/@earendil-works/pi-tui/native');
for (const platform of ['linux','darwin']) rmSync(join(nativeRoot,platform,'prebuilds'),{recursive:true,force:true});
rmSync(join(nativeRoot,'win32/prebuilds/win32-arm64'),{recursive:true,force:true});
writeFileSync(join(out,'package.json'), JSON.stringify({name:'@zhizhi/pi-native-portable',version:'0.1.0',private:true,type:'module'},null,2)+'\n');
// Source lock retained for supply-chain review, never consumed on the user's machine.
const files = {}, licenses = [], native = [];
function walk(directory) {
  for (const entry of readdirSync(directory,{withFileTypes:true})) {
    const path = join(directory,entry.name), name = relative(out,path).replaceAll('\\','/');
    if (entry.isSymbolicLink()) throw new Error(`SYMLINK_NOT_PORTABLE:${name}`);
    if (entry.isDirectory()) { if (entry.name === '.bin') { rmSync(path,{recursive:true,force:true}); continue; } walk(path); }
    else {
      files[name] = sha(readFileSync(path));
      if (/^(?:license|copying|notice)/iu.test(entry.name)) licenses.push(name);
      if (entry.name.endsWith('.node')) {
        if (!name.endsWith('/win32-x64/win32-platform.node')) throw new Error(`UNEXPECTED_NATIVE_BINARY:${name}`);
        native.push(name);
      }
    }
  }
}
walk(out);
const sourceCommit = spawnSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).stdout.trim();
writeFileSync(join(out,'component.json'),JSON.stringify({name:'pi',version:'1.3.0+zhizhi-native.1',source_commit:sourceCommit,
  entrypoints:{cli:'cli-native.mjs',interactive:'launch-interactive.mjs',worker:'cli-native.mjs',guard:'extensions/model-guard.js'},
  files,licenses,runtime_requirements:{node:'>=22.19.0',platform:'win32-x64',native_files:native},
  adaptation:{upstream_loader_sha256:originalHash,patched_loader_sha256:files['node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js'],type:'allowlisted native ESM import; no transpilation fallback'},
  known_limits:['Windows execution not verified on Linux','No real account/model calls performed','Missing approved account defaults to waiting_auth','No ambient extensions, tools, MCP, skills or runtime downloads permitted']},null,2)+'\n');
console.log(JSON.stringify({component:out,files:Object.keys(files).length,native}));
