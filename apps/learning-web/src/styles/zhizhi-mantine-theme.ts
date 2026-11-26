import { createTheme, type MantineColorsTuple } from '@mantine/core';
import { zhizhiPrimitives, zhizhiTokens } from './zhizhi-tokens';

/**
 * 存量 Growth 页面使用的既有 Mantine 主题（阶段一新旧并存，确保现有页面零破坏）。
 */
export const growthMantineTheme = createTheme({
  primaryColor: 'pine',
  colors: {
    pine: [
      '#eff8f5',
      '#deeee8',
      '#bcddd2',
      '#93cbb9',
      '#6bbb9f',
      '#49ae8e',
      '#087e70',
      '#09695f',
      '#0b574f',
      '#10483f',
    ],
  },
  fontFamily: 'Segoe UI, Growth Noto Sans SC, Microsoft YaHei, sans-serif',
  defaultRadius: 'md',
  components: {
    Badge: {
      defaultProps: {
        variant: 'light',
        component: 'span',
      },
    },
  },
});

const zhizhiGreenScale: MantineColorsTuple = [
  '#f2f8ec',
  zhizhiPrimitives.green100,
  zhizhiPrimitives.green200,
  zhizhiPrimitives.green300,
  zhizhiPrimitives.green400,
  zhizhiPrimitives.green500,
  '#63a355',
  zhizhiPrimitives.green700,
  zhizhiPrimitives.green800,
  '#1b462e',
];

const zhizhiNeutralScale: MantineColorsTuple = [
  zhizhiPrimitives.neutral25,
  zhizhiPrimitives.neutral50,
  zhizhiPrimitives.neutral100,
  zhizhiPrimitives.neutral200,
  zhizhiPrimitives.neutral300,
  zhizhiPrimitives.neutral500,
  zhizhiPrimitives.neutral600,
  zhizhiPrimitives.neutral700,
  zhizhiPrimitives.neutral850,
  zhizhiPrimitives.neutral900,
];

/**
 * 知枝绿色玻璃设计系统 Mantine 主题适配（源自 apps/learning-web/tokens/zhizhi.tokens.json）。
 * 供新版绿色玻璃页面与组件在阶段二统一切换或局部 MantineProvider 嵌套消费。
 */
export const zhizhiMantineTheme = createTheme({
  primaryColor: 'zhizhiGreen',
  primaryShade: 8,
  white: zhizhiTokens.bgColorContainer,
  black: zhizhiTokens.textColorPrimary,
  colors: {
    zhizhiGreen: zhizhiGreenScale,
    gray: zhizhiNeutralScale,
  },
  fontFamily: zhizhiTokens.fontUi,
  fontFamilyMonospace: zhizhiTokens.fontMono,
  defaultRadius: 'md',
  radius: {
    xs: zhizhiPrimitives.size2,
    sm: zhizhiTokens.radiusSm,
    md: zhizhiTokens.radiusCompose,
    lg: zhizhiTokens.radiusMd,
    xl: zhizhiTokens.radiusLg,
  },
  spacing: {
    xs: zhizhiTokens.spacingXs,
    sm: zhizhiTokens.spacingSm,
    md: zhizhiTokens.spacingMd,
    lg: zhizhiTokens.spacingLg,
    xl: zhizhiTokens.spacingXl,
  },
  components: {
    Badge: {
      defaultProps: {
        variant: 'light',
        component: 'span',
      },
    },
  },
});
