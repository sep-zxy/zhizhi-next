/** Parse source only. No imports of application code, dependency installs or builds. */
import { spawnSync } from 'node:child_process';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { dirname, resolve, relative, extname } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const skipped = new Set(['.git', '.venv', 'node_modules', 'target', 'dist', '.local', 'archive', 'output', '.quartz', '__pycache__']);
function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    if (skipped.has(entry.name)) return [];
    const name = resolve(directory, entry.name);
    if (entry.isSymbolicLink()) return [];
    return entry.isDirectory() ? walk(name) : [name];
  });
}
function git(args) {
  const result = spawnSync('git', args, { cwd: root, encoding: 'utf8' });
  if (result.status !== 0) throw new Error('Cannot enumerate changed source');
  return result.stdout.split('\0').filter(Boolean);
}
const all = process.argv.includes('--all');
const paths = all ? walk(root) : [...new Set([
  ...git(['diff', '--name-only', '-z', 'HEAD']),
  ...git(['ls-files', '--others', '--exclude-standard', '-z']),
])].map(name => resolve(root, name)).filter(name => existsSync(name));
const files = paths.filter(name => !relative(root, name).split(/[\\/]/).some(part => skipped.has(part)));
const python = files.filter(name => extname(name) === '.py');
let failed = 0;
if (python.length) {
  const result = spawnSync('uv', ['run', '--no-project', '--offline', '--python', '3.13', 'python', '-B', 'scripts/parse-python.py'], {
    cwd: root, encoding: 'utf8', input: JSON.stringify(python),
    env: { ...process.env, UV_PYTHON_DOWNLOADS: 'never' },
  });
  process.stdout.write(result.stdout ?? '');
  process.stderr.write(result.stderr ?? '');
  if (result.status !== 0) failed++;
}
let typescript;
for (const owner of ['apps/learning-web', 'apps/wiki-private', 'integrations/pi-zhizhi', 'optional/continuity-widget']) {
  try { typescript = createRequire(resolve(root, owner, 'package.json'))('typescript'); break; } catch { /* Try another installed owner; never install here. */ }
}
const counts = { javascript: 0, typescript: 0, powershell: 0, rust: 0 };
const missing = new Set();
for (const name of files) {
  const extension = extname(name);
  if (['.js', '.mjs', '.cjs'].includes(extension)) {
    const result = spawnSync(process.execPath, ['--check', name], { encoding: 'utf8' });
    counts.javascript++;
    if (result.status !== 0) { process.stderr.write(result.stderr ?? 'JavaScript parser failed\n'); failed++; }
  } else if (['.ts', '.tsx', '.mts', '.cts'].includes(extension)) {
    if (!typescript) { missing.add('TypeScript parser (CI or an already installed component)'); continue; }
    const kind = extension === '.tsx' ? typescript.ScriptKind.TSX : typescript.ScriptKind.TS;
    const source = typescript.createSourceFile(name, readFileSync(name, 'utf8'), typescript.ScriptTarget.Latest, true, kind);
    counts.typescript++;
    for (const diagnostic of source.parseDiagnostics) {
      console.error(`${relative(root, name)}: ${typescript.flattenDiagnosticMessageText(diagnostic.messageText, '\n')}`);
      failed++;
    }
  } else if (extension === '.ps1') {
    const code = '$ErrorActionPreference = "Stop"; $tokens = $null; $errors = $null; [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors) | Out-Null; if ($errors.Count) { $errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }; exit 1 }';
    // -File gives the parser a literal path argument; the helper never executes the target.
    const result = spawnSync('pwsh', ['-NoProfile', '-File', resolve(root, 'scripts/parse-powershell.ps1'), name], { encoding: 'utf8' });
    counts.powershell++;
    if (result.status !== 0) { console.error(result.stderr || result.error?.message || code); failed++; }
  } else if (extension === '.rs') {
    const result = spawnSync('rustfmt', ['--edition', '2024', '--emit', 'stdout', name], { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
    if (result.error?.code === 'ENOENT') missing.add('rustfmt');
    else { counts.rust++; if (result.status !== 0) { console.error(result.stderr); failed++; } }
  }
}
console.log(JSON.stringify({ scope: all ? 'active source' : 'changed source', parsed: counts, failures: failed, unchecked: [...missing], application_executed: false }));
if (missing.size) console.error('Unchecked languages require existing tools or CI; no dependency was installed.');
process.exitCode = failed || missing.size ? 1 : 0;
