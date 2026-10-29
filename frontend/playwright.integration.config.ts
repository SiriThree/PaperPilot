import { defineConfig } from '@playwright/test'

if (!process.env.PAPERPILOT_INTEGRATION_URL) throw new Error('Start the disposable integration preview and provide its URL')
export default defineConfig({
  testDir: './integration-tests', workers: 1, fullyParallel: false, timeout: 45000,
  outputDir: '../.runtime/browser-integration',
  use: { baseURL: process.env.PAPERPILOT_INTEGRATION_URL, headless: true, viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
})
