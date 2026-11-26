#!/usr/bin/env node
/**
 * 知枝（Zhizhi）Design Token 构建与校验脚本
 *
 * 输入：apps/learning-web/tokens/zhizhi.tokens.json（W3C DTCG 格式）
 * 输出：
 *   1. apps/learning-web/src/styles/zhizhi-tokens.css（全部 --zz-* CSS 变量与玻璃降级规则）
 *   2. apps/learning-web/src/styles/zhizhi-tokens.ts （CSS 变量表达式与 Light 模式解析常量）
 *
 * 用法：
 *   node apps/learning-web/scripts/build-tokens.mjs          生成/更新产物文件
 *   node apps/learning-web/scripts/build-tokens.mjs --check  仅校验产物是否与总账本一致，不写入文件
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(__dirname, '..');
const SOURCE_PATH = resolve(APP_ROOT, 'tokens/zhizhi.tokens.json');
const CSS_OUTPUT_PATH = resolve(APP_ROOT, 'src/styles/zhizhi-tokens.css');
const TS_OUTPUT_PATH = resolve(APP_ROOT, 'src/styles/zhizhi-tokens.ts');

const ALIAS_RE = /^\{([a-zA-Z0-9._-]+)\}$/;

const GENERIC_FONT_FAMILIES = new Set([
  'serif',
  'sans-serif',
  'monospace',
  'cursive',
  'fantasy',
  'system-ui',
  'ui-serif',
  'ui-sans-serif',
  'ui-monospace',
  'ui-rounded',
]);

function isTokenNode(node) {
  return Boolean(node && typeof node === 'object' && !Array.isArray(node) && '$value' in node);
}

function collectTokens(root) {
  const flatMap = new Map();
  const ordered = [];

  function walk(node, pathParts, inheritedType) {
    if (!node || typeof node !== 'object' || Array.isArray(node)) return;
    const currentType = typeof node.$type === 'string' ? node.$type : inheritedType;

    if (isTokenNode(node)) {
      const path = pathParts.join('.');
      const cssVar = node.$extensions?.['com.zhizhi.cssVar'];
      if (typeof cssVar !== 'string' || !cssVar.startsWith('--zz-')) {
        throw new Error(`Token "${path}" 缺少合法的 $extensions["com.zhizhi.cssVar"]（必须以 --zz- 开头）`);
      }
      const entry = {
        path,
        section: pathParts[0],
        group: pathParts[1] ?? '',
        name: pathParts.slice(1).join('.'),
        type: currentType,
        rawValue: node.$value,
        description: node.$description ?? '',
        cssVar,
        figma: node.$extensions?.['com.zhizhi.figma'] ?? null,
      };
      if (flatMap.has(path)) {
        throw new Error(`存在重复 Token 路径: "${path}"`);
      }
      flatMap.set(path, entry);
      ordered.push(entry);
      return;
    }

    for (const [key, value] of Object.entries(node)) {
      if (key.startsWith('$')) continue;
      walk(value, [...pathParts, key], currentType);
    }
  }

  walk(root, [], undefined);
  return { flatMap, ordered };
}

function formatRawValueForCss(value, type) {
  if (type === 'fontFamily' && Array.isArray(value)) {
    return value
      .map((family) => {
        if (GENERIC_FONT_FAMILIES.has(family) || family === 'Consolas') {
          return family;
        }
        return `"${family}"`;
      })
      .join(', ');
  }
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  throw new Error(`不支持的 Token 值格式: ${JSON.stringify(value)}`);
}

function resolveTokenValue(entry, flatMap, stack = new Set()) {
  if (stack.has(entry.path)) {
    throw new Error(`检测到循环引用: ${[...stack, entry.path].join(' -> ')}`);
  }
  stack.add(entry.path);

  if (typeof entry.rawValue === 'string') {
    const match = entry.rawValue.match(ALIAS_RE);
    if (match) {
      const targetPath = match[1];
      const target = flatMap.get(targetPath);
      if (!target) {
        throw new Error(`Token "${entry.path}" 引用了不存在的目标 "${targetPath}"`);
      }
      return {
        cssExpression: `var(${target.cssVar})`,
        resolvedValue: resolveTokenValue(target, flatMap, stack).resolvedValue,
        aliasTarget: targetPath,
      };
    }
  }

  const formatted = formatRawValueForCss(entry.rawValue, entry.type);
  return {
    cssExpression: formatted,
    resolvedValue: formatted,
    aliasTarget: null,
  };
}

function validateTokens(ordered, flatMap) {
  const seenCssVars = new Set();
  const resolvedList = [];

  for (const entry of ordered) {
    if (seenCssVars.has(entry.cssVar)) {
      throw new Error(`存在重复的 CSS 变量名: ${entry.cssVar}（Token: ${entry.path}）`);
    }
    seenCssVars.add(entry.cssVar);

    const resolved = resolveTokenValue(entry, flatMap);

    if (entry.section === 'semantic') {
      if (!resolved.aliasTarget || !resolved.aliasTarget.startsWith('primitives.')) {
        throw new Error(
          `语义层 Token "${entry.path}" 必须引用 primitives 层原语，当前值: ${JSON.stringify(entry.rawValue)}`,
        );
      }
    }

    resolvedList.push({
      ...entry,
      ...resolved,
    });
  }

  return resolvedList;
}

function cssVarToCamelKey(cssVar) {
  return cssVar
    .replace(/^--zz-/, '')
    .replace(/-([a-z0-9])/g, (_, ch) => ch.toUpperCase());
}

function generateCss(resolvedList, meta) {
  const primitives = resolvedList.filter((t) => t.section === 'primitives');
  const semantic = resolvedList.filter((t) => t.section === 'semantic');
  const extras = resolvedList.filter((t) => t.section !== 'primitives' && t.section !== 'semantic');

  const lines = [
    '/*',
    ' * AUTO-GENERATED FILE — DO NOT EDIT DIRECTLY.',
    ' * Source: apps/learning-web/tokens/zhizhi.tokens.json',
    ` * Figma:  ${meta?.fileUrl ?? 'rD3fYZVSpzQV52atQPNhky'}`,
    ' * Command: node apps/learning-web/scripts/build-tokens.mjs',
    ' */',
    '',
    ':root {',
    '  color-scheme: light;',
    '',
    '  /* 1. Primitives（基础色阶与数值，仅供 Semantic 引用，业务组件禁止直接消费） */',
  ];

  let prevGroup = '';
  for (const token of primitives) {
    if (prevGroup && token.group !== prevGroup) {
      lines.push('');
    }
    lines.push(`  ${token.cssVar}: ${token.cssExpression};`);
    prevGroup = token.group;
  }

  lines.push('');
  lines.push('  /* 2. Semantic（业务与组件唯一消费层） */');

  prevGroup = '';
  for (const token of semantic) {
    if (prevGroup && token.group !== prevGroup) {
      lines.push('');
    }
    lines.push(`  ${token.cssVar}: ${token.cssExpression};`);
    prevGroup = token.group;
  }

  lines.push('');
  lines.push('  /* 3. Layout, Typography & Surface Effects */');

  let prevSection = '';
  for (const token of extras) {
    if (prevSection && token.section !== prevSection) {
      lines.push('');
    }
    lines.push(`  ${token.cssVar}: ${token.cssExpression};`);
    prevSection = token.section;
  }

  lines.push('}');
  lines.push('');
  lines.push('/* 降级回退：不支持 backdrop-filter 时提高白底不透明度，保持墨色正文可读性 */');
  lines.push('@supports not ((backdrop-filter: blur(20px)) or (-webkit-backdrop-filter: blur(20px))) {');
  lines.push('  .zz-card-glass {');
  lines.push('    background: rgba(255, 255, 255, 0.94);');
  lines.push('  }');
  lines.push('  .zz-nav-glass {');
  lines.push('    background: linear-gradient(180deg, rgba(255, 255, 255, 0.64), rgba(255, 255, 255, 0.94));');
  lines.push('  }');
  lines.push('}');
  lines.push('');

  return lines.join('\n');
}

