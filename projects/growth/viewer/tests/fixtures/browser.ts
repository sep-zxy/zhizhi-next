import { existsSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { chromium } from '@playwright/test';

/** Opt-in system browser for isolated environments; bundled Chromium is default. */
export function launchTestChromium() {
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  if (executablePath && (!isAbsolute(executablePath) || !existsSync(executablePath))) {
    throw new Error('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH must name an existing absolute executable');
  }
  return chromium.launch(executablePath ? {executablePath} : {});
}
