import type { ContextItem, ContextSnapshot } from "./types";

export const DEFAULT_CONTEXT_LIMIT = 120_000;

export function createContextItem(
  kind: ContextItem["kind"],
  label: string,
  content: string,
  path?: string
): ContextItem {
  return {
    id: createContextId(kind, path || label),
    kind,
    label,
    path,
    content,
    charCount: content.length
  };
}

export function assembleContext(items: ContextItem[], limit = DEFAULT_CONTEXT_LIMIT): ContextSnapshot {
  const sections: string[] = [];
  let used = 0;
  let truncated = false;

  for (const item of items) {
    const header = `=== 素材开始：${item.label}${item.path ? `（${item.path}）` : ""} ===\n`;
    const footer = `\n=== 素材结束：${item.label} ===`;
    const available = limit - used - header.length - footer.length;

    if (available <= 0) {
      truncated = true;
      break;
    }

    const content = item.content.length > available
      ? `${item.content.slice(0, Math.max(0, available - 18))}\n[内容因预算截断]`
      : item.content;

    if (content.length < item.content.length) {
      truncated = true;
    }

    const section = `${header}${content}${footer}`;
    sections.push(section);
    used += section.length + 2;
  }

  return {
    items,
    text: sections.join("\n\n"),
    charCount: used,
    truncated
  };
}

export function upsertContextItem(items: ContextItem[], item: ContextItem): ContextItem[] {
  const existing = items.findIndex((candidate) => candidate.id === item.id);
  if (existing === -1) {
    return [...items, item];
  }

  return items.map((candidate, index) => index === existing ? item : candidate);
}

function createContextId(kind: string, source: string) {
  let hash = 2166136261;
  const value = `${kind}:${source}`;

  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }

  return `${kind}-${(hash >>> 0).toString(36)}`;
}

