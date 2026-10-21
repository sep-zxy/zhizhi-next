/** Native-only entrypoint: no loader registration via NODE_OPTIONS, TS stripping or jiti. */
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (/^(?:jiti|tsx|typescript)(?:\/|$)/u.test(specifier) || /(?:^|\/)jiti[^/]*\.js$/u.test(specifier)) {
      throw new Error('RUNTIME_TRANSPILATION_FORBIDDEN');
    }
    const result = nextResolve(specifier, context);
    if (/\.(?:ts|tsx|mts|cts)(?:[?#]|$)/u.test(result.url) || /\/node_modules\/(?:jiti|tsx|typescript)\//u.test(result.url)) {
      throw new Error('RUNTIME_TRANSPILATION_FORBIDDEN');
    }
    return result;
  },
});
process.env.PI_OFFLINE = '1';
process.env.PI_SKIP_VERSION_CHECK = '1';
process.env.ZHIZHI_PI_CLI_JS = fileURLToPath(import.meta.url);
await import('./node_modules/@earendil-works/pi-coding-agent/dist/cli.js');
