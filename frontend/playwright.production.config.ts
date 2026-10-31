import { defineConfig } from '@playwright/test'

if (!process.env.PAPERPILOT_PRODUCTION_URL) throw new Error('Provide the actual HTTPS production deployment URL')
export default defineConfig({
  testDir: './production-tests', workers: 1, fullyParallel: false, timeout: 120000,
  outputDir: '../.runtime/production/browser-artifacts',
  use: { baseURL: process.env.PAPERPILOT_PRODUCTION_URL, headless: true, viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: false, trace: 'off', screenshot: 'only-on-failure' },
})
