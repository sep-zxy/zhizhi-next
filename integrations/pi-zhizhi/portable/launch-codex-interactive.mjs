/** One explicitly configured packaged Codex CLI bridge. No Pi OAuth/key login or provider fallback. */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { isAbsolute, resolve, join, relative, sep } from 'node:path';
import { existsSync, realpathSync } from 'node:fs';
const waiting = code => { console.log(JSON.stringify({status:'waiting_auth',code})); process.exit(0); };
const once = process.argv.length === 3 && process.argv[2] === '--once-json';
if (process.argv.length !== 2 && !once) throw new Error('INTERACTIVE_ARGUMENT_OVERRIDES_FORBIDDEN');
const provider = process.env.ZHIZHI_MODEL_PROVIDER;
const model = process.env.ZHIZHI_MODEL_ID;
const agentDir = process.env.PI_CODING_AGENT_DIR;
const root = process.env.ZHIZHI_PORTABLE_ROOT;
const python = process.env.ZHIZHI_PI_PYTHON;
if (provider !== 'zhizhi-codex-cli' || model !== 'gpt-6.1-sol') waiting('APPROVED_EXACT_CODEX_BRIDGE_REQUIRED');
if (!agentDir || !isAbsolute(agentDir) || !root || !isAbsolute(root) || !python || !isAbsolute(python)) waiting('PACKAGED_CODEX_BRIDGE_REQUIRED');
try {
  const canonicalRoot = realpathSync(root);
  const expected = join(canonicalRoot,'runtime','python','python.exe');
  const same = (left, right) => process.platform === 'win32' ? left.toLowerCase() === right.toLowerCase() : left === right;
  if (!same(resolve(root),canonicalRoot) || !same(resolve(python),expected) || !same(realpathSync(expected),expected)) waiting('PACKAGED_CODEX_BRIDGE_REQUIRED');
  const tail = relative(canonicalRoot,realpathSync(agentDir));
  if (tail === '..' || tail.startsWith('..'+sep) || isAbsolute(tail) || existsSync(join(agentDir,'models.json'))) waiting('DEDICATED_UNMODIFIED_PI_HOST_REQUIRED');
} catch { waiting('PACKAGED_CODEX_BRIDGE_REQUIRED'); }
try {
  const { SettingsManager } = await import('./node_modules/@earendil-works/pi-coding-agent/dist/core/settings-manager.js');
  const { disableAutomaticRetries } = await import('./retry-policy.mjs');
  await disableAutomaticRetries(SettingsManager, agentDir);
} catch { waiting('PI_RETRY_POLICY_UNAVAILABLE'); }
const cli = fileURLToPath(new URL('./cli-native.mjs', import.meta.url));
const extension = fileURLToPath(new URL('./extensions/index.js', import.meta.url));
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !/^(?:NODE_OPTIONS|NODE_PATH|PYTHONPATH|PYTHONHOME|.*API_KEY)$/u.test(key)));
env.ZHIZHI_ALLOWED_PROVIDERS = 'zhizhi-codex-cli';
env.ZHIZHI_PI_CLI_JS = cli; env.PI_OFFLINE = '1'; env.PI_SKIP_VERSION_CHECK = '1';
const child = spawn(process.execPath, ['--no-experimental-strip-types', cli,
  ...(once ? ['--mode', 'json'] : []),
  '--no-session', '--offline', '--no-approve', '--no-builtin-tools', '--tools', 'zhizhi_intake,zhizhi_job_status,zhizhi_search,zhizhi_read_entry,zhizhi_propose_patch,zhizhi_relations,zhizhi_traverse_relations,zhizhi_review_queue,zhizhi_radar,zhizhi_growth,zhizhi_inbox,zhizhi_intake_preview,zhizhi_tasks,zhizhi_task_events', '--no-mcp', '--no-extensions', '--no-skills',
  '--no-prompt-templates', '--no-context-files', '--no-themes', '--provider', provider,
  '--model', model, '--extension', extension], {cwd:resolve(agentDir),env,stdio:'inherit',shell:false});
let deadline;
for (const signal of ['SIGINT','SIGTERM']) process.on(signal, () => {
  if (!deadline) { child.kill(signal); deadline = setTimeout(() => child.kill('SIGKILL'),8000); }
});
child.on('error', () => { console.error('PI_PROCESS_UNAVAILABLE'); process.exitCode = 1; });
child.on('exit', code => { if (deadline) clearTimeout(deadline); process.exitCode = code ?? 1; });
