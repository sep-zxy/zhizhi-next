import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig(base, {
  // This historical suite records design screenshots. Its non-visual companion
  // suites below exercise intake, review, access and text behavior in CI.
  testIgnore: ['**/wiki.spec.ts'],
  retries: 0,
  use: { ...base.use, screenshot: 'off', trace: 'off', video: 'off' },
  reporter: [['list'], ['junit', { outputFile: '../../artifacts/browser-private.xml' }]],
});
