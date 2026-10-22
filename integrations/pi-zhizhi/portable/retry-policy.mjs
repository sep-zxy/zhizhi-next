/** Official Pi settings API; only the dedicated product profile is changed. */
import { lstatSync, realpathSync } from 'node:fs';
import { join, resolve } from 'node:path';
export async function disableAutomaticRetries(SettingsManager, agentDir) {
  const same = (a,b) => process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
  for (const path of [agentDir, join(agentDir,'settings.json'), join(agentDir,'.pi'), join(agentDir,'.pi','settings.json')]) {
    // Refuse symlinks/junctions, including dangling links, before any settings write.
    try {
      const info = lstatSync(path);
      if (info.isSymbolicLink() || !same(realpathSync(path),resolve(path))) throw new Error('PI_RETRY_POLICY_UNAVAILABLE');
    } catch (error) { if (error.code !== 'ENOENT') throw new Error('PI_RETRY_POLICY_UNAVAILABLE'); }
  }
  const settings = SettingsManager.create(agentDir,agentDir);
  if (settings.drainErrors().length) throw new Error('PI_RETRY_POLICY_UNAVAILABLE');
  settings.setRetryEnabled(false);
  await settings.flush();
  if (settings.drainErrors().length) throw new Error('PI_RETRY_POLICY_UNAVAILABLE');
  // Reload exactly as the CLI does. A project override may not re-enable retries.
  const checked = SettingsManager.create(agentDir,agentDir);
  if (checked.drainErrors().length || checked.getRetryEnabled() !== false) throw new Error('PI_RETRY_POLICY_UNAVAILABLE');
}