function generateTs(resolvedList, meta) {
  const semanticAndSurface = resolvedList.filter((t) => t.section !== 'primitives');
  const primitives = resolvedList.filter((t) => t.section === 'primitives');

  const cssVarEntries = semanticAndSurface
    .map((t) => `  ${cssVarToCamelKey(t.cssVar)}: 'var(${t.cssVar})',`)
    .join('\n');

  const resolvedSemanticEntries = semanticAndSurface
    .map((t) => `  ${cssVarToCamelKey(t.cssVar)}: ${JSON.stringify(t.resolvedValue)},`)
    .join('\n');

  const resolvedPrimitiveEntries = primitives
    .map((t) => `  ${cssVarToCamelKey(t.cssVar)}: ${JSON.stringify(t.resolvedValue)},`)
    .join('\n');

  return [
    '/*',
    ' * AUTO-GENERATED FILE — DO NOT EDIT DIRECTLY.',
    ' * Source: apps/learning-web/tokens/zhizhi.tokens.json',
    ` * Figma:  ${meta?.fileUrl ?? 'rD3fYZVSpzQV52atQPNhky'}`,
    ' * Command: node apps/learning-web/scripts/build-tokens.mjs',
    ' */',
    '',
    '/** 供组件样式与内联样式引用的语义层 CSS 变量表达式 */',
    'export const zhizhiVars = {',
    cssVarEntries,
    '} as const;',
    '',
    '/** Light 模式解析后的语义层、布局、字体与玻璃面层常量值（供 Mantine Theme、Canvas/图谱等 JS 侧读取） */',
    'export const zhizhiTokens = {',
    resolvedSemanticEntries,
    '} as const;',
    '',
    '/** 底层原语色阶与尺寸常量（仅供主题派生或测试使用，业务组件请消费 zhizhiVars / zhizhiTokens） */',
    'export const zhizhiPrimitives = {',
    resolvedPrimitiveEntries,
    '} as const;',
    '',
    'export type ZhizhiVarKey = keyof typeof zhizhiVars;',
    'export type ZhizhiTokenKey = keyof typeof zhizhiTokens;',
    '',
  ].join('\n');
}

