import { defineConfig } from '@playwright/test'

if (process.env.PAPERPILOT_PHASE6_BROWSER_ISOLATED !== '1') throw new Error('Phase 6 requires an isolated fixture')
const webPort = Number(process.env.PAPERPILOT_PHASE6_WEB_PORT)
const apiPort = Number(process.env.PAPERPILOT_PHASE6_API_PORT)
if (!webPort || !apiPort || !(process.env.PAPERPILOT_PHASE67_FIRST_PROJECT_ID || process.env.PAPERPILOT_SECURITY_DB_PROJECT_ID)) throw new Error('Missing isolated fixture')
export default defineConfig({
  testDir: './phase6-tests', outputDir: '../output/phase6/playwright', workers: 1, timeout: 90000,
  use: { baseURL: `http://127.0.0.1:${webPort}`, headless: true },
  webServer: {
    command: `npm run dev -- --port ${webPort}`,
    url: `http://127.0.0.1:${webPort}`, reuseExistingServer: false,
    env: { PAPERPILOT_PROXY_TARGET: `http://127.0.0.1:${apiPort}` },
  },
})
