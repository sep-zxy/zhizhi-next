import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { zhizhiPrimitives, zhizhiTokens, zhizhiVars } from '../../src/styles/zhizhi-tokens';
import { growthMantineTheme, zhizhiMantineTheme } from '../../src/styles/zhizhi-mantine-theme';

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const buildScript = join(appRoot, 'scripts', 'build-tokens.mjs');

function parseHexColor(hex: string): [number, number, number] {
  const normalized = hex.trim().replace(/^#/, '');
  if (normalized.length !== 6) {
    throw new Error(`Expected 6-digit hex color, got "${hex}"`);
  }
  return [
    parseInt(normalized.slice(0, 2), 16),
    parseInt(normalized.slice(2, 4), 16),
    parseInt(normalized.slice(4, 6), 16),
  ];
}

function relativeLuminance(hex: string): number {
  const [r, g, b] = parseHexColor(hex).map((channel) => {
    const s = channel / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(fgHex: string, bgHex: string): number {
  const l1 = relativeLuminance(fgHex);
  const l2 = relativeLuminance(bgHex);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

describe('Zhizhi Design Tokens', () => {
  it('matches Figma variable counts and keeps generated CSS/TS outputs in sync', () => {
    const output = execFileSync(process.execPath, [buildScript, '--check'], {
      cwd: appRoot,
      encoding: 'utf8',
    });

    expect(output).toContain('校验通过');
    expect(output).toContain('共 104 个 Token');
    expect(output).toContain('Primitives 41 + Semantic 49 + Layout/Typography/Surface 14');
  });

  it('keeps semantic CSS variables mapped to --zz-* references', () => {
    expect(zhizhiVars.bgColorPage).toBe('var(--zz-bg-color-page)');
    expect(zhizhiVars.textColorPrimary).toBe('var(--zz-text-color-primary)');
    expect(zhizhiVars.brandColor).toBe('var(--zz-brand-color)');
    expect(zhizhiVars.brandColorAccent).toBe('var(--zz-brand-color-accent)');
    expect(zhizhiVars.blurGlass).toBe('var(--zz-blur-glass)');

    expect(zhizhiTokens.bgColorPage).toBe('#f0f2ec');
    expect(zhizhiTokens.textColorPrimary).toBe('#262b27');
    expect(zhizhiTokens.brandColor).toBe('#276342');
    expect(zhizhiTokens.brandColorAccent).toBe('#b5e875');
    expect(zhizhiTokens.shadowPanel).toBe('0 36px 56px -20px rgba(20, 28, 20, 0.22)');
    expect(zhizhiPrimitives.size9).toBe('36px');
  });

  it('meets WCAG AA contrast on key foreground/background token pairs', () => {
    // 常规浅底墨色正文与辅助文字
    expect(contrastRatio(zhizhiTokens.textColorPrimary, zhizhiTokens.bgColorContainer)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.textColorPrimary, zhizhiTokens.bgColorPage)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.textColorSecondary, zhizhiTokens.bgColorContainer)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.textColorSecondary, zhizhiTokens.bgColorPage)).toBeGreaterThanOrEqual(4.5);

    // 炭黑重点容器配反白文字
    expect(contrastRatio(zhizhiTokens.textColorAnti, zhizhiTokens.bgColorEmphasis)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.textColorAntiSecondary, zhizhiTokens.bgColorEmphasis)).toBeGreaterThanOrEqual(4.5);

    // 嫩绿强调按钮配墨色文字；深绿品牌色配纯白/浅绿底
    expect(contrastRatio(zhizhiTokens.textColorPrimary, zhizhiTokens.brandColorAccent)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.brandColor, zhizhiTokens.bgColorContainer)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.brandColor, zhizhiTokens.brandColorLight)).toBeGreaterThanOrEqual(4.5);

    // 功能反馈色（文字配对应浅底）
    expect(contrastRatio(zhizhiTokens.successColor, zhizhiTokens.successColorLight)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.warningColor, zhizhiTokens.warningColorLight)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.infoColor, zhizhiTokens.infoColorLight)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(zhizhiTokens.errorColor, zhizhiTokens.errorColorLight)).toBeGreaterThanOrEqual(4.5);
  });

  it('wires zhizhi-tokens.css into apps/learning-web/src/main.tsx and exposes both Mantine themes', () => {
    const mainSource = readFileSync(join(appRoot, 'src', 'main.tsx'), 'utf8');
    expect(mainSource).toContain("import './styles/zhizhi-tokens.css';");
    expect(mainSource).toContain('growthMantineTheme');

    expect(growthMantineTheme.primaryColor).toBe('pine');
    expect(zhizhiMantineTheme.primaryColor).toBe('zhizhiGreen');
    expect(zhizhiMantineTheme.fontFamily).toBe(zhizhiTokens.fontUi);
    expect(zhizhiMantineTheme.fontFamilyMonospace).toBe(zhizhiTokens.fontMono);
  });
});
