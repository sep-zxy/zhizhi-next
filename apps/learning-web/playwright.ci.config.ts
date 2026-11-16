import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig(base, {
  retries: 0,
  projects: base.projects?.filter(project => project.name === 'chromium-desktop'),
  use: { ...base.use, screenshot: 'off', trace: 'off', video: 'off' },
  reporter: [['list'], ['junit', { outputFile: '../../artifacts/browser-learning.xml' }]],
});
