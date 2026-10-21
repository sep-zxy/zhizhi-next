/** Exact, explicitly configured subscription route only; no account or provider guessing. */
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { isAbsolute, resolve } from 'node:path';
const provider = process.env.ZHIZHI_MODEL_PROVIDER;
const model = process.env.ZHIZHI_MODEL_ID;
const agentDir = process.env.PI_CODING_AGENT_DIR;
if (!provider || !model) {
  console.log(JSON.stringify({status:'waiting_auth',code:'APPROVED_EXACT_SUBSCRIPTION_ROUTE_REQUIRED'}));
  process.exit(0);
}
if (!['openai','zai','zai-coding-cn'].includes(provider) || /[*,:\r\n]/u.test(model) || !agentDir || !isAbsolute(agentDir)) {
  throw new Error('EXACT_ROUTE_AND_DEDICATED_AGENT_DIRECTORY_REQUIRED');
}
if (process.argv.length !== 2) throw new Error('INTERACTIVE_ARGUMENT_OVERRIDES_FORBIDDEN');
const cli = fileURLToPath(new URL('./cli-native.mjs', import.meta.url));
const extension = fileURLToPath(new URL('./extensions/index.js', import.meta.url));
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !/^(?:NODE_OPTIONS|NODE_PATH|OPENAI_API_KEY|ANTHROPIC_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY)$/u.test(key)));
env.ZHIZHI_ALLOWED_PROVIDERS = provider;
env.ZHIZHI_PI_CLI_JS = cli;
env.PI_OFFLINE = '1'; env.PI_SKIP_VERSION_CHECK = '1';
const authCheck = spawnSync(process.execPath, ['--no-experimental-strip-types', cli,
  'auth', 'check', '--provider', provider, '--json', '--no-refresh'],
  {cwd:resolve(agentDir),env,encoding:'utf8',timeout:15000,maxBuffer:16384});
let auth;
try { auth = JSON.parse(authCheck.stdout ?? ''); } catch { auth = {}; }
if (authCheck.status !== 0 || auth.status !== 'ready' || auth.provider !== provider ||
    auth.authType !== (provider === 'openai' ? 'oauth' : 'api_key')) {
  console.log(JSON.stringify({status:'waiting_auth',code:'OFFICIAL_SUBSCRIPTION_AUTH_REQUIRED'}));
  process.exit(0);
}
const child = spawn(process.execPath, ['--no-experimental-strip-types', cli,
  '--offline', '--no-approve', '--no-tools', '--no-mcp', '--no-extensions', '--no-skills',
  '--no-prompt-templates', '--no-context-files', '--no-themes', '--provider', provider,
  '--model', model, '--extension', extension], {cwd: resolve(agentDir), env, stdio:'inherit'});
child.on('error', error => { console.error(error.message); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