function normalizeEol(text) {
  return text.replace(/\r\n/g, '\n');
}

export function buildTokens({ check = false } = {}) {
  const rawJson = readFileSync(SOURCE_PATH, 'utf8');
  const data = JSON.parse(rawJson);
  const { flatMap, ordered } = collectTokens(data);
  const resolvedList = validateTokens(ordered, flatMap);
  const figmaMeta = data.$extensions?.['com.zhizhi.figma'];

  const cssContent = generateCss(resolvedList, figmaMeta);
  const tsContent = generateTs(resolvedList, figmaMeta);

  const outputs = [
    { path: CSS_OUTPUT_PATH, content: cssContent, label: 'apps/learning-web/src/styles/zhizhi-tokens.css' },
    { path: TS_OUTPUT_PATH, content: tsContent, label: 'apps/learning-web/src/styles/zhizhi-tokens.ts' },
  ];

  if (check) {
    const drifted = [];
    for (const out of outputs) {
      if (!existsSync(out.path)) {
        drifted.push(`${out.label}（文件不存在）`);
        continue;
      }
      const existing = normalizeEol(readFileSync(out.path, 'utf8'));
      if (existing !== normalizeEol(out.content)) {
        drifted.push(out.label);
      }
    }
    if (drifted.length > 0) {
      throw new Error(
        `Design Token 产物与 apps/learning-web/tokens/zhizhi.tokens.json 不同步：${drifted.join(', ')}。请运行 "pnpm --dir apps/learning-web tokens:build" 重新生成。`,
      );
    }
    return {
      total: resolvedList.length,
      primitives: resolvedList.filter((t) => t.section === 'primitives').length,
      semantic: resolvedList.filter((t) => t.section === 'semantic').length,
      extras: resolvedList.filter((t) => t.section !== 'primitives' && t.section !== 'semantic').length,
      checked: true,
    };
  }

  for (const out of outputs) {
    writeFileSync(out.path, out.content, 'utf8');
  }

  return {
    total: resolvedList.length,
    primitives: resolvedList.filter((t) => t.section === 'primitives').length,
    semantic: resolvedList.filter((t) => t.section === 'semantic').length,
    extras: resolvedList.filter((t) => t.section !== 'primitives' && t.section !== 'semantic').length,
    checked: false,
  };
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const check = process.argv.includes('--check');
  try {
    const summary = buildTokens({ check });
    const modeLabel = check ? '校验通过' : '已生成';
    console.log(
      `[zhizhi-tokens] ${modeLabel}：共 ${summary.total} 个 Token（Primitives ${summary.primitives} + Semantic ${summary.semantic} + Layout/Typography/Surface ${summary.extras}）`,
    );
  } catch (err) {
    console.error(`[zhizhi-tokens] 失败：${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  }
}
