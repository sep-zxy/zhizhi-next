/** Audited Pi 1.3.0 adaptation. Only packaged JS factories; never a jiti fallback. */
import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const extensions = new Map([
  [realpathSync(fileURLToPath(new URL('./extensions/index.js', import.meta.url))),
    () => import('./extensions/index.js')],
  [realpathSync(fileURLToPath(new URL('./extensions/model-guard.js', import.meta.url))),
    () => import('./extensions/model-guard.js')],
]);
export async function loadNativeExtension(extensionPath) {
  const load = extensions.get(realpathSync(extensionPath));
  if (!load) throw new Error('ONLY_PACKAGED_NATIVE_JS_EXTENSIONS_ALLOWED');
  return (await load()).default;
}
