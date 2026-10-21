import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { BRIDGE_PROVIDER, fail } from './codex-bridge-protocol.ts';
import type { CodexBridgeProvider } from './codex-provider.ts';
import { guardProvider } from './subscription-policy.ts';

export function installCodexProvider(pi: ExtensionAPI, bridge: CodexBridgeProvider): void {
  pi.registerProvider(bridge.provider);
  pi.on('session_start', async (_event, ctx) => {
    // Dispatch is fail closed even if a request hook is ignored by the host.
    for (const id of new Set(ctx.modelRegistry.getAll().map(model => model.provider))) {
      if (id === BRIDGE_PROVIDER) continue;
      const original = ctx.modelRegistry.getProvider(id); if (!original) continue;
      const denied = guardProvider(original, async () => fail('BRIDGE_OTHER_PROVIDER_FORBIDDEN'));
      denied.getModels = () => []; denied.getAllModels = () => []; denied.refreshModels = undefined;
      pi.registerProvider(denied);
    }
    pi.setActiveTools([]);
    try { const caps = await bridge.initialize(); pi.setActiveTools(caps.tools.map(tool => tool.name)); }
    catch { ctx.ui.notify('本机 Codex 桥未获配置确认，未调用模型。请核对已批准的便携验收范围。', 'warning'); }
  });
  pi.on('session_shutdown', () => bridge.close());
}
