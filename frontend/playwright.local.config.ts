import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './local-tests', workers: 1, fullyParallel: false, timeout: 45000,
  outputDir: '../.runtime/browser-local',
  // Only the owned loopback certificate; no credentials may enter traces or screenshots.
  use: { baseURL: 'https://localhost:8443', ignoreHTTPSErrors: true, headless: true, screenshot: 'off', trace: 'off', video: 'off' },
})
