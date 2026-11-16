import { defineConfig } from '@playwright/test';
import base from './playwright.config';

// Spread the base options: defineConfig(base, override) merges named projects
// and would retain the historical Firefox/WebKit and viewport matrix.
export default defineConfig({
  ...base,
  retries: 0,
  projects: base.projects?.filter(project => project.name === 'chromium-desktop'),
  use: { ...base.use, screenshot: 'off', trace: 'off', video: 'off' },
  reporter: [['list'], ['junit', { outputFile: '../../artifacts/browser-learning.xml' }]],
});
