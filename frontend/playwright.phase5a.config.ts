import { defineConfig } from '@playwright/test'

if (process.env.PAPERPILOT_PHASE5A_ISOLATED !== '1') throw new Error('Phase 5A requires a dedicated isolated database')
const webPort = Number(process.env.PAPERPILOT_PHASE5A_WEB_PORT || 5284)
const apiPort = Number(process.env.PAPERPILOT_PHASE5A_API_PORT || 8184)
export default defineConfig({
  testDir: './phase5a-tests', outputDir: '../output/phase5a/playwright', workers: 1, timeout: 60000,
  use: { baseURL: `http://127.0.0.1:${webPort}`, headless: true },
  webServer: {
    command: `npm run dev -- --port ${webPort}`,
    url: `http://127.0.0.1:${webPort}`, reuseExistingServer: false,
    env: { PAPERPILOT_PROXY_TARGET: `http://127.0.0.1:${apiPort}` },
  },
})
