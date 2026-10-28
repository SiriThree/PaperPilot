import { defineConfig } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const python = path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const apiPort = Number(process.env.PAPERPILOT_E2E_DEMO_API_PORT || 8101)
const webPort = Number(process.env.PAPERPILOT_E2E_DEMO_WEB_PORT || 5274)
const storage = mkdtempSync(path.join(tmpdir(), 'paperpilot-demo-e2e-'))

export default defineConfig({
  testDir: './demo-tests', outputDir: '../output/demo-data/playwright',
  fullyParallel: false, workers: 1, timeout: 30000,
  use: { baseURL: `http://127.0.0.1:${webPort}`, headless: true, viewport: { width: 1440, height: 1000 } },
  webServer: [
    { command: `"${python}" -m uvicorn app.demo:create_demo_app --factory --app-dir backend --host 127.0.0.1 --port ${apiPort} --no-access-log`, cwd: '..', url: `http://127.0.0.1:${apiPort}/api/v1/health`, reuseExistingServer: false, env: { PAPERPILOT_ENV: 'test', PAPERPILOT_A_MODE: 'mock', PAPERPILOT_C_MODE: 'mock', PAPERPILOT_D_MODE: 'mock', PAPERPILOT_STORAGE_DIR: path.join(storage, 'uploads'), PAPERPILOT_ALLOWED_ORIGINS: `http://127.0.0.1:${webPort}` } },
    { command: `npm run dev -- --port ${webPort}`, url: `http://127.0.0.1:${webPort}`, reuseExistingServer: false, env: { PAPERPILOT_PROXY_TARGET: `http://127.0.0.1:${apiPort}` } },
  ],
})
